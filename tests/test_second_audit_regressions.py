"""Régressions issues du second audit : arrêt Tk, OCR et sauvegardes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from dofusic.online.media_cache import MediaCache
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


def test_concurrent_saves_choose_distinct_files_without_overwriting(tmp_path):
    source = tmp_path / 'cache' / 'abcdefghijk.opus'
    source.parent.mkdir()
    source.write_bytes(b'valid audio bytes')
    media = MediaCache(tmp_path / 'cache', tmp_path / 'music')
    media.ensure_cached = lambda track: source
    track = SimpleNamespace(video_id='abcdefghijk', title='Même nom')

    def save(_index):
        return media.save_to_library(track, 'Ma musique')

    with ThreadPoolExecutor(max_workers=8) as executor:
        files = list(executor.map(save, range(8)))
    assert len(set(files)) == 8
    assert files[0].name == 'Ma musique.opus' or 'Ma musique.opus' in {p.name for p in files}
    assert all(path.read_bytes() == source.read_bytes() for path in files)


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


def test_built_program_reports_the_next_version():
    from dofusic.version import __version__
    assert __version__ == '1.0.12'


def test_thumbnail_pending_jobs_and_ready_cache_remain_bounded():
    from concurrent.futures import Future
    import threading

    from dofusic.online.models import OnlineTrack
    from dofusic.online.session import MusicSession

    class ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):
            future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    session = MusicSession.__new__(MusicSession)
    session._lock = threading.RLock()
    session._thumbnail_jobs = {}
    session._thumbnail_ready = {}
    session._thumbnail_ready_limit = 48
    session._thumbnail_job_limit = 48
    session._thumbnail_executor = ImmediateExecutor()
    session.thumbnail_service = SimpleNamespace(get_png=lambda track, size: b'png')
    session.search = SimpleNamespace(
        tick=lambda: None, snapshot=lambda: SimpleNamespace(revision=0, phase=None),
    )
    session.playback = SimpleNamespace(tick=lambda: None)
    session._last_search_revision = 0
    session._media_future = None

    for index in range(70):
        session.request_thumbnail(OnlineTrack(f'{index:011d}', f'Titre {index}'))
    assert len(session._thumbnail_jobs) == 48

    session.tick()
    assert len(session._thumbnail_jobs) == 0
    assert len(session._thumbnail_ready) == 48

    for index in range(70, 95):
        session.request_thumbnail(OnlineTrack(f'{index:011d}', f'Titre {index}'))
    session.tick()
    assert len(session._thumbnail_ready) == 48
    assert session.take_thumbnail(f'{94:011d}') == b'png'
