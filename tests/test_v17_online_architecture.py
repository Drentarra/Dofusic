from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_search_controller_exposes_explicit_state_and_ignores_stale_completion():
    from dofusic.online.search import SearchController, SearchPhase

    class ManualExecutor:
        def __init__(self):
            self.jobs = []
        def submit(self, fn, *args, **kwargs):
            future = Future()
            self.jobs.append((future, fn, args, kwargs))
            return future
        def shutdown(self, **kwargs):
            pass

    browser = SimpleNamespace(search=lambda query, limit=8: (), suggestions=lambda query, limit=8: (), prewarm=lambda: True)
    executor = ManualExecutor()
    controller = SearchController(browser, result_limit=8, executor=executor, suggestion_executor=executor)

    controller.submit_search('old')
    first = executor.jobs[-1][0]
    first.set_running_or_notify_cancel()
    controller.submit_search('new')
    second = executor.jobs[-1][0]
    assert controller.snapshot().phase is SearchPhase.LOADING
    assert controller.snapshot().query == 'new'

    first.set_result((SimpleNamespace(video_id='abcdefghijk'),))
    controller.tick()
    assert controller.snapshot().phase is SearchPhase.LOADING
    assert controller.snapshot().query == 'new'

    second.set_result(tuple())
    controller.tick()
    assert controller.snapshot().phase is SearchPhase.EMPTY
    assert controller.snapshot().query == 'new'


def test_media_cache_releases_per_video_lock_after_operation(tmp_path):
    from dofusic.online.media_cache import MediaCache
    from dofusic.online.models import OnlineTrack

    produced = tmp_path / 'source.mp3'
    produced.write_bytes(b'audio')
    def transcoder(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        return target

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=lambda _track, _dir: produced, transcoder=transcoder)

    cache.ensure_cached(OnlineTrack('abcdefghijk', 'Track'))

    assert cache.active_lock_count == 0


def test_music_window_does_not_own_future_objects_anymore():
    import inspect
    import dofusic.ui.music_window as module

    source = inspect.getsource(module)
    assert 'from concurrent.futures import Future' not in source
    assert '_search_job' not in source
    assert '_suggest_job' not in source
    assert '_cache_job' not in source
    assert '_save_job' not in source


def test_obsolete_online_modules_are_not_part_of_public_slim_architecture():
    root = Path(__file__).resolve().parents[1] / 'Data' / 'dofusic' / 'online'
    assert not (root / 'coordinator.py').exists()
    assert not (root / 'download.py').exists()
    assert not (root / 'service.py').exists()
    assert not (root / 'youtube_page.py').exists()
    assert not (root / 'browser.py').exists()
    assert not (root / 'extractor.py').exists()
    assert (root / 'session.py').exists()
    assert (root / 'playback.py').exists()
    assert (root / 'media_cache.py').exists()
    assert (root / 'search.py').exists()
    assert (root / 'discovery.py').exists()


def test_search_controller_reuses_same_running_query_instead_of_queueing_duplicate():
    from dofusic.online.search import SearchController

    class ManualExecutor:
        def __init__(self):
            self.jobs = []
        def submit(self, fn, *args, **kwargs):
            future = Future()
            self.jobs.append((future, fn, args, kwargs))
            return future
        def shutdown(self, **kwargs):
            pass

    browser = SimpleNamespace(search=lambda query, limit=8: (), suggestions=lambda query, limit=8: (), prewarm=lambda: True)
    executor = ManualExecutor()
    controller = SearchController(browser, executor=executor, suggestion_executor=executor)

    first_revision = controller.submit_search('Shaka Ponk')
    second_revision = controller.submit_search('  shaka   ponk  ')

    assert second_revision == first_revision
    assert len(executor.jobs) == 1


def test_media_cache_ignores_interrupted_transcode_part_file(tmp_path):
    from dofusic.online.media_cache import MediaCache

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music')
    interrupted = cache.cache_dir / 'abcdefghijk.part.mp3'
    interrupted.write_bytes(b'partial')

    assert cache._cached_path('abcdefghijk') is None


def test_cache_pruning_never_removes_files_for_other_active_video(tmp_path):
    from dofusic.online.media_cache import MediaCache

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', cache_mb=64)
    cache.cache_bytes = 4
    active = cache.cache_dir / 'abcdefghijk.mp3'
    old = cache.cache_dir / 'lmnopqrstuv.mp3'
    active.write_bytes(b'active')
    old.write_bytes(b'old')

    with cache._video_lock('abcdefghijk'):
        cache.prune_cache()

    assert active.exists()
    assert not old.exists()


def test_music_window_does_not_tick_shared_session_from_its_own_poll_loop():
    import inspect
    from dofusic.ui.music_window import MusicWindow

    source = inspect.getsource(MusicWindow._poll)
    assert 'self.session.tick()' not in source
