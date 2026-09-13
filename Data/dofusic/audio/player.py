from __future__ import annotations

import os
import queue
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

import numpy as np


SPECTRUM_BANDS = 24
SPECTRUM_FPS = 15
SPECTRUM_SAMPLE_RATE = 16000
SPECTRUM_MIN_HZ = 60.0
SPECTRUM_MAX_HZ = 16000.0


LOUDNESS_TARGET_DBFS = -16.0
LOUDNESS_MIN_GAIN = 0.25
LOUDNESS_MAX_GAIN = 4.0


def loudness_gain_from_dbfs(
    dbfs: float | None,
    *,
    target_dbfs: float = LOUDNESS_TARGET_DBFS,
    min_gain: float = LOUDNESS_MIN_GAIN,
    max_gain: float = LOUDNESS_MAX_GAIN,
) -> float:
    """Return a bounded linear gain bringing a track near one common loudness.

    The estimate is computed from the same mono PCM stream already decoded for
    the visualizer, so normalization does not launch a second decoder.
    """
    if dbfs is None:
        return 1.0
    try:
        value = float(dbfs)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(value):
        return 1.0
    lower = max(0.01, float(min_gain))
    upper = max(lower, float(max_gain))
    gain = 10.0 ** ((float(target_dbfs) - value) / 20.0)
    return float(max(lower, min(upper, gain)))


def _spectrum_layout(sample_rate: int, bands: int, fps: int):
    bands = max(1, int(bands))
    fps = max(1, int(fps))
    sample_rate = max(1, int(sample_rate))
    frame_size = 2048 if sample_rate >= 22050 else 1024
    hop = max(1, int(round(sample_rate / float(fps))))
    window = np.hanning(frame_size).astype(np.float32)
    frequencies = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    high_hz = min(SPECTRUM_MAX_HZ, sample_rate * 0.49)
    low_hz = min(SPECTRUM_MIN_HZ, max(10.0, high_hz * 0.5))
    if high_hz <= low_hz:
        high_hz = max(low_hz + 1.0, sample_rate * 0.49)
    edges = np.geomspace(low_hz, high_hz, bands + 1)
    band_slices: list[tuple[int, int]] = []
    for index in range(bands):
        left = int(np.searchsorted(frequencies, edges[index], side='left'))
        right = int(np.searchsorted(frequencies, edges[index + 1], side='right'))
        left = max(1, min(left, len(frequencies) - 1))
        right = max(left + 1, min(right, len(frequencies)))
        band_slices.append((left, right))
    return frame_size, hop, window, band_slices


def _spectrum_row(frame: np.ndarray, window: np.ndarray, band_slices: list[tuple[int, int]]) -> np.ndarray:
    magnitude = np.abs(np.fft.rfft(frame * window)).astype(np.float32, copy=False)
    row = np.zeros(len(band_slices), dtype=np.float32)
    for band_index, (left, right) in enumerate(band_slices):
        values = magnitude[left:right]
        if values.size:
            row[band_index] = float(np.sqrt(np.mean(values * values)))
    return row


def _normalize_spectrum(raw: np.ndarray) -> np.ndarray:
    reference = float(np.percentile(raw, 98.5)) if raw.size else 0.0
    if reference <= 1e-9:
        return np.zeros_like(raw, dtype=np.float32)
    normalized = np.clip(raw / reference, 0.0, 1.25)
    normalized = np.log1p(normalized * 6.0) / np.log(7.0)
    return np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)


def compute_spectrum_chunks(
    chunks,
    sample_rate: int,
    *,
    bands: int = SPECTRUM_BANDS,
    fps: int = SPECTRUM_FPS,
) -> tuple[np.ndarray, int]:
    """Compute a spectrum timeline while keeping only a small rolling PCM buffer.

    ``chunks`` may yield mono or multi-channel numpy arrays. Only compact spectrum
    rows are retained; the decoded track itself is never accumulated in RAM.
    """
    bands = max(1, int(bands))
    sample_rate = max(1, int(sample_rate))
    frame_size, hop, window, band_slices = _spectrum_layout(sample_rate, bands, fps)
    pending = np.empty(0, dtype=np.float32)
    rows: list[np.ndarray] = []
    total_samples = 0

    for chunk in chunks:
        work = np.asarray(chunk)
        if work.size == 0:
            continue
        if work.ndim > 1:
            work = np.mean(work.astype(np.float32, copy=False), axis=1)
        else:
            work = work.astype(np.float32, copy=False).reshape(-1)
        peak = float(np.max(np.abs(work))) if work.size else 0.0
        if peak > 1.5:
            work = work / peak
        total_samples += int(work.size)
        if pending.size:
            pending = np.concatenate((pending, work))
        else:
            pending = np.ascontiguousarray(work)

        while pending.size >= frame_size:
            rows.append(_spectrum_row(pending[:frame_size], window, band_slices))
            pending = pending[hop:]

    if pending.size or not rows:
        frame = np.zeros(frame_size, dtype=np.float32)
        take = min(frame_size, pending.size)
        if take:
            frame[:take] = pending[:take]
        rows.append(_spectrum_row(frame, window, band_slices))

    raw = np.vstack(rows).astype(np.float32, copy=False)
    return _normalize_spectrum(raw), total_samples


def compute_spectrum_frames(
    samples: np.ndarray,
    sample_rate: int,
    *,
    bands: int = SPECTRUM_BANDS,
    fps: int = SPECTRUM_FPS,
) -> np.ndarray:
    """Compatibility wrapper for tests/callers that already hold decoded PCM."""
    frames, _sample_count = compute_spectrum_chunks((samples,), sample_rate, bands=bands, fps=fps)
    return frames


class MusicPlayer:
    """Petit wrapper pygame. L'import est lazy pour garder les tests/headless propres."""

    def __init__(
        self,
        *,
        volume: int = 70,
        fade_ms: int = 650,
        on_status: Callable[[str], None] | None = None,
        spectrum_enabled: bool = True,
        normalize_loudness: bool = False,
    ) -> None:
        self.volume = max(0, min(100, int(volume)))
        self.fade_ms = max(0, int(fade_ms))
        self.muted = False
        self.current: Path | None = None
        self._looping = True
        self._pygame = None
        self._ready = False
        self._on_status = on_status or (lambda _msg: None)
        self.spectrum_enabled = bool(spectrum_enabled)
        self.normalize_loudness = bool(normalize_loudness)

        self._spectrum_lock = threading.Lock()
        self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
        self._spectrum_duration_ms = 0.0
        self._spectrum_generation = 0
        self._spectrum_cache: dict[Path, tuple[np.ndarray, float]] = {}
        self._loudness_gain_by_path: dict[Path, float] = {}
        self._current_loudness_gain = 1.0
        self._analysis_thread: threading.Thread | None = None
        self._analysis_jobs: queue.Queue[tuple[Path, int] | None] = queue.Queue(maxsize=1)
        self._analysis_stop = threading.Event()
        self._loudness_generation = 0
        self._loudness_lock = threading.Lock()
        self._loudness_thread: threading.Thread | None = None
        self._loudness_process: subprocess.Popen | None = None
        self._playback_fallback_path: Path | None = None

    def initialize(self) -> bool:
        if self._ready:
            return True
        try:
            import pygame

            pygame.mixer.pre_init(44100, -16, 2, 512)
            pygame.mixer.init()
            pygame.mixer.music.set_volume(self.effective_volume())
            self._pygame = pygame
            self._ready = True
            return True
        except Exception as exc:  # pragma: no cover - dépend audio OS
            self._on_status(f"Erreur audio: {exc}")
            self._ready = False
            return False

    def effective_volume(self) -> float:
        if self.muted:
            return 0.0
        master = self.volume / 100.0
        return max(0.0, min(1.0, master * float(self._current_loudness_gain)))

    def _apply_effective_volume(self) -> None:
        if self._ready and self._pygame:
            try:
                self._pygame.mixer.music.set_volume(self.effective_volume())
            except Exception:
                pass

    def set_volume(self, value: int | float) -> int:
        self.volume = max(0, min(100, int(round(value))))
        self._apply_effective_volume()
        return self.volume

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self._apply_effective_volume()

    def set_spectrum_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.spectrum_enabled:
            return
        self.spectrum_enabled = enabled
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
            if not enabled:
                self._spectrum_cache.clear()
        # Normalization stays active even when the visualizer is disabled.
        if self.current is not None:
            self._start_track_analysis(self.current)

    # ------------------------------------------------------------------
    # Production FFT spectrum
    def _publish_analysis(
        self,
        path: Path,
        generation: int,
        frames: np.ndarray,
        duration_ms: float,
        loudness_gain: float,
    ) -> None:
        with self._spectrum_lock:
            if generation != self._spectrum_generation or self.current != path:
                return
            measured_gain = float(loudness_gain)
            self._loudness_gain_by_path[path] = measured_gain
            self._current_loudness_gain = measured_gain if self.normalize_loudness else 1.0
            if self.spectrum_enabled:
                self._spectrum_frames = np.asarray(frames, dtype=np.float32)
                self._spectrum_duration_ms = float(duration_ms)
                self._spectrum_cache[path] = (self._spectrum_frames, self._spectrum_duration_ms)
                # Bound memory while still making common zone revisits instant.
                while len(self._spectrum_cache) > 8:
                    oldest = next(iter(self._spectrum_cache))
                    if oldest == path and len(self._spectrum_cache) > 1:
                        oldest = next(key for key in self._spectrum_cache if key != path)
                    self._spectrum_cache.pop(oldest, None)
            else:
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
        self._apply_effective_volume()

    def _analyze_spectrum_worker(self, path: Path, generation: int) -> None:
        if not self._ready or self._pygame is None:
            return
        process = None
        sample_rate = SPECTRUM_SAMPLE_RATE
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            command = [
                str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-i', str(path), '-vn', '-ac', '1', '-ar', str(sample_rate),
                '-f', 'f32le', '-acodec', 'pcm_f32le', 'pipe:1',
            ]
            kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.DEVNULL,
                'stdin': subprocess.DEVNULL,
                'bufsize': 256 * 1024,
            }
            if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                flags = subprocess.CREATE_NO_WINDOW
                flags |= getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
                kwargs['creationflags'] = flags
            process = subprocess.Popen(command, **kwargs)
            assert process.stdout is not None

            energy_sum = 0.0
            loudness_samples = 0

            def pcm_chunks():
                nonlocal energy_sum, loudness_samples
                remainder = b''
                while not self._analysis_stop.is_set():
                    with self._spectrum_lock:
                        if generation != self._spectrum_generation or self.current != path:
                            return
                    block = process.stdout.read(64 * 1024)
                    if not block:
                        break
                    data = remainder + block
                    usable = len(data) - (len(data) % 4)
                    if usable:
                        samples = np.frombuffer(data[:usable], dtype='<f4').copy()
                        finite = samples[np.isfinite(samples)]
                        if finite.size:
                            energy_sum += float(np.dot(finite.astype(np.float64), finite.astype(np.float64)))
                            loudness_samples += int(finite.size)
                        yield samples
                    remainder = data[usable:]

            chunks = pcm_chunks()
            if self.spectrum_enabled:
                frames, sample_count = compute_spectrum_chunks(
                    chunks, sample_rate, bands=SPECTRUM_BANDS, fps=SPECTRUM_FPS
                )
            else:
                sample_count = 0
                for samples in chunks:
                    sample_count += int(np.asarray(samples).size)
                frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)

            return_code = process.wait(timeout=2.0)
            if return_code != 0 or sample_count <= 0:
                raise RuntimeError(f'FFmpeg audio analysis failed ({return_code})')
            duration_ms = max(1.0, (sample_count / float(sample_rate)) * 1000.0)
            if loudness_samples > 0 and energy_sum > 0.0:
                rms = float(np.sqrt(energy_sum / float(loudness_samples)))
                dbfs = 20.0 * float(np.log10(max(rms, 1e-9)))
                gain = loudness_gain_from_dbfs(dbfs)
            else:
                gain = 1.0
            self._publish_analysis(path, generation, frames, duration_ms, gain)
        except Exception:
            self._publish_analysis(
                path, generation, np.zeros((0, SPECTRUM_BANDS), dtype=np.float32), 0.0, 1.0
            )
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=0.5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    def _queue_latest_analysis(self, job: tuple[Path, int] | None) -> None:
        try:
            self._analysis_jobs.put_nowait(job)
            return
        except queue.Full:
            pass
        try:
            self._analysis_jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            self._analysis_jobs.put_nowait(job)
        except queue.Full:
            pass

    def _spectrum_worker_loop(self) -> None:
        while not self._analysis_stop.is_set():
            try:
                job = self._analysis_jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            if job is None:
                return
            path, generation = job
            self._analyze_spectrum_worker(path, generation)

    def _ensure_spectrum_worker(self) -> None:
        thread = self._analysis_thread
        if thread is not None and thread.is_alive():
            return
        self._analysis_stop.clear()
        thread = threading.Thread(
            target=self._spectrum_worker_loop,
            name='DofusicSpectrum',
            daemon=True,
        )
        self._analysis_thread = thread
        thread.start()

    def _start_spectrum_analysis(self, path: Path) -> None:
        if not self.spectrum_enabled:
            return
        with self._spectrum_lock:
            self._spectrum_generation += 1
            generation = self._spectrum_generation
            measured_gain = self._loudness_gain_by_path.get(path, 1.0)
            self._current_loudness_gain = measured_gain if self.normalize_loudness else 1.0
            cached = self._spectrum_cache.get(path) if self.spectrum_enabled else None
            has_loudness = path in self._loudness_gain_by_path
            if cached is not None:
                self._spectrum_frames, self._spectrum_duration_ms = cached
                if has_loudness:
                    self._apply_effective_volume()
                    return
            else:
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
                if not self.spectrum_enabled and has_loudness:
                    self._apply_effective_volume()
                    return
        self._apply_effective_volume()
        self._ensure_spectrum_worker()
        self._queue_latest_analysis((path, generation))

    def _terminate_loudness_process(self) -> None:
        with self._loudness_lock:
            process = self._loudness_process
            self._loudness_process = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def _loudness_worker(self, path: Path, generation: int) -> None:
        process = None
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            command = [
                str(ffmpeg), '-hide_banner', '-nostdin', '-i', str(path), '-vn',
                '-af', 'volumedetect', '-f', 'null', '-',
            ]
            kwargs = {
                'stdout': subprocess.DEVNULL,
                'stderr': subprocess.PIPE,
                'stdin': subprocess.DEVNULL,
            }
            if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                flags = subprocess.CREATE_NO_WINDOW
                flags |= getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
                kwargs['creationflags'] = flags
            process = subprocess.Popen(command, **kwargs)
            with self._loudness_lock:
                if generation != self._loudness_generation:
                    process.terminate()
                    return
                self._loudness_process = process
            _stdout, stderr = process.communicate()
            text = (stderr or b'').decode('utf-8', errors='replace')
            match = re.search(r'mean_volume:\s*([-+]?\d+(?:\.\d+)?)\s*dB', text, re.IGNORECASE)
            gain = loudness_gain_from_dbfs(float(match.group(1))) if match else 1.0
            with self._loudness_lock:
                if generation != self._loudness_generation or self.current != path:
                    return
                self._loudness_gain_by_path[path] = gain
                self._current_loudness_gain = gain if self.normalize_loudness else 1.0
            self._apply_effective_volume()
        except Exception:
            return
        finally:
            with self._loudness_lock:
                if self._loudness_process is process:
                    self._loudness_process = None

    def _start_loudness_analysis(self, path: Path) -> None:
        cached = self._loudness_gain_by_path.get(path)
        if cached is not None:
            self._current_loudness_gain = cached if self.normalize_loudness else 1.0
            self._apply_effective_volume()
            return
        self._terminate_loudness_process()
        with self._loudness_lock:
            self._loudness_generation += 1
            generation = self._loudness_generation
        thread = threading.Thread(
            target=self._loudness_worker,
            args=(path, generation),
            name='DofusicLoudness',
            daemon=True,
        )
        self._loudness_thread = thread
        thread.start()

    def _start_track_analysis(self, path: Path) -> None:
        if self.spectrum_enabled:
            self._start_spectrum_analysis(path)
        elif self.normalize_loudness:
            self._start_loudness_analysis(path)
        else:
            self._current_loudness_gain = 1.0
            self._apply_effective_volume()

    def set_normalize_loudness(self, enabled: bool) -> None:
        self.normalize_loudness = bool(enabled)
        if self.current is None:
            self._current_loudness_gain = 1.0
            self._apply_effective_volume()
            return
        if not self.normalize_loudness:
            self._terminate_loudness_process()
            self._current_loudness_gain = 1.0
            self._apply_effective_volume()
            return
        cached = self._loudness_gain_by_path.get(self.current)
        if cached is not None:
            self._current_loudness_gain = cached
            self._apply_effective_volume()
            return
        self._start_track_analysis(self.current)

    def spectrum_levels(self, count: int = SPECTRUM_BANDS) -> tuple[float, ...]:
        """Return the frequency spectrum at the current playback position."""
        count = max(1, int(count))
        if not self.spectrum_enabled or self.muted or not self._ready or self._pygame is None:
            return tuple(0.0 for _ in range(count))
        with self._spectrum_lock:
            frames = self._spectrum_frames
            duration_ms = self._spectrum_duration_ms
        if frames.size == 0 or duration_ms <= 0.0:
            return tuple(0.0 for _ in range(count))
        try:
            position_ms = int(self._pygame.mixer.music.get_pos())
        except Exception:
            position_ms = -1
        if position_ms < 0:
            return tuple(0.0 for _ in range(count))
        frame_index = int(((float(position_ms) % duration_ms) / duration_ms) * len(frames))
        frame_index = max(0, min(len(frames) - 1, frame_index))
        values = np.asarray(frames[frame_index], dtype=np.float32)
        if values.size != count:
            old_x = np.linspace(0.0, 1.0, values.size, dtype=np.float32)
            new_x = np.linspace(0.0, 1.0, count, dtype=np.float32)
            values = np.interp(new_x, old_x, values).astype(np.float32)
        return tuple(float(v) for v in np.clip(values, 0.0, 1.0))

    def _cleanup_playback_fallback(self, *, except_path: Path | None = None) -> None:
        old = self._playback_fallback_path
        if old is None or (except_path is not None and old == except_path):
            return
        self._playback_fallback_path = None
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass

    def _transcode_playback_fallback(self, source: Path) -> Path:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise RuntimeError('FFmpeg Dofusic indisponible') from exc

        folder = Path(tempfile.gettempdir()) / 'DofusicAudioFallback'
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='dofusic_', suffix='.ogg', dir=folder)
        os.close(fd)
        target = Path(name)
        target.unlink(missing_ok=True)
        command = [
            str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(source), '-vn', '-map_metadata', '-1',
            '-c:a', 'libvorbis', '-q:a', '4', str(target),
        ]
        kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.PIPE, 'text': True, 'check': False}
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(command, **kwargs)
        if result.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
            target.unlink(missing_ok=True)
            lines = (result.stderr or '').strip().splitlines()
            detail = lines[-1] if lines else f'code {result.returncode}'
            raise RuntimeError(f'conversion FFmpeg impossible: {detail}')
        return target

    def play(self, path: Path | str | None, *, loop: bool = True, restart: bool = False) -> bool:
        if path is None:
            return False
        target = Path(path)
        loop = bool(loop)
        if self.current == target and self._looping == loop and not restart:
            return True
        if not self.initialize():
            return False
        assert self._pygame is not None
        try:
            if self.current:
                self._pygame.mixer.music.fadeout(self.fade_ms)
            self._current_loudness_gain = self._loudness_gain_by_path.get(target, 1.0)
            loaded_path = target
            try:
                self._pygame.mixer.music.load(str(target))
            except Exception:
                loaded_path = self._transcode_playback_fallback(target)
                self._pygame.mixer.music.load(str(loaded_path))
            self._cleanup_playback_fallback(except_path=loaded_path)
            self._playback_fallback_path = loaded_path if loaded_path != target else None
            self._pygame.mixer.music.set_volume(self.effective_volume())
            self._pygame.mixer.music.play(-1 if loop else 0, fade_ms=self.fade_ms)
            self.current = target
            self._looping = loop
            self._start_track_analysis(target)
            return True
        except Exception as exc:  # pragma: no cover - dépend codecs/audio OS
            self.current = None
            self._current_loudness_gain = 1.0
            with self._spectrum_lock:
                self._spectrum_generation += 1
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
            self._on_status(f"Erreur lecture audio {target.name}: {exc}")
            return False

    def stop(self, *, fade_ms: int = 0) -> None:
        """Stop the current track without tearing down the pygame mixer."""
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
        self.current = None
        self._current_loudness_gain = 1.0
        if not self._ready or self._pygame is None:
            return
        try:
            fade = max(0, int(fade_ms))
            if fade:
                self._pygame.mixer.music.fadeout(fade)
            else:
                self._pygame.mixer.music.stop()
        except Exception:
            pass

    def is_playing(self) -> bool:
        if not self._ready or self._pygame is None:
            return False
        try:
            return bool(self._pygame.mixer.music.get_busy())
        except Exception:
            return False

    def close(self) -> None:
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
        self._analysis_stop.set()
        self._queue_latest_analysis(None)
        with self._loudness_lock:
            self._loudness_generation += 1
        self._terminate_loudness_process()
        thread = self._analysis_thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=0.20)
            except Exception:
                pass
        self._analysis_thread = None
        if not self._ready or not self._pygame:
            return
        try:
            self._pygame.mixer.music.stop()
            self._pygame.mixer.quit()
        finally:
            self._ready = False
            self.current = None
            self._cleanup_playback_fallback()

