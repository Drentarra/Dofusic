"""Regressions HUD / dungeon prioritization. No live Dofus process required."""
from types import SimpleNamespace

import numpy as np

from dofusic.app import DofusicController
from dofusic.models import LocationKind, LocationRecord
from dofusic.vision.combat import CombatObservation, CombatStateTracker
from dofusic.vision.layout import (
    HUDGeometry,
    HUDInputs,
    combat_toolbar_frame_width,
    tracking_screen_rects,
)


def _toolbar(width: int) -> np.ndarray:
    image = np.zeros((40, 320, 3), dtype=np.uint8)
    image[:37, :width] = (75, 65, 55)
    return image


def test_combat_overlay_shows_entire_visible_toolbar():
    collapsed = _toolbar(38)
    expanded = _toolbar(184)
    h1 = HUDInputs(zone=np.zeros((34, 50, 3), dtype=np.uint8),
                   position=np.zeros((29, 180, 3), dtype=np.uint8), combat=collapsed)
    h2 = HUDInputs(zone=h1.zone, position=h1.position, combat=expanded)
    args = dict(capture_screen_rect=(100, 200, 320, 100),
                capture_image_shape=(100, 320, 3), geometry=HUDGeometry())
    a = tracking_screen_rects(hud=h1, toolbar_buttons=1, **args).combat
    b = tracking_screen_rects(hud=h2, toolbar_buttons=6, **args).combat
    assert a[:2] == b[:2] == (100, 200)
    assert a[2] >= 35
    assert b[2] >= 175
    assert b[2] > a[2]
    assert a[3] == b[3] == 37


def test_combat_overlay_respects_ui_scale():
    assert combat_toolbar_frame_width(6, 1.0) == 208
    assert combat_toolbar_frame_width(6, 2.0) == 416


def test_dungeon_keeps_audio_priority_but_confirms_and_displays_combat(monkeypatch):
    from unittest.mock import Mock

    controller = DofusicController.__new__(DofusicController)
    location = LocationRecord(1, 'Grange du Tournesol Affamé', LocationKind.SUBAREA)
    controller.current_location = location
    controller.music_library = SimpleNamespace(
        dungeon_catalog=SimpleNamespace(is_dungeon=lambda value: value == location))
    controller.combat_tracker = CombatStateTracker(required_confirmations=2)
    controller.state = SimpleNamespace(in_combat=False, combat_confidence=0.0)
    controller.hud_geometry = HUDGeometry()
    controller.logger = Mock()
    controller._play_location = Mock()
    monkeypatch.setattr('dofusic.app.analyze_combat_toolbar',
                        lambda *_: CombatObservation(True, 0.95, 1))

    controller._update_combat_from_toolbar(None)
    assert controller.state.in_combat is False
    controller._update_combat_from_toolbar(None)
    assert controller.state.in_combat is True
    assert controller.combat_tracker.toolbar_buttons == 1
    assert controller.state.combat_confidence == 0.95
    controller._play_location.assert_not_called()

    monkeypatch.setattr('dofusic.app.analyze_combat_toolbar',
                        lambda *_: CombatObservation(False, 0.96, 2))
    controller._update_combat_from_toolbar(None)
    controller._update_combat_from_toolbar(None)
    assert controller.state.in_combat is False
    controller._play_location.assert_not_called()
