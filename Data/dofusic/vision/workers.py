from __future__ import annotations

import multiprocessing as mp
import os
import queue
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Literal

import numpy as np

from dofusic.models import PositionOCRResult, ZoneOCRResult

OCRChannel = Literal['zone', 'position']
OCRResult = ZoneOCRResult | PositionOCRResult


class OCREventKind(str, Enum):
    STARTING = 'starting'
    READY = 'ready'
    RESULT = 'result'
    ERROR = 'error'
    STOPPED = 'stopped'


@dataclass(slots=True)
class OCRRequest:
    request_id: int
    image: np.ndarray
    captured_at: float
    submitted_at: float


@dataclass(slots=True)
class OCREvent:
    kind: OCREventKind
    channel: str
    request_id: int = 0
    result: OCRResult | None = None
    message: str = ''
    pid: int = 0
    captured_at: float = 0.0
    submitted_at: float = 0.0
    finished_at: float = 0.0


def _put_latest(q, value) -> bool:
    try:
        q.put_nowait(value)
        return True
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(value)
        return True
    except queue.Full:
        return False


def _worker_main(channel: str, requests, events, cpu_threads: int) -> None:
    pid = os.getpid()
    _put_latest(events, OCREvent(OCREventKind.STARTING, channel, message=f'Chargement OCR {channel}', pid=pid))
    try:
        # Import inside subprocess only. This keeps RapidOCR/ONNX out of the UI process.
        from dofusic.vision.engines import PositionOCREngine, ZoneOCREngine

        if channel == 'zone':
            engine = ZoneOCREngine(cpu_threads=cpu_threads)
        elif channel == 'position':
            engine = PositionOCREngine(cpu_threads=cpu_threads)
        else:
            raise ValueError(f'Canal OCR inconnu: {channel}')
        engine.load()
        warmup = getattr(engine, 'warmup', None)
        if callable(warmup):
            warmup()
        _put_latest(events, OCREvent(OCREventKind.READY, channel, message=engine.engine_name, pid=pid))
    except BaseException:
        _put_latest(events, OCREvent(OCREventKind.ERROR, channel, message=traceback.format_exc(), pid=pid))
        return

    while True:
        try:
            request = requests.get(timeout=0.25)
        except queue.Empty:
            continue
        if request is None:
            _put_latest(events, OCREvent(OCREventKind.STOPPED, channel, message='Arrêt demandé', pid=pid))
            return
        if not isinstance(request, OCRRequest):
            continue

        try:
            result = engine.read(request.image)
            finished_at = time.monotonic()
            _put_latest(events, OCREvent(
                OCREventKind.RESULT, channel, request.request_id, result, 'ok', pid,
                captured_at=request.captured_at,
                submitted_at=request.submitted_at,
                finished_at=finished_at,
            ))
        except BaseException:
            finished_at = time.monotonic()
            _put_latest(events, OCREvent(
                OCREventKind.ERROR, channel, request.request_id, None, traceback.format_exc(), pid,
                captured_at=request.captured_at,
                submitted_at=request.submitted_at,
                finished_at=finished_at,
            ))


class OCRWorkerClient:
    def __init__(self, *, channel: OCRChannel, cpu_threads: int = 1) -> None:
        if channel not in ('zone', 'position'):
            raise ValueError(channel)
        self.channel = channel
        self.cpu_threads = max(1, int(cpu_threads))
        self._ctx = mp.get_context('spawn')
        self._requests = None
        self._events = None
        self._process: mp.Process | None = None
        self._next_request_id = 1

    @property
    def alive(self) -> bool:
        return bool(self._process and self._process.is_alive())

    @property
    def exitcode(self) -> int | None:
        return self._process.exitcode if self._process else None

    def start(self) -> None:
        if self.alive:
            return
        self._requests = self._ctx.Queue(maxsize=1)
        self._events = self._ctx.Queue(maxsize=8)
        self._process = self._ctx.Process(
            target=_worker_main,
            args=(self.channel, self._requests, self._events, self.cpu_threads),
            name=f'DofusicOCR-{self.channel}',
            daemon=True,
        )
        self._process.start()

    def submit(self, image: np.ndarray, captured_at: float | None = None) -> int | None:
        """Submit one OCR request without evicting any already queued request."""
        if not self.alive or self._requests is None:
            return None
        request_id = self._next_request_id
        submitted_at = time.monotonic()
        observed_at = submitted_at if captured_at is None else float(captured_at)
        request = OCRRequest(request_id, np.ascontiguousarray(image).copy(), observed_at, submitted_at)
        try:
            self._requests.put_nowait(request)
        except queue.Full:
            return None
        self._next_request_id += 1
        return request_id

    def submit_latest(self, image: np.ndarray, captured_at: float | None = None) -> int | None:
        """Compatibility alias; V20 scheduling is single-flight, never latest-wins."""
        return self.submit(image, captured_at=captured_at)

    def poll(self) -> list[OCREvent]:
        if self._events is None:
            return []
        items: list[OCREvent] = []
        while True:
            try:
                items.append(self._events.get_nowait())
            except queue.Empty:
                break
        return items

    def restart(self) -> None:
        self.close(timeout=0.5)
        self.start()

    def close(self, *, timeout: float = 1.5) -> None:
        process = self._process
        if process is None:
            return
        if self._requests is not None and process.is_alive():
            try:
                self._requests.put_nowait(None)
            except queue.Full:
                try:
                    self._requests.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._requests.put_nowait(None)
                except queue.Full:
                    pass
        process.join(timeout=max(0.0, timeout))
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.5)
        self._process = None
        self._requests = None
        self._events = None
