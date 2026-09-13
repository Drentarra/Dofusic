from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.player import MusicPlayer
from dofusic.online.discovery import YouTubeDiscoveryClient
from dofusic.online.models import OnlineTrack
from dofusic.online.media_cache import MediaCache


def test_discovery_client_is_stateless_and_has_no_playback_surface():
    client = YouTubeDiscoveryClient(
        search_runner=lambda _query, _limit: (),
        suggestion_runner=lambda _query, _limit: (),
    )

    assert client.search('test') == ()
    assert client.suggestions('test') == ()
    assert not hasattr(client, 'play')
    assert not hasattr(client, 'stop')
    assert not hasattr(client, 'playback_state')
    assert not hasattr(client, 'spectrum_levels')


def test_media_cache_coalesces_same_video_cache_work(tmp_path):
    calls = 0
    calls_lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def downloader(track, cache_dir):
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(2.0)
        target = Path(cache_dir) / f'{track.video_id}.mp3'
        target.write_bytes(b'audio')
        return target

    def transcoder(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        return target

    service = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader, transcoder=transcoder)
    track = OnlineTrack('abcdefghijk', 'Titre')

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.ensure_cached, track)
        assert started.wait(1.0)
        second = executor.submit(service.ensure_cached, track)
        time.sleep(0.08)
        with calls_lock:
            observed_calls = calls
        release.set()
        assert first.result(timeout=2.0).name == 'abcdefghijk.opus'
        assert second.result(timeout=2.0).name == 'abcdefghijk.opus'

    assert observed_calls == 1
    assert calls == 1


def test_music_player_loads_track_once(tmp_path):
    class Channel:
        def __init__(self):
            self.load_calls = []
        def load(self, value): self.load_calls.append(value)
        def set_volume(self, value): pass
        def play(self, *args, **kwargs): pass
        def fadeout(self, value): pass
        def get_busy(self): return True

    channel = Channel()
    player = MusicPlayer(spectrum_enabled=False)
    player._ready = True
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=channel))
    player._start_track_analysis = lambda path: None
    target = tmp_path / 'track.mp3'
    target.write_bytes(b'audio')

    assert player.play(target, loop=False) is True
    assert channel.load_calls == [str(target)]


def test_thumbnail_service_fetches_once_and_reuses_disk_cache(tmp_path):
    from dofusic.online.thumbnails import ThumbnailService

    calls = []
    import io
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (2, 2), 'white').save(stream, format='PNG')
    png = stream.getvalue()

    def fetch(url, timeout):
        calls.append((url, timeout))
        return png

    service = ThumbnailService(tmp_path, fetcher=fetch, max_items=4)
    track = OnlineTrack('abcdefghijk', 'Titre', thumbnail_url='https://example.test/thumb.png')

    first = service.get_png(track, size=(120, 68))
    second = service.get_png(track, size=(120, 68))

    assert first.startswith(b'\x89PNG')
    assert second == first
    assert len(calls) == 1


def test_search_controller_prewarm_is_single_flight():
    from concurrent.futures import Future
    from dofusic.online.search import SearchController

    pending = Future()
    class Executor:
        def __init__(self): self.calls = 0
        def submit(self, fn, *args, **kwargs):
            self.calls += 1
            return pending
        def shutdown(self, **kwargs): pass
    class Browser:
        def prewarm(self): return True
        def close(self): pass
    executor = Executor()
    controller = SearchController(Browser(), executor=executor, suggestion_executor=executor)

    controller.prewarm()
    controller.prewarm()
    assert executor.calls == 1

def test_playback_rejects_different_play_while_preparing():
    from concurrent.futures import Future
    from dofusic.online.playback import PlaybackController

    pending = Future()
    class Executor:
        def __init__(self): self.calls = 0
        def submit(self, fn, *args, **kwargs):
            self.calls += 1
            return pending
        def shutdown(self, **kwargs): pass
    media = SimpleNamespace(ensure_cached=lambda track: Path(f'{track.video_id}.mp3'))
    config = SimpleNamespace(volume=70, mute=False)
    executor = Executor()
    playback = PlaybackController(SimpleNamespace(), config, media, executor=executor)
    first = OnlineTrack('abcdefghijk', 'Premier')
    second = OnlineTrack('lmnopqrstuv', 'Second')

    assert playback.play_now(first) is True
    assert playback.play_now(second) is False
    assert playback.preparing is not None
    assert playback.preparing.online_track == first
    assert executor.calls == 1

def test_music_session_thumbnail_request_reuses_pending_future(tmp_path):
    from concurrent.futures import Future
    from dofusic.online.session import MusicSession

    pending = Future()
    class Executor:
        def submit(self, fn, *args, **kwargs): return Future()
        def shutdown(self, **kwargs): pass
    class ThumbExecutor:
        def __init__(self): self.calls = 0
        def submit(self, fn, *args, **kwargs):
            self.calls += 1
            return pending
        def shutdown(self, **kwargs): pass
    class Browser:
        def close(self): pass
    class Thumbnails:
        def get_png(self, track, *, size): return b'png'
    class Search:
        def close(self): pass
        def prewarm(self): pass
        def tick(self): pass
        def snapshot(self): return SimpleNamespace(revision=0, phase=SimpleNamespace())
    class Playback:
        current=None; preparing=None; pending=(); repeat_current=False; status=''; last_error=''
        def close(self): pass
        def tick(self): pass
    config = SimpleNamespace(music_dir=str(tmp_path / 'music'), online_cache_mb=64, online_search_results=8, volume=70, mute=False)
    thumb_executor = ThumbExecutor()
    session = MusicSession(
        SimpleNamespace(), config, discovery=Browser(), media_cache=SimpleNamespace(),
        search_controller=Search(), playback=Playback(), thumbnail_service=Thumbnails(),
        media_executor=Executor(), thumbnail_executor=thumb_executor,
    )
    track = OnlineTrack('abcdefghijk', 'Titre')

    session.request_thumbnail(track, size=(112, 63))
    session.request_thumbnail(track, size=(112, 63))

    assert thumb_executor.calls == 1

def test_music_window_v13_renders_async_thumbnails_and_prewarm():
    import inspect
    from dofusic.ui.music_window import MusicWindow

    render = inspect.getsource(MusicWindow._render_results)
    source_mode = inspect.getsource(MusicWindow._set_source_mode)
    poll = inspect.getsource(MusicWindow._poll)

    assert 'request_thumbnail' in render
    assert '_thumbnail_labels' in render
    assert 'THUMBNAIL_SIZE' in render
    assert 'session.prewarm()' in source_mode
    assert 'tk.PhotoImage' in poll


def test_music_window_disables_title_play_surface_while_preparing():
    import inspect
    from dofusic.ui.music_window import MusicWindow

    render = inspect.getsource(MusicWindow._render_results)
    busy = inspect.getsource(MusicWindow._update_busy_indicators)
    assert '_play_title_buttons' in render
    assert '_play_title_buttons' in busy
    assert "state='disabled' if preparing_id else 'normal'" in busy


def test_controller_has_single_audio_override_state():
    import inspect
    from dofusic.app import DofusicController
    source = inspect.getsource(DofusicController)
    assert '_online_audio_active' not in source


def test_portable_release_precreates_thumbnail_cache_directory():
    source = Path('Data/tools/build_portable.py').read_text(encoding='utf-8')
    assert "'cache' / 'thumbnails'" in source
