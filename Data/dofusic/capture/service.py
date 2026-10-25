from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from dofusic.capture.dofus_window import CapturedFrame


class CaptureBackend(Protocol):
    def capture(self) -> CapturedFrame | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CaptureSnapshot:
    sequence: int
    frame: CapturedFrame | None
    captured_at: float
    error: str = ''
    window_mode: str = 'unknown'


class CaptureService:
    """Continuously capture Dofus on one dedicated thread.

    The Tk/controller thread only reads the newest immutable snapshot. Capture
    latency (MSS, visibility checks, PrintWindow fallback) therefore cannot
    block UI rendering or overlay movement.
    """

    def __init__(self, backend_factory: Callable[[], CaptureBackend], *, fps: int = 30) -> None:
        self._backend_factory = backend_factory
        self._period = 1.0 / max(1, int(fps))
        self._unusable_capture_timeout_sec = 7.0
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._desired_running = False
        self._restart_pending = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: CaptureSnapshot | None = None
        self._sequence = 0

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _start_locked(self) -> None:
        """Start exactly one backend after the preceding capture thread exits."""
        self._stop = threading.Event()
        self._restart_pending = False
        thread = threading.Thread(target=self._run, name='DofusicCapture', daemon=True)
        self._thread = thread
        thread.start()

    def start(self) -> None:
        with self._lifecycle_lock:
            self._desired_running = True
            if self.alive:
                # A Win32 call can outlive close(timeout). Do not reopen the
                # backend concurrently; arrange a restart in _run's finally.
                if self._stop.is_set():
                    self._restart_pending = True
                return
            self._start_locked()

    def _publish(
        self, frame: CapturedFrame | None, captured_at: float, error: str = '',
        window_mode: str = 'unknown',
    ) -> None:
        with self._lock:
            self._sequence += 1
            self._latest = CaptureSnapshot(self._sequence, frame, float(captured_at), error, window_mode)

    def _run(self) -> None:
        backend: CaptureBackend | None = None
        unusable_since: float | None = None
        consecutive_errors = 0
        try:
            while not self._stop.is_set():
                if backend is None:
                    try:
                        backend = self._backend_factory()
                    except Exception as exc:
                        # A temporary MSS/Win32 initialization failure must not
                        # require closing and reopening Dofus to recover.
                        self._publish(None, time.monotonic(), f'{type(exc).__name__}: {exc}')
                        self._stop.wait(1.0)
                        continue

                cycle_started = time.monotonic()
                try:
                    frame = backend.capture()
                    mode = str(getattr(backend, 'window_mode', 'unknown'))
                    observed_at = time.monotonic()
                    self._publish(frame, observed_at, window_mode=mode)
                    consecutive_errors = 0
                    # PrintWindow can keep OCR running while permanently
                    # hiding the on-screen outline. A prolonged non-visible
                    # capture is just as unhealthy as a missing frame. An
                    # inconclusive (unknown) Win32 display mode is likewise
                    # not proof the user switched to windowed mode.
                    unusable = (
                        (mode == 'fullscreen' and (frame is None or frame.source != 'visible'))
                        or (mode == 'unknown' and frame is None)
                    )
                    if unusable:
                        if unusable_since is None:
                            unusable_since = observed_at
                    else:
                        unusable_since = None
                except Exception as exc:
                    consecutive_errors += 1
                    self._publish(None, time.monotonic(), f'{type(exc).__name__}: {exc}')
                # Recover from a persistent PrintWindow-only, empty or unknown
                # capture. Never recreate the backend for a genuine windowed
                # reading; mode transitions remain controlled by the app.
                exhausted = consecutive_errors >= 10 or (
                    unusable_since is not None
                    and time.monotonic() - unusable_since >= self._unusable_capture_timeout_sec
                )
                if exhausted and not self._stop.is_set():
                    try:
                        backend.close()
                    except Exception:
                        pass
                    backend = None
                    consecutive_errors = 0
                    unusable_since = None
                    self._stop.wait(0.35)
                remaining = self._period - (time.monotonic() - cycle_started)
                if remaining > 0:
                    self._stop.wait(remaining)
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass
            with self._lifecycle_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    if self._desired_running and self._restart_pending:
                        self._start_locked()

    def latest(self, after_sequence: int = 0, *, now: float | None = None) -> CaptureSnapshot | None:
        del now  # kept for interface parity with DirectCaptureService
        with self._lock:
            snapshot = self._latest
        if snapshot is None or snapshot.sequence <= int(after_sequence):
            return None
        return snapshot

    def close(self, *, timeout: float = 1.5) -> None:
        with self._lifecycle_lock:
            self._desired_running = False
            self._restart_pending = False
            self._stop.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        # If it is still alive, start() will defer reopening until _run exits.
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None


class DirectCaptureService:
    """Synchronous adapter reserved for deterministic dependency-injected tests."""

    def __init__(self, backend: CaptureBackend) -> None:
        self.backend = backend
        self._sequence = 0

    @property
    def alive(self) -> bool:
        return True

    def start(self) -> None:
        return None

    def latest(self, after_sequence: int = 0, *, now: float | None = None) -> CaptureSnapshot | None:
        del after_sequence
        self._sequence += 1
        captured_at = time.monotonic() if now is None else float(now)
        try:
            frame = self.backend.capture()
            return CaptureSnapshot(self._sequence, frame, captured_at)
        except Exception as exc:
            return CaptureSnapshot(self._sequence, None, captured_at, f'{type(exc).__name__}: {exc}')

    def close(self, *, timeout: float = 0.0) -> None:
        del timeout
        try:
            self.backend.close()
        except Exception:
            pass
