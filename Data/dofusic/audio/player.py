from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from dofusic.audio.analysis import AudioAnalysisMixin
from dofusic.audio.decode import AudioDecodeMixin
from dofusic.audio.spectrum import SPECTRUM_BANDS


class MusicPlayer(AudioAnalysisMixin, AudioDecodeMixin):
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
        # A file may be replaced while retaining its name. Never reuse the
        # previous track's FFT or loudness when its file identity changes.
        self._audio_cache_stamps: dict[Path, tuple[int, int] | None] = {}
        self._loudness_cache_lock = threading.Lock()
        self._current_loudness_gain = 1.0
        self._analysis_thread: threading.Thread | None = None
        self._analysis_jobs: queue.Queue[tuple[Path, int] | None] = queue.Queue(maxsize=1)
        self._analysis_stop = threading.Event()
        self._spectrum_process_lock = threading.Lock()
        self._spectrum_process: subprocess.Popen | None = None
        self._loudness_generation = 0
        self._loudness_lock = threading.Lock()
        self._loudness_thread: threading.Thread | None = None
        self._loudness_stop = threading.Event()
        self._loudness_jobs: queue.Queue[tuple[Path, int] | None] = queue.Queue(maxsize=1)
        self._loudness_process: subprocess.Popen | None = None
        self._playback_fallback_path: Path | None = None
        # The mixer belongs to the UI thread. Transitions are advanced by tick(),
        # never by pygame.music.fadeout() (which blocks its caller).
        self._audio_owner_thread: int | None = None
        self._volume_dirty = False
        self._fade_factor = 1.0
        self._pending_transition: tuple[Path | None, bool, float, int] | None = None
        # One cancellable FFmpeg conversion worker, latest request wins.
        self._decode_stop = threading.Event()
        self._decode_jobs: queue.Queue[tuple[Path, bool, int] | None] = queue.Queue(maxsize=1)
        self._decode_results: queue.SimpleQueue[tuple[Path, bool, int, Path | None, str | None]] = queue.SimpleQueue()
        self._decode_thread: threading.Thread | None = None
        self._decode_process_lock = threading.Lock()
        self._decode_process: subprocess.Popen | None = None
        self._decode_generation = 0
        self._preparing: Path | None = None
        self._preparing_loop: bool | None = None
        self._closed = False


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
            self._audio_owner_thread = threading.get_ident()
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
        # FFT/loudness workers must never touch pygame's shared mixer.
        if self._audio_owner_thread is not None and threading.get_ident() != self._audio_owner_thread:
            self._volume_dirty = True
            return
        if self._ready and self._pygame:
            try:
                self._pygame.mixer.music.set_volume(self.effective_volume() * self._fade_factor)
                self._volume_dirty = False
            except Exception:
                pass

    def set_volume(self, value: int | float) -> int:
        self.volume = max(0, min(100, int(round(value))))
        self._apply_effective_volume()
        return self.volume

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self._apply_effective_volume()


    @property
    def transition_target(self) -> Path | None:
        """Next track in a fade; None for stop fades or no transition."""
        transition = self._pending_transition
        return transition[0] if transition is not None else None

    def is_preparing(self, target: Path | None = None) -> bool:
        return self._preparing is not None and (target is None or self._preparing == Path(target))


    def _start_playback(self, target: Path, *, loop: bool, prepared: Path | None = None) -> bool:
        """Mixer access only on the caller/UI thread; FFmpeg stays in worker."""
        assert self._pygame is not None
        channel = self._pygame.mixer.music
        try:
            # After the fade reaches zero, stop the old mixer stream before
            # attempting a new codec. A failed load must not revive old audio.
            if self.current is not None:
                stopping = getattr(channel, 'stop', None)
                if callable(stopping):
                    stopping()
            self._terminate_spectrum_process()
            with self._loudness_lock:
                self._loudness_generation += 1
            self._terminate_loudness_process()
            self._current_loudness_gain = (
                self._loudness_gain_by_path.get(target, 1.0) if self.normalize_loudness else 1.0
            )
            loaded_path = prepared or target
            try:
                channel.load(str(loaded_path))
            except Exception:
                if prepared is not None:
                    self._discard_fallback(prepared)
                    raise
                self.current = None
                self._prepare_fallback(target, loop)
                return True  # Request accepted, actual playback is pending.
            self._cleanup_playback_fallback(except_path=loaded_path)
            self._playback_fallback_path = loaded_path if loaded_path != target else None
            self._fade_factor = 1.0
            self._apply_effective_volume()
            channel.play(-1 if loop else 0, fade_ms=self.fade_ms)
            self.current = target
            self._looping = loop
            try:
                self._start_track_analysis(target)
            except Exception as exc:
                self._on_status(f'Analyse audio secondaire indisponible : {exc}')
            return True
        except Exception as exc:
            self.current = None
            self._fade_factor = 1.0
            with self._spectrum_lock:
                self._spectrum_generation += 1
                self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
                self._spectrum_duration_ms = 0.0
            self._on_status(f'Erreur lecture audio {target.name}: {exc}')
            return False

    def tick(self, *, now: float | None = None) -> None:
        """Nonblocking audio pump. Called from DofusicController.tick / Tk loop."""
        if self._closed:
            return
        now = time.monotonic() if now is None else float(now)
        while True:
            try:
                source, loop, generation, converted, error = self._decode_results.get_nowait()
            except queue.Empty:
                break
            if generation != self._decode_generation or self._preparing != source:
                if converted is not None:
                    self._discard_fallback(converted)
                continue
            self._preparing = None
            # Repeat can be toggled while FFmpeg is still converting a codec.
            # Use the latest requested mode without cancelling/restarting it.
            final_loop = loop if self._preparing_loop is None else self._preparing_loop
            self._preparing_loop = None
            if converted is None:
                self._on_status(f'Erreur préparation audio {source.name}: {error}')
            else:
                self._start_playback(source, loop=final_loop, prepared=converted)
        transition = self._pending_transition
        if transition is not None:
            target, loop, started, duration_ms = transition
            elapsed = max(0.0, (now - started) * 1000.0)
            if elapsed >= duration_ms:
                self._pending_transition = None
                self._fade_factor = 1.0
                if target is None:
                    if self._ready and self._pygame is not None:
                        self._pygame.mixer.music.stop()
                    self.current = None
                else:
                    self._start_playback(target, loop=loop)
                self._apply_effective_volume()
            else:
                self._fade_factor = max(0.0, 1.0 - elapsed / duration_ms)
                self._apply_effective_volume()
        elif self._volume_dirty:
            self._apply_effective_volume()

    def play(self, path: Path | str | None, *, loop: bool = True, restart: bool = False) -> bool:
        if path is None or self._closed:
            return False
        target = Path(path)
        loop = bool(loop)
        transition = self._pending_transition
        if transition is not None and transition[0] == target and transition[1] == loop and not restart:
            return True
        if self.is_preparing(target) and not restart:
            if self._preparing_loop != loop:
                self._preparing_loop = loop
            return True
        if (self.current == target and self._looping == loop and not restart
                and self.is_playing() and transition is None):
            return True
        self._invalidate_replaced_track(target)
        if not self.initialize():
            return False
        self._cancel_decode()
        self._audio_owner_thread = threading.get_ident()
        # Cancel stale fade if the desired track becomes the old one again.
        if transition is not None and self.current == target and self._looping == loop and not restart and self.is_playing():
            self._pending_transition = None
            self._fade_factor = 1.0
            self._apply_effective_volume()
            return True
        if self.current is not None and self.is_playing() and self.fade_ms > 0:
            # When several OCR decisions arrive during a fade, only the most
            # recent target matters. Do not restart the fading clock each time:
            # that can indefinitely postpone the next audible track while the
            # old one is already close to silence.
            started = transition[2] if transition is not None else time.monotonic()
            duration = transition[3] if transition is not None else self.fade_ms
            self._pending_transition = (target, loop, started, duration)
            return True
        self._pending_transition = None
        self._fade_factor = 1.0
        return self._start_playback(target, loop=loop)

    def stop(self, *, fade_ms: int = 0) -> None:
        """Cancel pending work, and optionally fade out without freezing Tk."""
        self._cancel_decode()
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
        self._terminate_spectrum_process()
        with self._loudness_lock:
            self._loudness_generation += 1
        self._terminate_loudness_process()
        self._discard_pending_loudness()
        fade = max(0, int(fade_ms))
        if fade and self._ready and self._pygame is not None and self.current is not None and self.is_playing():
            self._pending_transition = (None, False, time.monotonic(), fade)
            return
        self._pending_transition = None
        self.current = None
        self._current_loudness_gain = 1.0
        self._fade_factor = 1.0
        if self._ready and self._pygame is not None:
            try:
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
        self._closed = True
        self._pending_transition = None
        self._decode_stop.set()
        self._cancel_decode()
        self._queue_decode_job(None)
        thread = self._decode_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.25)
        # Even if a worker completes after this bound, it discards its output.
        while True:
            try:
                _source, _loop, _generation, converted, _error = self._decode_results.get_nowait()
            except queue.Empty:
                break
            if converted is not None:
                self._discard_fallback(converted)
        with self._spectrum_lock:
            self._spectrum_generation += 1
            self._spectrum_frames = np.zeros((0, SPECTRUM_BANDS), dtype=np.float32)
            self._spectrum_duration_ms = 0.0
        self._analysis_stop.set()
        self._terminate_spectrum_process()
        self._queue_latest_analysis(None)
        with self._loudness_lock:
            self._loudness_generation += 1
        self._loudness_stop.set()
        self._terminate_loudness_process()
        self._queue_latest_loudness(None)
        loudness_thread = self._loudness_thread
        if loudness_thread is not None and loudness_thread.is_alive():
            loudness_thread.join(timeout=0.25)
        self._loudness_thread = None
        thread = self._analysis_thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=0.20)
            except Exception:
                pass
        self._analysis_thread = None
        try:
            if self._ready and self._pygame:
                try:
                    self._pygame.mixer.music.stop()
                    self._pygame.mixer.quit()
                finally:
                    self._ready = False
        finally:
            self.current = None
            self._cleanup_playback_fallback()
