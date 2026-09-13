from __future__ import annotations

from concurrent.futures import Future
import time
from pathlib import Path

from dofusic.online.models import OnlineTrack
from dofusic.online.playback import PlaybackController
from dofusic.online.search import SearchController, SearchPhase
from dofusic.online.session import MediaActionPhase, MusicSession


class _Library:
    def __init__(self, tracks=()):
        self.all_tracks = tuple(tracks)
    def scan(self):
        return None


class _Player:
    def __init__(self):
        self.busy = True
        self.levels = tuple((i + 1) / 24 for i in range(24))
    def is_playing(self):
        return self.busy
    def spectrum_levels(self, count=24):
        return tuple(self.levels[:count])


class _Controller:
    def __init__(self, tracks=()):
        self.music_library = _Library(tracks)
        self.player = _Player()
        self.calls = []
    def play_online_track(self, path, title):
        self.calls.append(('online-file', Path(path), title))
        self.player.busy = True
        return True
    def play_manual_local_track(self, path, title=None, *, loop=False):
        self.calls.append(('local', Path(path), title, loop))
        return True
    def resume_local_audio(self):
        self.calls.append(('resume',))
        return True


class _Discovery:
    def __init__(self):
        self.calls = []
    def prewarm(self):
        self.calls.append(('prewarm',))
        return True
    def search(self, query, *, limit=8):
        self.calls.append(('search', query, limit))
        return (OnlineTrack('abcdefghijk', 'Resultat'),)
    def suggestions(self, query, *, limit=8):
        self.calls.append(('suggestions', query, limit))
        return ('Suggestion',)
    def close(self):
        self.calls.append(('close',))


class _MediaCache:
    def __init__(self, path):
        self.path = Path(path)
        self.calls = []
    def ensure_cached(self, track):
        self.calls.append(('cache', track.video_id))
        return self.path
    def save_to_library(self, track, desired_name):
        self.calls.append(('save', track.video_id, desired_name))
        return self.path


class _Thumbnails:
    def get_png(self, track, *, size):
        return b''


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future
    def shutdown(self, **kwargs):
        pass


class _Config:
    music_dir = ''
    online_cache_mb = 64
    online_search_results = 8
    volume = 64
    mute = False


def _session(tmp_path, *, controller=None, discovery=None, media=None):
    controller = controller or _Controller()
    discovery = discovery or _Discovery()
    media = media or _MediaCache(tmp_path / 'cached.mp3')
    executor = _ImmediateExecutor()
    search = SearchController(discovery, result_limit=8, executor=executor, suggestion_executor=executor)
    playback = PlaybackController(controller, _Config(), media, executor=executor)
    return MusicSession(
        controller,
        _Config(),
        discovery=discovery,
        media_cache=media,
        search_controller=search,
        playback=playback,
        thumbnail_service=_Thumbnails(),
        media_executor=executor,
        thumbnail_executor=executor,
    )


def test_online_play_prepares_cache_then_uses_dofusic_player(tmp_path):
    controller = _Controller()
    discovery = _Discovery()
    cached = tmp_path / 'cached.mp3'
    media = _MediaCache(cached)
    session = _session(tmp_path, controller=controller, discovery=discovery, media=media)
    track = OnlineTrack('abcdefghijk', 'Titre')

    session.play_now(track)

    assert session.current is not None
    assert session.current.online_track == track
    assert ('cache', 'abcdefghijk') in media.calls
    assert controller.calls == [('online-file', cached, 'Titre')]
    assert not any(call[0] == 'play' for call in discovery.calls)


def test_search_and_suggestions_use_discovery_backend(tmp_path):
    discovery = _Discovery()
    session = _session(tmp_path, discovery=discovery)

    session.submit_search('abc')
    session.tick()
    assert session.search_snapshot().phase is SearchPhase.RESULTS
    assert session.search_snapshot().results[0].title == 'Resultat'

    session.submit_suggestions('abc')
    session.tick()
    assert session.suggestions_snapshot().values == ('Suggestion',)
    assert ('search', 'abc', 8) in discovery.calls
    assert ('suggestions', 'abc', 8) in discovery.calls


def test_online_queue_advances_using_dofusic_player_state(tmp_path):
    controller = _Controller()
    session = _session(tmp_path, controller=controller)
    first = OnlineTrack('abcdefghijk', 'Premier')
    second = OnlineTrack('lmnopqrstuv', 'Second')
    session.play_now(first)
    session.enqueue(second)

    controller.player.busy = False
    session.playback.tick(now=time.monotonic() + 1.0)

    assert session.current is not None
    assert session.current.online_track == second


def test_volume_and_mute_do_not_drive_discovery_client(tmp_path):
    discovery = _Discovery()
    session = _session(tmp_path, discovery=discovery)

    session.set_volume(22)
    session.set_muted(True)

    assert session.config.volume == 22
    assert session.config.mute is True
    assert not any(call[0] in {'volume', 'muted'} for call in discovery.calls)


def test_stop_online_resumes_local_music_without_stopping_discovery_client(tmp_path):
    controller = _Controller()
    discovery = _Discovery()
    session = _session(tmp_path, controller=controller, discovery=discovery)
    session.play_now(OnlineTrack('abcdefghijk', 'Titre'))

    session.stop_online()

    assert controller.calls[-1] == ('resume',)
    assert session.current is None
    assert ('close',) not in discovery.calls


def test_save_to_library_is_exposed_as_typed_media_event(tmp_path):
    target = tmp_path / 'Titre.mp3'
    media = _MediaCache(target)
    session = _session(tmp_path, media=media)
    track = OnlineTrack('abcdefghijk', 'Titre')

    assert session.begin_save(track, 'Nom') is True
    session.tick()

    event = session.media_event()
    assert event.phase is MediaActionPhase.SAVED
    assert event.path == target
    assert media.calls == [('save', 'abcdefghijk', 'Nom')]


def test_search_memory_keeps_online_and_local_queries_separate(tmp_path):
    session = _session(tmp_path)
    online_results = (OnlineTrack('abcdefghijk', 'Shaka Ponk'),)

    session.remember_search('online', 'shaka ponk', online_results)
    session.remember_search('local', 'combat', ())

    assert session.search_memory('online') == ('shaka ponk', online_results)
    assert session.search_memory('local') == ('combat', ())


def test_search_memory_survives_music_window_close_by_living_in_session(tmp_path):
    session = _session(tmp_path)
    results = (OnlineTrack('abcdefghijk', 'Titre'),)
    session.remember_search('online', "bob l'éponge", results)

    assert session.search_memory('online')[0] == "bob l'éponge"
    assert session.search_memory('online')[1] == results


def test_online_file_playback_advances_from_local_player_busy_state(tmp_path):
    first_path = tmp_path / 'first.mp3'
    second_path = tmp_path / 'second.mp3'
    first_path.write_bytes(b'a')
    second_path.write_bytes(b'b')

    class Media(_MediaCache):
        def ensure_cached(self, track):
            self.calls.append(('cache', track.video_id))
            return first_path if track.video_id == 'abcdefghijk' else second_path

    controller = _Controller()
    session = _session(tmp_path, controller=controller, media=Media(tmp_path / 'unused.mp3'))
    first = OnlineTrack('abcdefghijk', 'Premier')
    second = OnlineTrack('lmnopqrstuv', 'Second')
    session.play_now(first)
    session.enqueue(second)

    controller.player.busy = False
    session.playback.tick(now=time.monotonic() + 1.0)

    assert session.current is not None
    assert session.current.online_track == second
    assert ('online-file', second_path, 'Second') in controller.calls
