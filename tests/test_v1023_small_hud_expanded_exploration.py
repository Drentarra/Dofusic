"""Stable combat exits when HUD icons lose pixels at fractional window scales.

Uses the pre-existing public regression crops, never a user's private screenshot.
The fix must recognize an *ordered* exploration signature, not infer a combat
exit from lost/occluded combat icons or from a resolution-specific exception.
"""
from pathlib import Path

import cv2
import pytest

from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDTransform, extract_hud_inputs
from tools.hud_reference import estimate_hud_transform

FIXTURES = Path(__file__).parent / 'fixtures' / 'combat_reference'


def _scaled_observation(image_id: int, relative_scale: float):
    raw = cv2.imread(str(FIXTURES / f'{image_id}.png'))
    assert raw is not None
    reference = estimate_hud_transform(raw)
    assert reference is not None
    scaled = cv2.resize(raw, None, fx=relative_scale, fy=relative_scale, interpolation=cv2.INTER_LINEAR)
    transform = HUDTransform(
        scale=reference.scale * relative_scale,
        origin_x=round(reference.origin_x * relative_scale),
        origin_y=round(reference.origin_y * relative_scale),
    )
    hud = extract_hud_inputs(scaled, transform=transform)
    return analyze_combat_toolbar(hud.combat)


@pytest.mark.parametrize('relative_scale', [0.70, 0.75, 0.811, 0.838, 0.875, 1.0])
def test_expanded_exploration_is_recognized_at_multiple_fractional_scales(relative_scale):
    observed = _scaled_observation(1, relative_scale)
    assert observed.in_combat is False
    assert observed.toolbar_buttons == 6
    assert observed.confidence >= 0.60


@pytest.mark.parametrize('relative_scale', [0.70, 0.75, 0.811, 0.838, 0.875, 1.0])
def test_expanded_combat_remains_combat_at_fractional_scales(relative_scale):
    observed = _scaled_observation(4, relative_scale)
    assert observed.in_combat is True
    assert observed.toolbar_buttons == 6


def test_combat_exits_without_collapsing_expanded_toolbar():
    tracker = CombatStateTracker(required_confirmations=3)
    for _ in range(3):
        tracker.update(_scaled_observation(4, 0.811))
    assert tracker.in_combat is True
    for i in range(2):
        assert tracker.update(_scaled_observation(1, 0.811)) is None
        assert tracker.in_combat is True
    assert tracker.update(_scaled_observation(1, 0.811)) is False
    assert tracker.in_combat is False
    assert tracker.toolbar_buttons == 6


def test_obscured_small_hud_cannot_forcibly_leave_combat():
    raw = cv2.imread(str(FIXTURES / '1.png'))
    transform = estimate_hud_transform(raw)
    assert transform is not None
    small = cv2.resize(raw, None, fx=.811, fy=.811, interpolation=cv2.INTER_LINEAR)
    actual = HUDTransform(scale=transform.scale*.811)
    toolbar = extract_hud_inputs(small, transform=actual).combat.copy()
    # Cover the distinctive second exploration glyph: no ordered proof exists.
    toolbar[:, round(35*actual.scale):round(76*actual.scale)] = (82, 83, 89)
    obs = analyze_combat_toolbar(toolbar)
    assert obs.in_combat is None
    tracker = CombatStateTracker(initial_state=True)
    for _ in range(10):
        assert tracker.update(obs) is None
    assert tracker.in_combat is True
