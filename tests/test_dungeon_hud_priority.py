"""Regressions HUD / dungeon prioritization. No live Dofus process required."""
from types import SimpleNamespace

import numpy as np

from dofusic.app import DofusicController
from dofusic.models import LocationKind, LocationRecord
from dofusic.vision.combat import CombatObservation, CombatStateTracker
from dofusic.vision.layout import (
    HUDGeometry, HUDInputs, HUDTransform, combat_toolbar_frame_width,
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
    bar = _toolbar(184)
    assert combat_toolbar_frame_width(6, 1.0) == 214
    assert combat_toolbar_frame_width(6, 2.0) == 428


def test_dungeon_skips_combat_recognition_and_keeps_state_stable():
    controller = DofusicController.__new__(DofusicController)
    location = LocationRecord(1, 'Grange du Tournesol Affamé', LocationKind.SUBAREA)
    controller.current_location = location
    controller.music_library = SimpleNamespace(
        dungeon_catalog=SimpleNamespace(is_dungeon=lambda value: value == location))
    controller.combat_tracker = CombatStateTracker(required_confirmations=2, initial_state=True)
    controller.state = SimpleNamespace(in_combat=True, combat_confidence=0.9)
    controller._combat_suspended_for_dungeon = False
    controller.hud_geometry = HUDGeometry()

    for _ in range(10):
        controller._update_combat_from_toolbar(None)
    assert controller.state.in_combat is False
    assert controller.combat_tracker.in_combat is False
    assert controller._combat_suspended_for_dungeon is True

    # Exiting a confirmed dungeon makes normal recognition available again.
    controller.current_location = LocationRecord(2, 'Astrub', LocationKind.SUBAREA)
    controller._update_combat_from_toolbar(None)
    assert controller._combat_suspended_for_dungeon is False
    assert controller.combat_tracker.in_combat is False
