from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dofusic.app import DofusicController
from dofusic.audio.player import MusicPlayer
from dofusic.config import AppConfig, load_config, save_config
import dofusic.vision.layout as layout


class _Worker:
    def __init__(self):
        self._alive = False
        self.started = 0
        self.closed = 0

    @property
    def alive(self):
        return self._alive

    def start(self):
        self.started += 1
        self._alive = True

    def close(self, **_kwargs):
        self.closed += 1
        self._alive = False

    def poll(self):
        return []


class _Capture:
    def __init__(self):
        self.calls = 0
        self.closed = 0

    def capture(self):
        self.calls += 1
        return None

    def close(self):
        self.closed += 1


class _Player:
    def __init__(self):
        self.current = None
        self.play_calls = []
        self.stop_calls = []
        self.muted = False

    def set_muted(self, value):
        self.muted = bool(value)

    def play(self, path, *, loop=True, restart=False):
        self.current = Path(path)
        self.play_calls.append((self.current, bool(loop), bool(restart)))
        return True

    def stop(self, *, fade_ms=0):
        self.stop_calls.append(int(fade_ms))
        self.current = None

    def set_volume(self, value):
        return int(value)

    def close(self):
        return None


class _Library:
    def __init__(self, fallback: Path):
        self.fallback = fallback
        self._fallback = fallback

    def resolve(self, location, *, combat=False, **_kwargs):
        if location is None and not combat:
            return self._fallback
        return None


def test_v26_defaults_enable_visualizer_loudness_and_audible_fades(tmp_path):
    cfg = AppConfig()

    assert cfg.config_version == 26
    assert cfg.show_visualizer is True
    assert cfg.normalize_loudness is True
    assert cfg.fade_ms >= 500

    old_path = tmp_path / 'config.json'
    old_path.write_text(json.dumps({
        'config_version': 25,
        'show_visualizer': False,
        'normalize_loudness': False,
        'fade_ms': 0,
    }), encoding='utf-8')
    migrated = load_config(old_path)
    assert migrated.config_version == 26
    assert migrated.show_visualizer is True
    assert migrated.normalize_loudness is True
    assert migrated.fade_ms >= 500

    migrated.show_visualizer = False
    migrated.normalize_loudness = False
    save_config(migrated, old_path)
    persisted = load_config(old_path)
    assert persisted.show_visualizer is False
    assert persisted.normalize_loudness is False


def test_blank_zone_crop_is_recognized_as_no_text_and_real_glyphs_as_text():
    blank = np.zeros((34, 96, 3), dtype=np.uint8)
    glyphs = blank.copy()
    glyphs[8:25, 8:10] = 255
    glyphs[8:25, 14:16] = 255
    glyphs[8:25, 20:22] = 255

    assert hasattr(layout, 'zone_crop_has_text')
    assert layout.zone_crop_has_text(blank) is False
    assert layout.zone_crop_has_text(glyphs) is True


def test_zone_overlay_width_freezes_until_ocr_confirms_changed_text():
    controller = DofusicController.__new__(DofusicController)
    controller.state = SimpleNamespace(zone_overlay_rect=(10, 40, 122, 34))
    controller._zone_overlay_width_px = 122
    controller._zone_overlay_refresh_requested = False

    # Transition noise can make the dynamic crop enormous; without a confirmed
    # OCR label the visible white outline must keep the old width.
    frozen_blank = controller._stable_zone_overlay_rect((10, 40, 500, 34), text_present=False)
    frozen_noise = controller._stable_zone_overlay_rect((10, 40, 500, 34), text_present=True)
    assert frozen_blank == (10, 40, 122, 34)
    assert frozen_noise == (10, 40, 122, 34)

    controller._zone_overlay_refresh_requested = True
    still_frozen = controller._stable_zone_overlay_rect((10, 40, 500, 34), text_present=False)
    refreshed = controller._stable_zone_overlay_rect((10, 40, 180, 34), text_present=True)

    assert still_frozen == (10, 40, 122, 34)
    assert refreshed == (10, 40, 180, 34)


def test_dofus_process_gate_starts_automation_only_when_game_exists_and_fades_on_close(tmp_path):
    running = {'value': False}
    capture = _Capture()
    zone_worker = _Worker()
    position_worker = _Worker()
    player = _Player()
    generic = tmp_path / 'Musique1.mp3'

    controller = DofusicController(
        AppConfig(fade_ms=700),
        music_library=_Library(generic),
        player=player,
        zone_worker=zone_worker,
        position_worker=position_worker,
        capture=capture,
        process_probe=lambda _name: running['value'],
    )

    controller.start()
    controller.tick(now=1.0)
    assert zone_worker.started == 0
    assert position_worker.started == 0
    assert capture.calls == 0
    assert player.play_calls == []

    running['value'] = True
    controller.tick(now=2.0)
    assert zone_worker.started == 1
    assert position_worker.started == 1
    assert capture.calls >= 1
    assert player.play_calls == [(generic, True, False)]

    # A valid position arms automation for this Dofus process session.
    controller._automatic_detection_unlocked = True
    controller.state.automatic_detection_unlocked = True

    running['value'] = False
    controller.tick(now=3.0)
    assert zone_worker.closed >= 1
    assert position_worker.closed >= 1
    assert player.stop_calls[-1] == 700
    assert controller.automatic_detection_unlocked is False
    assert controller.state.window_found is False


def test_closing_dofus_does_not_stop_a_manual_audio_override(tmp_path):
    running = {'value': True}
    capture = _Capture()
    player = _Player()
    generic = tmp_path / 'Musique.mp3'
    controller = DofusicController(
        AppConfig(fade_ms=650),
        music_library=_Library(generic),
        player=player,
        zone_worker=_Worker(),
        position_worker=_Worker(),
        capture=capture,
        process_probe=lambda _name: running['value'],
    )
    controller.start()
    controller.tick(now=1.0)

    manual = tmp_path / 'manual.mp3'
    assert controller.play_manual_local_track(manual, 'Manuel') is True
    stop_count = len(player.stop_calls)

    running['value'] = False
    controller.tick(now=2.0)

    assert len(player.stop_calls) == stop_count
    assert controller.audio_override_source == 'local'
    assert player.current == manual


def test_spectrum_analysis_only_applies_loudness_gain_when_normalization_is_enabled(tmp_path):
    track = tmp_path / 'track.mp3'

    disabled = MusicPlayer(spectrum_enabled=True, normalize_loudness=False)
    disabled.current = track
    disabled._spectrum_generation = 4
    disabled._publish_analysis(track, 4, np.zeros((1, 24), dtype=np.float32), 1000.0, 2.0)
    assert disabled._current_loudness_gain == 1.0
    assert disabled._loudness_gain_by_path[track] == 2.0

    enabled = MusicPlayer(spectrum_enabled=True, normalize_loudness=True)
    enabled.current = track
    enabled._spectrum_generation = 5
    enabled._publish_analysis(track, 5, np.zeros((1, 24), dtype=np.float32), 1000.0, 2.0)
    assert enabled._current_loudness_gain == 2.0


def test_manual_and_online_file_transitions_use_player_fade_in_and_fade_out(tmp_path):
    class _MusicChannel:
        def __init__(self):
            self.fadeouts = []
            self.loads = []
            self.plays = []
            self.volumes = []

        def fadeout(self, value):
            self.fadeouts.append(int(value))

        def load(self, value):
            self.loads.append(str(value))

        def set_volume(self, value):
            self.volumes.append(float(value))

        def play(self, loops, *, fade_ms=0):
            self.plays.append((int(loops), int(fade_ms)))

    channel = _MusicChannel()
    pygame = SimpleNamespace(mixer=SimpleNamespace(music=channel))
    player = MusicPlayer(fade_ms=650, spectrum_enabled=False, normalize_loudness=False)
    player._ready = True
    player._pygame = pygame
    player.current = tmp_path / 'old.mp3'

    target = tmp_path / 'next.mp3'
    assert player.play(target, loop=False, restart=True) is True

    assert channel.fadeouts == [650]
    assert channel.loads == [str(target)]
    assert channel.plays == [(0, 650)]


def test_settings_expose_visualizer_normalization_and_fade_controls():
    import inspect
    from dofusic.ui.settings_window import SettingsWindow
    from dofusic.ui.main_window import MainWindow

    settings_source = inspect.getsource(SettingsWindow._build) + inspect.getsource(SettingsWindow._apply)
    assert 'Afficher le visualiseur audio' in settings_source
    assert 'Normaliser le volume entre les morceaux' in settings_source
    assert 'Fondu audio en ms' in settings_source
    assert 'self.config.normalize_loudness' in settings_source
    assert 'self.config.fade_ms' in settings_source

    apply_source = inspect.getsource(MainWindow._apply_preferences)
    assert 'set_spectrum_enabled' in apply_source
    assert 'set_normalize_loudness' in apply_source
    assert 'player.fade_ms' in apply_source


def test_relaunching_dofus_rearms_coordinate_gate_and_restarts_generic(tmp_path):
    running = {'value': True}
    capture = _Capture()
    zone_worker = _Worker()
    position_worker = _Worker()
    player = _Player()
    generic = tmp_path / 'Musique2.mp3'
    controller = DofusicController(
        AppConfig(fade_ms=650),
        music_library=_Library(generic),
        player=player,
        zone_worker=zone_worker,
        position_worker=position_worker,
        capture=capture,
        process_probe=lambda _name: running['value'],
    )
    controller.start()
    controller.tick(now=1.0)
    controller._automatic_detection_unlocked = True
    controller.state.automatic_detection_unlocked = True

    running['value'] = False
    controller.tick(now=2.0)
    assert controller.automatic_detection_unlocked is False

    running['value'] = True
    controller.tick(now=3.0)
    assert zone_worker.started == 2
    assert position_worker.started == 2
    assert controller.automatic_detection_unlocked is False
    assert player.play_calls[-1] == (generic, True, False)
