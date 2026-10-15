from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.player import MusicPlayer


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


def test_controller_has_single_audio_override_state():
    import inspect
    from dofusic.app import DofusicController
    source = inspect.getsource(DofusicController)
    assert '_online_audio_active' not in source


