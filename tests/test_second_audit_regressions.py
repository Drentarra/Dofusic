"""Régressions issues du second audit : arrêt Tk, OCR et sauvegardes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dofusic.ui.main_window import MainWindow
from dofusic.vision.workers import OCRWorkerClient


def test_tk_refresh_does_not_reschedule_when_window_is_closed():
    window = MainWindow.__new__(MainWindow)
    window._closed = True
    window.root = SimpleNamespace(after=lambda *_args: pytest.fail('Tk callback after close'))
    window.controller = SimpleNamespace(tick=lambda: pytest.fail('controller tick after close'))
    window._update()
    window._visualizer_tick()
    window.close()


def test_tk_refresh_stops_scheduling_when_close_occurs_inside_tick():
    calls = []
    window = MainWindow.__new__(MainWindow)
    window._closed = False
    window.root = SimpleNamespace(after=lambda *_args: calls.append('after'))
    window.config = SimpleNamespace(ui_tick_ms=100)

    def tick():
        window._closed = True
        raise ValueError('Fermeture pendant le rappel Tk')

    window.controller = SimpleNamespace(tick=tick, logger=SimpleNamespace(exception=lambda *_args: None))
    window.overlay = SimpleNamespace(hide=lambda: None)
    window.palette = SimpleNamespace(text='white', red='red')
    window._set_widget = lambda *_args, **_kwargs: None
    window.window_indicator = object()
    window.window_indicator_dot = object()
    window._update()
    assert calls == []


def test_worker_restart_cleans_old_queues_before_start():
    class FakeQueue:
        def __init__(self):
            self.closed = False

        def cancel_join_thread(self):
            pass

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.running = False
            self.joined = False

        def is_alive(self):
            return self.running

        def join(self, timeout=None):
            self.joined = True

        def start(self):
            self.running = True

    class Context:
        def __init__(self):
            self.created = []

        def Queue(self, maxsize):
            q = FakeQueue()
            self.created.append(q)
            return q

        def Process(self, **kwargs):
            return FakeProcess()

    worker = OCRWorkerClient(channel='zone')
    previous_req, previous_events = FakeQueue(), FakeQueue()
    old = FakeProcess()
    worker._process = old
    worker._requests = previous_req
    worker._events = previous_events
    worker._ctx = Context()
    worker.start()
    assert old.joined
    assert previous_req.closed and previous_events.closed
    assert worker.alive
    worker._process.running = False
    worker.close()
    assert all(q.closed for q in worker._ctx.created)


def test_worker_start_failure_releases_allocated_ipc_queues():
    class FakeQueue:
        closed = False

        def cancel_join_thread(self):
            pass

        def close(self):
            self.closed = True

    class Unstartable:
        def start(self):
            raise OSError('Windows process access denied')

    class Context:
        def __init__(self):
            self.created = []

        def Queue(self, maxsize):
            q = FakeQueue()
            self.created.append(q)
            return q

        def Process(self, **kwargs):
            return Unstartable()

    worker = OCRWorkerClient(channel='position')
    worker._ctx = Context()
    with pytest.raises(OSError):
        worker.start()
    assert worker._process is None
    assert worker._requests is None and worker._events is None
    assert all(q.closed for q in worker._ctx.created)


def test_program_version_uses_three_component_semver():
    from dofusic.version import __version__
    assert len(__version__.split('.')) == 3
    assert all(component.isdigit() for component in __version__.split('.'))
