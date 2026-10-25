"""Regressions for independently audited local-audio failure paths."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from dofusic.app import DofusicController
from dofusic.audio.player import MusicPlayer
from dofusic.config import AppConfig
from dofusic.ui.music_window import MusicWindow
from dofusic.vision.zone_stability import ZoneTextContinuity


def _player_with_channel(channel, *, normalize=False):
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=normalize)
    player._ready = True
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=channel))
    return player


def test_music_window_has_exact_local_title():
    import inspect
    assert "self.window.title('Dofusic Musique locale')" in inspect.getsource(MusicWindow.__init__)


def test_audio_replays_same_track_if_playback_has_stopped(tmp_path):
    calls = []

    class Channel:
        def get_busy(self):
            return False
        def fadeout(self, ms):
            calls.append(('fade', ms))
        def load(self, path):
            calls.append(('load', path))
        def set_volume(self, volume):
            pass
        def play(self, loops, fade_ms):
            calls.append(('play', loops))

    player = _player_with_channel(Channel())
    target = tmp_path / 'musique.opus'
    player.current = target
    assert player.play(target, loop=True)
    assert ('load', str(target)) in calls
    assert ('play', -1) in calls


def test_audio_unchanged_track_does_not_reload_when_actually_playing(tmp_path):
    class Channel:
        def get_busy(self):
            return True
        def load(self, path):
            raise AssertionError('already playing must not be loaded')

    player = _player_with_channel(Channel())
    target = tmp_path / 'musique.ogg'
    player.current = target
    assert player.play(target)


def test_audio_normalization_disabled_is_respected_on_new_track(tmp_path):
    volumes = []

    class Channel:
        def set_volume(self, volume):
            volumes.append(volume)
        def load(self, path):
            pass
        def play(self, loops, fade_ms):
            pass

    player = _player_with_channel(Channel(), normalize=False)
    target = tmp_path / 'ancien.opus'
    player._loudness_gain_by_path[target] = 0.4
    assert player.play(target)
    assert volumes and volumes[-1] == 0.7


def test_invalid_converted_audio_does_not_leak_temporary_file(tmp_path):
    target = tmp_path / 'original.wma'
    temporary = tmp_path / 'converted.ogg'
    temporary.write_bytes(b'fake conversion')

    class Channel:
        def load(self, path):
            raise ValueError('decode refused')

    player = _player_with_channel(Channel())
    player._transcode_playback_fallback = lambda path: temporary
    # A codec fallback is accepted asynchronously, then rejected on the UI pump.
    assert player.play(target)
    assert player.is_preparing(target)
    player._decode_thread.join(timeout=2)
    player.tick()
    assert not player.is_preparing()
    assert not temporary.exists()
    assert player.current is None
    player._ready = False
    player.close()


def test_close_cleans_playback_fallback_even_without_mixer(tmp_path):
    temporary = tmp_path / 'converted.ogg'
    temporary.write_bytes(b'tmp')
    player = MusicPlayer(spectrum_enabled=False)
    player._playback_fallback_path = temporary
    player.close()
    assert not temporary.exists()


def _controller(player):
    controller = DofusicController.__new__(DofusicController)
    controller.player = player
    controller.config = AppConfig()
    controller.logger = logging.getLogger('test.audio.retry')
    controller.state = SimpleNamespace(music='ancien', status='', display_theme='ancien')
    controller._audio_override_active = False
    controller._automatic_detection_unlocked = True
    controller._game_process_running = True
    controller._last_audio_retry_at = float('-inf')
    return controller


def test_no_soundtrack_stops_stale_automatic_music_but_not_manual(tmp_path):
    calls = []
    player = SimpleNamespace(stop=lambda **kwargs: calls.append(kwargs))
    controller = _controller(player)
    controller._desired_local_track = tmp_path / 'ancienne.opus'
    controller._play_track(None, status='ignored')
    assert calls == [{'fade_ms': controller.config.fade_ms}]
    assert controller.state.music == 'Aucune musique'
    controller._audio_override_active = True
    controller._play_track(None, status='ignored')
    assert len(calls) == 1


def test_playback_error_is_not_marked_success(tmp_path):
    player = SimpleNamespace(play=lambda *a, **kw: False, current=None)
    controller = _controller(player)
    track = tmp_path / 'piste_defaillante.opus'
    controller._play_track(track, status='Lieu confirmé')
    assert controller._desired_local_track == track
    assert controller.state.music == 'Aucune musique'
    assert 'Lecture audio impossible' in controller.state.status


def test_failed_music_is_retried_sparingly_without_rerolling(tmp_path):
    attempted = []
    track = tmp_path / 'combat.opus'
    player = SimpleNamespace(
        current=None,
        is_playing=lambda: False,
        play=lambda path, **kwargs: attempted.append(path) or False,
    )
    controller = _controller(player)
    controller._desired_local_track = track
    controller._retry_desired_audio(10.0)
    controller._retry_desired_audio(13.0)
    controller._retry_desired_audio(15.0)
    assert attempted == [track, track]
    controller._audio_override_active = True
    controller._retry_desired_audio(25.0)
    assert attempted == [track, track]


def test_partial_zone_suffix_is_held_but_real_shorter_name_confirms():
    continuity = ZoneTextContinuity(confirmations=2)
    continuity.confirm('Bois de Litneg')
    assert continuity.hold('Litneg') is True
    assert continuity.hold('Litneg') is False
    continuity.confirm('Litneg')
    assert continuity.confirmed == 'Litneg'
