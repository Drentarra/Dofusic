from __future__ import annotations

import inspect
from concurrent.futures import Future
from types import SimpleNamespace

from dofusic.online.playback import PlaybackController
from dofusic.online.models import OnlineTrack, PlaylistItem
from dofusic.ui.music_window import MusicWindow


def test_online_result_row_reserves_fixed_controls_column():
    source = inspect.getsource(MusicWindow._render_results)
    assert 'controls = tk.Frame(row' in source
    assert "controls.pack(side='right'" in source or "controls.grid(" in source
    assert "info.pack(side='left', fill='x', expand=True)" in source or 'info.grid(' in source
    assert "text='▶'" in source
    assert "text='↓'" in source


def test_music_window_has_busy_indicators_for_search_play_and_download():
    build = inspect.getsource(MusicWindow._build)
    render = inspect.getsource(MusicWindow._render_results)
    poll = inspect.getsource(MusicWindow._poll)
    assert 'search_busy_label' in build
    assert '_play_buttons' in render
    assert '_download_buttons' in render
    assert '_update_busy_indicators' in poll


def test_repeated_click_on_same_preparing_online_track_does_not_restart_prepare():
    track = OnlineTrack('abcdefghijk', 'Titre')
    pending = Future()

    class Executor:
        def __init__(self):
            self.calls = 0
        def submit(self, fn, *args, **kwargs):
            self.calls += 1
            return pending

    executor = Executor()
    media_cache = SimpleNamespace(ensure_cached=lambda _track: None)
    controller = SimpleNamespace()
    config = SimpleNamespace(music_dir='', volume=70, mute=False)
    playback = PlaybackController(controller, config, media_cache, executor=executor)

    playback.play_now(track)
    playback.play_now(track)

    assert executor.calls == 1
    assert playback.preparing == PlaylistItem.online(track)
