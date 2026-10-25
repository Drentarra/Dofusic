"""Serialized OCR process lifecycle, outside the Tkinter event loop.

Only this background thread may start/restart/close OCR child processes.
It never touches Tkinter, pygame, or ControllerState.  The UI thread keeps
polling OCR results; startup/teardown actions cannot block a UI callback.
"""
from __future__ import annotations

import queue
import threading


class OCRRuntimeSupervisor:
    def __init__(self, workers: dict[str, object]):
        self._workers = workers
        self._commands: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._errors: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._desired_running = False
        self._closing = False
        self._pending_restarts: set[str] = set()
        self._pending_start = False
        self._pending_stop = False
        self._pending_channels: set[str] = set()
        self._phase = 'idle'

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def _ensure_thread_locked(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='DofusicOCRLifecycle', daemon=True)
            self._thread.start()

    def start(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._desired_running = True
            if self._pending_start:
                return
            self._pending_start = True
            self._ensure_thread_locked()
            self._commands.put(('start', None))

    def stop(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._desired_running = False
            if self._pending_stop:
                return
            self._pending_stop = True
            self._commands.put(('stop', None))

    def restart(self, channel: str) -> None:
        if channel not in self._workers:
            raise ValueError(channel)
        with self._lock:
            if self._closing or not self._desired_running or channel in self._pending_restarts:
                return
            self._pending_restarts.add(channel)
            self._commands.put(('restart', channel))

    def pending(self, channel: str) -> bool:
        with self._lock:
            return channel in self._pending_channels or self._pending_start or self._phase == 'starting'

    def poll_errors(self) -> list[tuple[str, str]]:
        result = []
        while True:
            try:
                result.append(self._errors.get_nowait())
            except queue.Empty:
                return result

    def shutdown(self) -> threading.Thread | None:
        """Request teardown without joining on the GUI thread."""
        with self._lock:
            if not self._closing:
                self._closing = True
                self._desired_running = False
                self._commands.put(('shutdown', None))
            return self._thread

    def _is_desired(self) -> bool:
        with self._lock:
            return self._desired_running and not self._closing

    def _set_phase(self, value: str) -> None:
        with self._lock:
            self._phase = value

    def _stop_workers(self) -> None:
        self._set_phase('stopping')
        for name, worker in self._workers.items():
            try:
                worker.close()
            except Exception:
                pass
        self._set_phase('idle')

    def _start_workers(self, names: tuple[str, ...]) -> None:
        self._set_phase('starting')
        for name in names:
            if not self._is_desired():
                break
            worker = self._workers[name]
            with self._lock:
                self._pending_channels.add(name)
            try:
                if not worker.alive:
                    worker.start()
            except Exception as exc:
                self._errors.put((name, f'{type(exc).__name__}: {exc}'))
            finally:
                with self._lock:
                    self._pending_channels.discard(name)
        self._set_phase('running' if self._is_desired() else 'idle')

    def _run(self) -> None:
        try:
            while True:
                command, channel = self._commands.get()
                with self._lock:
                    if command == 'start':
                        self._pending_start = False
                    elif command == 'stop':
                        self._pending_stop = False
                    elif command == 'restart' and channel is not None:
                        self._pending_restarts.discard(channel)
                    closing = self._closing
                    desired = self._desired_running
                if command == 'shutdown' or closing:
                    self._stop_workers()
                    return
                if command == 'stop':
                    # Start may have been requested again while stop was queued.
                    # The subsequent start command will resume the workers.
                    self._stop_workers()
                elif command == 'start':
                    if desired:
                        self._start_workers(tuple(self._workers))
                elif command == 'restart' and channel is not None and desired:
                    try:
                        self._workers[channel].close()
                    except Exception:
                        pass
                    self._start_workers((channel,))
        finally:
            self._set_phase('stopped')
