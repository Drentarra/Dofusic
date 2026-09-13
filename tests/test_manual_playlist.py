from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path

from dofusic.online.models import OnlineTrack
from dofusic.online.playback import PlaybackController


class _Library:
    def __init__(self, tracks):
        self.all_tracks = tuple(tracks)
    def scan(self):
        return None


class _Player:
    def __init__(self):
        self.busy = True
    def is_playing(self):
        return self.busy


class _Controller:
    def __init__(self, tracks):
        self.music_library = _Library(tracks)
        self.player = _Player()
        self.calls = []
    def play_online_track(self, path, title):
        self.calls.append(('online-file', Path(path), title))
        return True
    def play_manual_local_track(self, path, title=None, *, loop=False):
        self.calls.append(('local', Path(path), title, loop))
        return True
    def resume_local_audio(self):
        self.calls.append(('resume',))
        return True


class _MediaCache:
    def __init__(self, path=Path('cached.mp3')):
        self.path = Path(path)
    def ensure_cached(self, track):
        return self.path


class _ManualExecutor:
    def __init__(self):
        self.futures = []
    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.futures.append((fn, args, kwargs, future))
        return future
    def shutdown(self, **kwargs):
        pass


class _Config:
    volume = 70
    mute = False


def test_online_track_cache_prepare_is_dispatched_off_the_ui_thread(tmp_path):
    cached = tmp_path / 'cached.mp3'
    cached.write_bytes(b'audio')
    controller = _Controller(())
    executor = _ManualExecutor()
    playback = PlaybackController(controller, _Config(), _MediaCache(cached), executor=executor)
    track = OnlineTrack('abcdefghijk', 'Titre')

    playback.play_now(track)

    assert playback.current is None
    assert playback.preparing is not None
    assert playback.preparing.title == 'Titre'
    assert controller.calls == []
    assert len(executor.futures) == 1

    fn, args, kwargs, future = executor.futures.pop(0)
    future.set_result(fn(*args, **kwargs))
    playback.tick(now=999.0)

    assert playback.preparing is None
    assert playback.current is not None
    assert playback.current.title == 'Titre'
    assert playback.current.source == 'online'
    assert controller.calls == [('online-file', cached, 'Titre')]


def test_local_tracks_can_be_enqueued_in_same_manual_playlist(tmp_path):
    first = tmp_path / 'First.mp3'
    second = tmp_path / 'Second.mp3'
    first.write_bytes(b'1')
    second.write_bytes(b'2')
    controller = _Controller((first, second))
    playback = PlaybackController(controller, _Config(), _MediaCache(), executor=_ManualExecutor())

    assert playback.play_local_track(first)
    playback.enqueue_local_track(second)

    assert playback.current is not None
    assert playback.current.source == 'local'
    assert playback.current.local_path == first
    assert playback.pending[0].source == 'local'
    assert playback.pending[0].local_path == second
    assert controller.calls[-1] == ('local', first, 'First', False)


def test_success_can_clear_stale_online_error():
    playback = PlaybackController(_Controller(()), _Config(), _MediaCache(), executor=_ManualExecutor())
    playback.last_error = 'ancienne erreur'
    playback.clear_error()
    assert playback.last_error == ''


def test_online_plus_only_enqueues_and_never_autostarts_media():
    controller = _Controller(())
    executor = _ManualExecutor()
    playback = PlaybackController(controller, _Config(), _MediaCache(), executor=executor)
    track = OnlineTrack('abcdefghijk', 'En attente')

    playback.enqueue(track)

    assert playback.current is None
    assert playback.preparing is None
    assert len(playback.pending) == 1
    assert playback.pending[0].online_track == track
    assert executor.futures == []
    assert controller.calls == []


def test_local_plus_only_enqueues_and_never_autostarts_when_manual_playlist_is_empty(tmp_path):
    local = tmp_path / 'Queued.mp3'
    local.write_bytes(b'audio')
    controller = _Controller((local,))
    playback = PlaybackController(controller, _Config(), _MediaCache(), executor=_ManualExecutor())

    assert playback.enqueue_local_track(local)

    assert playback.current is None
    assert len(playback.pending) == 1
    assert playback.pending[0].local_path == local
    assert controller.calls == []
