from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dofusic.app import DofusicController
from dofusic.audio.player import MusicPlayer
from dofusic.ui.main_window import MainWindow


class _FakeMusicChannel:
    def __init__(self):
        self.stop_calls = 0
        self.fade_calls = []
    def stop(self):
        self.stop_calls += 1
    def fadeout(self, value):
        self.fade_calls.append(value)
    def get_busy(self):
        return False


class _FakePygame:
    def __init__(self, channel):
        self.mixer = SimpleNamespace(music=channel)


def test_music_player_stop_releases_current_track_without_closing_mixer(tmp_path):
    channel = _FakeMusicChannel()
    player = MusicPlayer(spectrum_enabled=False)
    player._ready = True
    player._pygame = _FakePygame(channel)
    player.current = tmp_path / 'local.mp3'

    player.stop(fade_ms=0)

    assert channel.stop_calls == 1
    assert player.current is None
    assert player._ready is True


def test_controller_online_track_uses_file_backed_audio_override(tmp_path):
    class _Player:
        def __init__(self): self.calls = []
        def play(self, path, *, loop=True, restart=False):
            self.calls.append((Path(path), loop, restart))
            return True

    target = tmp_path / 'online.mp3'
    target.write_bytes(b'audio')
    controller = DofusicController.__new__(DofusicController)
    controller.player = _Player()
    controller.state = SimpleNamespace(music='', status='')
    controller._audio_override_active = False
    controller._audio_override_source = None

    assert controller.play_online_track(target, 'Titre YouTube') is True
    assert controller.player.calls == [(target, False, True)]
    assert controller.audio_override_source == 'online'
    assert controller.state.music == 'Titre YouTube'


def test_main_window_volume_handler_propagates_to_music_session():
    import inspect
    source = inspect.getsource(MainWindow._on_volume)
    assert 'self.music_session.set_volume(volume)' in source
    assert 'online_coordinator' not in source


def test_main_window_mute_handler_propagates_to_music_session():
    import inspect
    source = inspect.getsource(MainWindow._on_mute)
    assert 'self.music_session.set_muted(muted)' in source
    assert 'online_coordinator' not in source
