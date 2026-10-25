"""Temporary unknown button counts must not distort a confirmed overlay."""
import logging
from types import SimpleNamespace

import numpy as np

from dofusic import app
from dofusic.vision.combat import CombatObservation, CombatStateTracker
from dofusic.vision.layout import HUDGeometry


def test_unknown_one_button_does_not_shrink_known_two_button_overlay(monkeypatch):
    controller = app.DofusicController.__new__(app.DofusicController)
    controller.hud_geometry = HUDGeometry()
    controller.combat_tracker = CombatStateTracker(required_confirmations=3)
    controller.state = SimpleNamespace(in_combat=False, combat_confidence=0)
    controller.logger = logging.getLogger('test.combat-unknown-button')
    controller.combat_tracker.toolbar_buttons = 2
    controller._combat_suspended_for_dungeon = False
    controller._is_dungeon_context = lambda: False
    uncertain = CombatObservation(None, 0, 1)
    monkeypatch.setattr(app, 'analyze_combat_toolbar', lambda image, geometry: uncertain)
    controller._update_combat_from_toolbar(np.zeros((40, 320, 3), dtype=np.uint8))
    assert controller.combat_tracker.toolbar_buttons == 2
    assert controller.combat_tracker.in_combat is False


def test_confident_one_button_can_update_overlay(monkeypatch):
    controller = app.DofusicController.__new__(app.DofusicController)
    controller.hud_geometry = HUDGeometry()
    controller.combat_tracker = CombatStateTracker(required_confirmations=3)
    controller.state = SimpleNamespace(in_combat=False, combat_confidence=0)
    controller.logger = logging.getLogger('test.combat-confirmed-button')
    controller.combat_tracker.toolbar_buttons = 2
    controller._combat_suspended_for_dungeon = False
    controller._is_dungeon_context = lambda: False
    known = CombatObservation(True, 0.94, 1)
    monkeypatch.setattr(app, 'analyze_combat_toolbar', lambda image, geometry: known)
    controller._update_combat_from_toolbar(np.zeros((40, 320, 3), dtype=np.uint8))
    assert controller.combat_tracker.toolbar_buttons == 1
