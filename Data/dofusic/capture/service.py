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


class CaptureService:
    """Continuously capture Dofus on one dedicated thread.

    The Tk/controller thread only reads the newest immutable snapshot. Capture
    latency (MSS, visibility checks, PrintWindow fallback) therefore cannot
    block UI rendering or overlay movement.
    """

    def __init__(self, backend_factory: Callable[[], CaptureBackend], *, fps: int = 30) -> None:
        self._backend_factory = backend_factory
        self._period = 1.0 / max(1, int(fps))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: CaptureSnapshot | None = None
        self._sequence = 0

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.alive:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name='DofusicCapture', daemon=True)
        self._thread = thread
        thread.start()

    def _publish(self, frame: CapturedFrame | None, captured_at: float, error: str = '') -> None:
        with self._lock:
            self._sequence += 1
            self._latest = CaptureSnapshot(self._sequence, frame, float(captured_at), error)

    def _run(self) -> None:
        backend: CaptureBackend | None = None
        try:
            backend = self._backend_factory()
            while not self._stop.is_set():
                cycle_started = time.monotonic()
                try:
                    frame = backend.capture()
                    self._publish(frame, time.monotonic())
                except Exception as exc:
                    self._publish(None, time.monotonic(), f'{type(exc).__name__}: {exc}')
                remaining = self._period - (time.monotonic() - cycle_started)
                if remaining > 0:
                    self._stop.wait(remaining)
        except Exception as exc:
            self._publish(None, time.monotonic(), f'{type(exc).__name__}: {exc}')
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass

    def latest(self, after_sequence: int = 0, *, now: float | None = None) -> CaptureSnapshot | None:
        del now  # kept for interface parity with DirectCaptureService
        with self._lock:
            snapshot = self._latest
        if snapshot is None or snapshot.sequence <= int(after_sequence):
            return None
        return snapshot

    def close(self, *, timeout: float = 1.5) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
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
