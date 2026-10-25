"""Four-state HUD classification: tested independently of any fixed resolution.

Regression images are the existing public cropped-toolbar fixtures; no
personal user screenshots, map images or new private material are distributed.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDTransform, extract_hud_inputs
from tools.hud_reference import estimate_hud_transform

FIXTURES = Path(__file__).parent / 'fixtures' / 'combat_reference'


def _toolbar(number: int, factor: float = 1.0) -> np.ndarray:
    frame = cv2.imread(str(FIXTURES / f'{number}.png'))
    assert frame is not None
    reference = estimate_hud_transform(frame)
    assert reference is not None
    if factor != 1:
        frame = cv2.resize(frame, None, fx=factor, fy=factor, interpolation=cv2.INTER_LINEAR)
    transform = HUDTransform(
        scale=reference.scale * factor,
        origin_x=round(reference.origin_x * factor),
        origin_y=round(reference.origin_y * factor),
    )
    return extract_hud_inputs(frame, transform=transform).combat


@pytest.mark.parametrize('factor', [.60, .6216, .65, .70, .811, .875, 1., 1.25])
@pytest.mark.parametrize('number,state,buttons,name', [
    (1, False, 6, 'exploration_expanded'),
    (2, False, 2, 'exploration_collapsed'),
    (3, True, 1, 'combat_collapsed'),
    (4, True, 6, 'combat_expanded'),
])
def test_four_configurations_across_fractional_scales(number, state, buttons, name, factor):
    observed = analyze_combat_toolbar(_toolbar(number, factor))
    assert (observed.in_combat, observed.toolbar_buttons, observed.configuration) == (state, buttons, name)
    assert observed.confidence >= .60


def test_overlay_recognizes_unfolded_bar_when_one_combat_icon_is_occluded():
    toolbar = _toolbar(4).copy()
    # One original icon is hidden by another UI element. This removes the
    # complete ordered combat signature, but leaves most of the six controls.
    toolbar[6:31, 76:101] = tuple(int(i) for i in np.median(toolbar[2:5, 77:100], axis=(0, 1)))
    observed = analyze_combat_toolbar(toolbar)
    assert observed.in_combat is None
    assert observed.toolbar_buttons == 6
    assert observed.layout_confidence >= .60
    tracker = CombatStateTracker(required_confirmations=3, initial_state=False)
    assert tracker.update(observed) is None
    assert tracker.toolbar_buttons == 6
    assert tracker.in_combat is False  # Layout evidence must not start combat.


def test_layout_update_does_not_end_a_confirmed_fight():
    tracker = CombatStateTracker(required_confirmations=3, initial_state=True)
    observed = analyze_combat_toolbar(_toolbar(4).copy())
    tracker.update(observed)
    assert tracker.toolbar_buttons == 6
    toolbar = _toolbar(4).copy()
    toolbar[6:31, 76:101] = tuple(int(i) for i in np.median(toolbar[2:5, 77:100], axis=(0, 1)))
    unknown = analyze_combat_toolbar(toolbar)
    for _ in range(7):
        assert tracker.update(unknown) is None
    assert tracker.in_combat is True
    assert tracker.toolbar_buttons == 6


def test_unknown_folded_exploration_preserves_mode_but_sets_two_button_layout():
    toolbar = _toolbar(2).copy()
    toolbar[6:31, 42:67] = tuple(int(i) for i in np.median(toolbar[2:5, 42:66], axis=(0, 1)))
    observed = analyze_combat_toolbar(toolbar)
    assert observed.in_combat is None
    assert observed.toolbar_buttons == 2
    assert observed.layout_confidence >= .60
    tracker = CombatStateTracker(initial_state=True)
    assert tracker.update(observed) is None
    assert tracker.toolbar_buttons == 2
    assert tracker.in_combat is True


def test_short_toolbar_with_distant_matching_background_is_not_six_buttons():
    toolbar = _toolbar(3).copy()
    toolbar[4:32, 76:205] = tuple(int(i) for i in np.median(toolbar[2:5, 5:30], axis=(0, 1)))
    observed = analyze_combat_toolbar(toolbar)
    # Distant scenery and decorative panels alone cannot publish a six-button
    # layout. Correct state is only inferred from a verified near-slot proof.
    assert observed.in_combat is True
    assert observed.toolbar_buttons == 1


def test_1280_like_scale_exits_combat_without_retracting():
    tracker = CombatStateTracker(required_confirmations=3)
    for _ in range(3):
        tracker.update(analyze_combat_toolbar(_toolbar(4, .6216)))
    assert tracker.in_combat is True
    for _ in range(2):
        assert tracker.update(analyze_combat_toolbar(_toolbar(1, .6216))) is None
    assert tracker.update(analyze_combat_toolbar(_toolbar(1, .6216))) is False
    assert tracker.in_combat is False
    assert tracker.toolbar_buttons == 6
