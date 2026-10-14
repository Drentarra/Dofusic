"""Contrôles de sécurité des médias et d'inactivité de l'interface."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dofusic.online.discovery import _track_from_row
from dofusic.online.errors import MediaCacheError
from dofusic.online.media_cache import MediaCache
from dofusic.online.models import OnlineTrack, valid_video_id
from dofusic.online.retrieval import YouTubeAudioFetcher
from dofusic.online.thumbnails import ThumbnailService
from dofusic.ui.main_window import MainWindow
from dofusic.vision.workers import OCRWorkerClient


def test_online_video_identifier_is_validated_at_every_file_boundary(tmp_path):
    assert valid_video_id('abcdefghijk')
    assert not valid_video_id('../private/')
    assert not valid_video_id('bad*pattern')
    assert _track_from_row({'id': '../private/', 'title': 'Invalide'}) is None

    bad = OnlineTrack('../private/', 'Invalide')
    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music')
    with pytest.raises(MediaCacheError):
        cache.validate_track(bad)

    service = ThumbnailService(tmp_path / 'thumbnails',
                               fetcher=lambda *_args: (_ for _ in ()).throw(AssertionError('network called')))
    assert service.get_png(bad) == b''
    assert not list((tmp_path / 'thumbnails').iterdir())

    fetcher = YouTubeAudioFetcher(tmp_path / 'downloads',
                                runner=lambda *_args: (_ for _ in ()).throw(AssertionError('yt-dlp called')))
    with pytest.raises(MediaCacheError):
        fetcher.fetch(bad)


def test_visualizer_does_not_repeat_identical_tk_canvas_commands():
    class Canvas:
        def __init__(self):
            self.calls = []

        def coords(self, bar, *coordinates):
            self.calls.append(('coords', bar, coordinates))
            return (bar * 10, 0, bar * 10 + 5, 20)

        def itemconfigure(self, bar, **options):
            self.calls.append(('itemconfigure', bar, options))

    window = MainWindow.__new__(MainWindow)
    window.canvas = Canvas()
    window.palette = SimpleNamespace(track_active='green', visualizer_idle='gray')
    window._visualizer_bars = (1, 2)
    window._visualizer_max_height = 40
    window._visualizer_baseline = 60

    window._redraw_visualizer((0.0, 0.0))
    first_count = len(window.canvas.calls)
    assert first_count == 6
    window._redraw_visualizer((0.0, 0.0))
    assert len(window.canvas.calls) == first_count
    window._visualizer_last_drawn = {}
    window._redraw_visualizer((0.0, 0.0))
    assert len(window.canvas.calls) == first_count * 2


def test_ocr_worker_close_does_not_join_blocked_queue_feeders():
    class DeadProcess:
        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    class Queue:
        def __init__(self):
            self.calls = []

        def cancel_join_thread(self):
            self.calls.append('cancel')

        def close(self):
            self.calls.append('close')

        def join_thread(self):
            raise AssertionError('Blocking queue join must not be used')

    client = OCRWorkerClient(channel='position')
    requests = Queue()
    events = Queue()
    client._process = DeadProcess()
    client._requests = requests
    client._events = events
    client.close()
    assert requests.calls == ['cancel', 'close']
    assert events.calls == ['cancel', 'close']
    assert client._process is None


def test_keyed_lock_pool_serializes_same_key_and_releases_entries():
    import threading
    from dofusic.online.keyed_locks import KeyedLockPool

    pool = KeyedLockPool()
    entered = threading.Event()
    release = threading.Event()
    acquired = []

    def first_worker():
        with pool.hold('video-id'):
            entered.set()
            release.wait(2.0)

    def second_worker():
        with pool.hold('video-id'):
            acquired.append(True)

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    assert entered.wait(1.0)
    second.start()
    try:
        assert pool.active_count == 1
        assert pool.active_keys == frozenset({'video-id'})
        assert not acquired
    finally:
        release.set()
        first.join(2.0)
        second.join(2.0)
    assert not first.is_alive() and not second.is_alive()
    assert acquired == [True]
    assert pool.active_count == 0
