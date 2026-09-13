from dofusic.vision.scheduler import OCRChannelScheduler


def test_scheduler_never_replaces_active_request_id():
    scheduler = OCRChannelScheduler()
    assert scheduler.should_submit(now=1.0, min_interval=0.25)
    scheduler.submitted(request_id=7, now=1.0)

    assert scheduler.active_request_id == 7
    assert not scheduler.should_submit(now=1.30, min_interval=0.25)
    assert scheduler.active_request_id == 7
    assert scheduler.refresh_pending is True


def test_scheduler_accepts_only_matching_completion_and_then_allows_current_frame():
    scheduler = OCRChannelScheduler()
    scheduler.submitted(request_id=7, now=1.0)

    assert scheduler.completed(request_id=6, now=1.40) is False
    assert scheduler.active_request_id == 7
    assert scheduler.completed(request_id=7, now=1.40) is True
    assert scheduler.active_request_id is None
    # Cadence was already satisfied while inference was running; do not add a
    # second artificial wait after completion.
    assert scheduler.should_submit(now=1.40, min_interval=0.25)


def test_scheduler_enforces_cadence_after_fast_completion():
    scheduler = OCRChannelScheduler()
    scheduler.submitted(request_id=1, now=2.0)
    assert scheduler.completed(request_id=1, now=2.05)
    assert not scheduler.should_submit(now=2.10, min_interval=0.25)
    assert scheduler.should_submit(now=2.25, min_interval=0.25)


def test_worker_client_submit_never_replaces_already_queued_request():
    import queue
    from types import SimpleNamespace
    import numpy as np
    from dofusic.vision.workers import OCRWorkerClient

    client = OCRWorkerClient(channel='zone')
    client._requests = queue.Queue(maxsize=1)
    client._process = SimpleNamespace(is_alive=lambda: True)

    first = client.submit(np.zeros((4, 4, 3), dtype=np.uint8), captured_at=1.0)
    second = client.submit(np.ones((4, 4, 3), dtype=np.uint8), captured_at=2.0)

    queued = client._requests.get_nowait()
    assert first == queued.request_id
    assert second is None
    assert queued.captured_at == 1.0
