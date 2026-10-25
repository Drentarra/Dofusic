"""Vision/capture regressions from the 2.0.0 source audit."""
from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints

import numpy as np
import pytest

from dofusic.models import Coordinates
from dofusic.vision.engines import ZoneOCREngine
from dofusic.vision.position import PositionCandidate, select_position_candidate
from dofusic.vision.workers import OCREvent, OCREventKind, OCRWorkerClient


def test_live_hud_extraction_without_transform_never_infers_scale_from_pixels():
    import cv2

    from dofusic.vision.layout import HUDGeometry, HUDTransform, extract_hud_inputs
    from tools.image_io import read_image

    fixture = Path(__file__).parent / 'fixtures/hud/havresac_collapsed_corner.png'
    image = cv2.resize(read_image(fixture), None, fx=.75, fy=.75)
    hud = extract_hud_inputs(image, HUDGeometry())
    assert hud.transform == HUDTransform()


def test_position_consensus_requires_independent_image_variants():
    wrong = Coordinates(41, -23)
    correct = Coordinates(4, -23)
    candidates = [
        PositionCandidate('41, -23', wrong, .95, 'color'),
        PositionCandidate('41, -23', wrong, .94, 'color'),
        PositionCandidate('41, -23', wrong, .93, 'color'),
        PositionCandidate('4, -23', correct, .89, 'contrast'),
        PositionCandidate('4, -23', correct, .90, 'tophat'),
    ]
    assert select_position_candidate(candidates).coordinates == correct


def test_ocr_recognizer_type_hints_are_resolvable():
    assert get_type_hints(ZoneOCREngine._make_recognizer)['model_path'] is Path


def test_worker_poll_tolerates_queue_closed_by_lifecycle_thread():
    client = OCRWorkerClient(channel='zone')
    events = client._ctx.Queue(maxsize=8)
    client._events = events
    events.close()
    assert client.poll() == []


def test_worker_submit_tolerates_queue_closed_by_lifecycle_thread():
    client = OCRWorkerClient(channel='zone')
    requests = client._ctx.Queue(maxsize=1)
    client._requests = requests
    client._process = SimpleNamespace(is_alive=lambda: True)
    requests.close()
    assert client.submit(np.zeros((3, 8, 3), dtype=np.uint8)) is None


def test_worker_poll_does_not_mix_events_from_replaced_process():
    client = OCRWorkerClient(channel='zone')
    replacement = queue.Queue()
    current = OCREvent(OCREventKind.READY, 'zone', pid=2)
    replacement.put_nowait(current)

    class ReplacedQueue(queue.Queue):
        def get_nowait(self):
            event = super().get_nowait()
            client._events = replacement
            return event

    previous = ReplacedQueue()
    previous.put_nowait(OCREvent(OCREventKind.READY, 'zone', pid=1))
    client._events = previous
    assert client.poll() == []
    assert client.poll() == [current]


def test_worker_releases_first_queue_when_second_allocation_fails():
    client = OCRWorkerClient(channel='zone')
    requests = client._ctx.Queue(maxsize=1)
    allocated = []

    def make_queue(*, maxsize):
        if allocated:
            raise OSError('no IPC handles available')
        allocated.append(requests)
        return requests

    client._ctx = SimpleNamespace(Queue=make_queue)
    try:
        with pytest.raises(OSError, match='no IPC handles available'):
            client.start()
        with pytest.raises(ValueError, match='closed'):
            requests.put_nowait('must already be closed')
        assert client._requests is None
        assert client._events is None
        assert client._process is None
    finally:
        requests.cancel_join_thread()
        requests.close()
