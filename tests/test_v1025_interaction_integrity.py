"""Adversarial tests: input effects are not evidence of game-state changes."""
import logging
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from dofusic import app
from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.interaction_guard import ToolbarInteractionGuard
from dofusic.vision.layout import HUDTransform, extract_hud_inputs
from tools.hud_reference import estimate_hud_transform

FIXTURES = Path(__file__).parent / 'fixtures' / 'combat_reference'


def toolbar(index: int, scale: float):
    frame = cv2.imread(str(FIXTURES / f'{index}.png'))
    assert frame is not None
    ref = estimate_hud_transform(frame)
    assert ref is not None
    if scale != 1.0:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    return extract_hud_inputs(frame, transform=HUDTransform(scale=ref.scale * scale,
        origin_x=round(ref.origin_x * scale), origin_y=round(ref.origin_y * scale))).combat


@pytest.mark.parametrize('scale', [.6216, .811, .8378, 1.0, 1.25])
def test_repeated_hover_cannot_steal_zone_music_or_shrink_overlay(scale):
    """Even a perfect one-button-looking input is not game evidence while hovered."""
    tracker = CombatStateTracker(required_confirmations=3, min_inferred_transition_seconds=.4)
    for _ in range(3):
        tracker.update(analyze_combat_toolbar(toolbar(2, scale)), now=0)
    assert (tracker.in_combat, tracker.toolbar_buttons) == (False, 2)
    falsely_one_button = analyze_combat_toolbar(toolbar(3, scale))
    assert falsely_one_button.in_combat is True
    assert falsely_one_button.evidence_type == 'absence'
    guard = ToolbarInteractionGuard(release_grace_s=.35)
    for i in range(40):
        now = i * .10
        # Pointer touches a collapsed toolbar for four seconds.
        assert guard.observe(cursor=(15, 12), bounds=(0, 0, 320*scale, 40*scale), now=now)
        assert tracker.update(falsely_one_button, disturbed=True, now=now) is None
        assert (tracker.in_combat, tracker.toolbar_buttons) == (False, 2)
    for now in (4.0, 4.1, 4.2):
        assert guard.observe(cursor=(500, 500), bounds=(0, 0, 320*scale, 40*scale), now=now)
        assert tracker.update(falsely_one_button, disturbed=True, now=now) is None
    # Once the hover ends the true 2-button exploration scene remains stable.
    assert not guard.observe(cursor=(500, 500), bounds=(0, 0, 320*scale, 40*scale), now=4.40)
    for now in (4.4, 4.5, 4.6):
        assert tracker.update(analyze_combat_toolbar(toolbar(2, scale)), now=now) is None
    assert (tracker.in_combat, tracker.toolbar_buttons) == (False, 2)


def test_inferred_combat_needs_persistent_distinct_evidence_not_just_three_frames():
    tracker = CombatStateTracker(required_confirmations=3, min_inferred_transition_seconds=.4)
    for _ in range(3):
        tracker.update(analyze_combat_toolbar(toolbar(2, 1.0)), now=0)
    implicit = analyze_combat_toolbar(toolbar(3, 1.0))
    assert implicit.evidence_type == 'absence'
    assert implicit.confidence <= .82
    for now in (.0, .1, .2, .3):
        assert tracker.update(implicit, now=now) is None
        assert (tracker.in_combat, tracker.toolbar_buttons) == (False, 2)
    assert tracker.update(implicit, now=.41) is True
    assert (tracker.in_combat, tracker.toolbar_buttons) == (True, 1)
    # A positively recognized return to exploration remains quick.
    for now in (.5, .6):
        assert tracker.update(analyze_combat_toolbar(toolbar(2, 1.0)), now=now) is None
    assert tracker.update(analyze_combat_toolbar(toolbar(2, 1.0)), now=.7) is False
    assert (tracker.in_combat, tracker.toolbar_buttons) == (False, 2)


def test_different_evidence_class_must_restart_pending_confirmation():
    from dofusic.vision.combat import CombatObservation
    tracker = CombatStateTracker(required_confirmations=3, min_inferred_transition_seconds=.4)
    direct = CombatObservation(True, .9, 6, .9, 'combat_expanded', 'direct')
    inferred = CombatObservation(True, .75, 1, .75, 'combat_collapsed', 'absence')
    tracker.update(direct, now=0)
    tracker.update(direct, now=.1)
    assert tracker.update(inferred, now=.2) is None
    assert tracker.update(inferred, now=.3) is None
    assert tracker.update(inferred, now=.4) is None
    assert tracker.in_combat is False
    assert tracker.update(inferred, now=.61) is True


def test_pointer_guard_works_with_screen_space_and_release_window(monkeypatch):
    controller = app.DofusicController.__new__(app.DofusicController)
    controller.logger = logging.getLogger('test.guard')
    from dofusic.vision.layout import HUDGeometry
    controller.hud_geometry = HUDGeometry()
    frame = SimpleNamespace(screen_rect=(200, 100, 800, 100))
    hud = SimpleNamespace(transform=HUDTransform(scale=.625))
    monkeypatch.setattr(app, 'screen_cursor_position', lambda: (225, 110))
    assert controller._toolbar_pointer_interference(frame, hud, now=0.)
    monkeypatch.setattr(app, 'screen_cursor_position', lambda: (700, 600))
    assert controller._toolbar_pointer_interference(frame, hud, now=.10)
    assert not controller._toolbar_pointer_interference(frame, hud, now=.36)


def test_hovered_collapsed_combat_cannot_fake_combat_exit():
    tracker = CombatStateTracker(initial_state=True, min_inferred_transition_seconds=.4)
    tracker.toolbar_buttons = 1
    explored = analyze_combat_toolbar(toolbar(2, .811))
    for i in range(20):
        assert tracker.update(explored, disturbed=True, now=i*.1) is None
    assert (tracker.in_combat, tracker.toolbar_buttons) == (True, 1)
