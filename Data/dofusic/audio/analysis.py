"""Cancellable FFT/loudness workers and caches for MusicPlayer.

The mixin preserves the historical player API; it intentionally owns no
mixer thread, and worker generations continue to guard stale results.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
from pathlib import Path

import numpy as np

from dofusic.audio.cache_policy import (
    _LOUDNESS_CACHE_LIMIT,
    _SPECTRUM_CACHE_LIMIT_BYTES,
    _SPECTRUM_CACHE_LIMIT_ITEMS,
)
from dofusic.audio.spectrum import (
    SPECTRUM_BANDS,
    SPECTRUM_FPS,
    SPECTRUM_SAMPLE_RATE,
    compute_spectrum_chunks,
    loudness_gain_from_dbfs,
)


class AudioAnalysisMixin:
    """Analysis workers; playback/mixer operations remain in MusicPlayer."""

    @staticmethod
    def _audio_file_stamp(path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except OSError:
            return None
        return info.st_size, info.st_mtime_ns

    def _invalidate_replaced_track(self, path: Path) -> None:
        # Usually a constant-time no-op; stat only paths with reusable analysis.
        with self._loudness_cache_lock:
            old = self._audio_cache_stamps.get(path)
            tracked = path in self._audio_cache_stamps
        if not tracked or old == self._audio_file_stamp(path):
            return
        with self._spectrum_lock:
            self._spectrum_cache.pop(path, None)
        with self._loudness_cache_lock:
            self._loudness_gain_by_path.pop(path, None)
            self._audio_cache_stamps.pop(path, None)

    def _remember_loudness_gain(self, path: Path, gain: float) -> None:
        """Keep normalization reuse bounded during long local-audio sessions."""
        with self._loudness_cache_lock:
            self._loudness_gain_by_path.pop(path, None)
            self._loudness_gain_by_path[path] = float(gain)
            self._audio_cache_stamps.pop(path, None)
            self._audio_cache_stamps[path] = self._audio_file_stamp(path)
            while len(self._loudness_gain_by_path) > _LOUDNESS_CACHE_LIMIT:
                dropped = next(iter(self._loudness_gain_by_path))
                self._loudness_gain_by_path.pop(dropped)
                self._audio_cache_stamps.pop(dropped, None)

    def set_spectrum_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.spectrum_enabled:
            return
        self.spectrum_enabled = enabled
        if enabled:
            with self._loudness_lock:
                self._loudness_generation += 1
            self._terminate_loudness_process()
        else:
            self._terminate_spectrum_process()
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
            if not enabled:
                self._spectrum_cache.clear()
        # Normalization stays active even when the visualizer is disabled.
        if self.current is not None:
            self._start_track_analysis(self.current)

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
            self._remember_loudness_gain(path, measured_gain)
            self._current_loudness_gain = measured_gain if self.normalize_loudness else 1.0
            if self.spectrum_enabled:
                self._spectrum_frames = np.asarray(frames, dtype=np.float32)
                self._spectrum_duration_ms = float(duration_ms)
                # Number of tracks alone is not a memory bound: one long
                # track can hold millions of FFT values. Keep a byte budget.
                self._spectrum_cache.pop(path, None)
                if self._spectrum_frames.nbytes <= _SPECTRUM_CACHE_LIMIT_BYTES:
                    self._spectrum_cache[path] = (self._spectrum_frames, self._spectrum_duration_ms)
                while (
                    len(self._spectrum_cache) > _SPECTRUM_CACHE_LIMIT_ITEMS
                    or sum(value[0].nbytes for value in self._spectrum_cache.values()) > _SPECTRUM_CACHE_LIMIT_BYTES
                ):
                    self._spectrum_cache.pop(next(iter(self._spectrum_cache)))
            else:
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
        self._apply_effective_volume()

    def _terminate_spectrum_process(self) -> None:
        """Interrupt a decoder that may still be blocked on its stdout pipe."""
        with self._spectrum_process_lock:
            process = self._spectrum_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

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
            with self._spectrum_process_lock:
                self._spectrum_process = process
            if self._analysis_stop.is_set():
                self._terminate_spectrum_process()
                return
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
                        samples = np.frombuffer(data[:usable], dtype='<f4')
                        valid = np.isfinite(samples)
                        finite = samples if bool(valid.all()) else samples[valid]
                        if finite.size:
                            finite_64 = finite.astype(np.float64)
                            energy_sum += float(np.dot(finite_64, finite_64))
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
            # Discard stale work when a track change interrupts FFmpeg.
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
            with self._spectrum_process_lock:
                if self._spectrum_process is process:
                    self._spectrum_process = None

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
            with self._spectrum_lock:
                if generation != self._spectrum_generation or self.current != path:
                    continue
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
        self._invalidate_replaced_track(path)
        # A cached new track must also interrupt the previous decoder.
        self._terminate_spectrum_process()
        with self._spectrum_lock:
            self._spectrum_generation += 1
            generation = self._spectrum_generation
            measured_gain = self._loudness_gain_by_path.get(path, 1.0)
            self._current_loudness_gain = measured_gain if self.normalize_loudness else 1.0
            cached = self._spectrum_cache.get(path)
            has_loudness = path in self._loudness_gain_by_path
            if cached is not None:
                self._spectrum_frames, self._spectrum_duration_ms = cached
                if has_loudness:
                    self._apply_effective_volume()
                    return
            else:
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
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
            # Invalid local files and stalled decoders must not leave a helper
            # process running indefinitely. Analysis is optional for playback.
            _stdout, stderr = process.communicate(timeout=30.0)
            text = (stderr or b'').decode('utf-8', errors='replace')
            match = re.search(r'mean_volume:\s*([-+]?\d+(?:\.\d+)?)\s*dB', text, re.IGNORECASE)
            gain = loudness_gain_from_dbfs(float(match.group(1))) if match else 1.0
            with self._loudness_lock:
                if generation != self._loudness_generation or self.current != path:
                    return
                self._remember_loudness_gain(path, gain)
                self._current_loudness_gain = gain if self.normalize_loudness else 1.0
            self._apply_effective_volume()
        except Exception:
            return
        finally:
            if process is not None:
                try:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                        process.wait(timeout=0.5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            with self._loudness_lock:
                if self._loudness_process is process:
                    self._loudness_process = None

    def _loudness_worker_loop(self) -> None:
        """Only one FFmpeg loudness analysis runs at a time (latest wins)."""
        while not self._loudness_stop.is_set():
            try:
                job = self._loudness_jobs.get(timeout=0.25)
            except queue.Empty:
                continue
            if job is None:
                return
            path, generation = job
            with self._loudness_lock:
                if generation != self._loudness_generation:
                    continue
            self._loudness_worker(path, generation)

    def _queue_latest_loudness(self, job: tuple[Path, int] | None) -> None:
        try:
            self._loudness_jobs.put_nowait(job)
        except queue.Full:
            try:
                self._loudness_jobs.get_nowait()
            except queue.Empty:
                pass
            self._loudness_jobs.put_nowait(job)

    def _discard_pending_loudness(self) -> None:
        while True:
            try:
                self._loudness_jobs.get_nowait()
            except queue.Empty:
                break

    def _start_loudness_analysis(self, path: Path) -> None:
        if self._closed:
            return
        self._invalidate_replaced_track(path)
        # Cancel the previous work even when the incoming track is cached.
        with self._loudness_lock:
            self._loudness_generation += 1
            generation = self._loudness_generation
        self._terminate_loudness_process()
        self._discard_pending_loudness()
        with self._loudness_cache_lock:
            cached = self._loudness_gain_by_path.get(path)
        if cached is not None:
            self._current_loudness_gain = cached if self.normalize_loudness else 1.0
            self._apply_effective_volume()
            return
        self._queue_latest_loudness((path, generation))
        with self._loudness_lock:
            thread = self._loudness_thread
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._loudness_worker_loop,
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
            with self._loudness_lock:
                self._loudness_generation += 1
            self._terminate_loudness_process()
            self._current_loudness_gain = 1.0
            self._apply_effective_volume()
            return
        self._invalidate_replaced_track(self.current)
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
