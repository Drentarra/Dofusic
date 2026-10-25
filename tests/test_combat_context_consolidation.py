"""A single combat tracker owns confirmation and the last reliable toolbar layout."""
import numpy as np
import pytest

from dofusic import app
from dofusic.vision.combat import CombatObservation, CombatStateTracker


def observe(state, buttons, confidence=.95):
    return CombatObservation(state, confidence, buttons)


def test_layout_and_combat_share_a_tracker_without_unknown_flicker():
    tracker = CombatStateTracker(required_confirmations=3)
    assert tracker.toolbar_buttons is None
    assert tracker.update(observe(False, 2)) is None
    assert tracker.toolbar_buttons == 2
    for count in (1, None, 6):
        assert tracker.update(observe(None, count, 0)) is None
        assert tracker.toolbar_buttons == 2
    for _ in range(3):
        tracker.update(observe(True, 6))
    assert tracker.in_combat is True
    assert tracker.toolbar_buttons == 6
    # Folding controls does not mean leaving combat.
    assert tracker.update(observe(True, 1)) is None
    assert tracker.in_combat is True
    assert tracker.toolbar_buttons == 1
    tracker.reset()
    assert tracker.toolbar_buttons is None
    assert tracker.in_combat is False


def test_dungeon_suspends_mode_but_recognizes_toolbar_shape():
    tracker = CombatStateTracker(required_confirmations=2, initial_state=False)
    for _ in range(6):
        assert tracker.update(observe(True, 6), enabled=False) is None
    assert tracker.in_combat is False
    assert tracker.toolbar_buttons == 6
    assert tracker.update(observe(True, 1)) is None
    assert tracker.in_combat is False
    assert tracker.update(observe(True, 1)) is True
    assert tracker.toolbar_buttons == 1


def test_controller_toolbar_state_is_owned_by_combat_tracker():
    controller = app.DofusicController.__new__(app.DofusicController)
    controller.combat_tracker = CombatStateTracker()
    controller.combat_tracker.toolbar_buttons = 2
    assert controller.combat_tracker.toolbar_buttons == 2
    controller.combat_tracker.reset()
    assert controller.combat_tracker.toolbar_buttons is None

@pytest.mark.parametrize('sequence', [
    # A confirmed transition always needs three valid frames, regardless of HUD folding.
    ((2, 2, 2), (4, 4, 4), (3, 3, 3), (1, 1, 1)),
    ((1, 1, 1), (3, 3, 3), (4, 4, 4), (2, 2, 2)),
])
def test_reference_actual_toolbar_and_combat_context_share_evidence(sequence):
    from pathlib import Path

    import cv2

    from dofusic.vision.combat import analyze_combat_toolbar
    from dofusic.vision.layout import HUDGeometry, tracking_screen_rects
    from tools.hud_reference import extract_reference_hud_inputs as extract_hud_inputs

    tracker = CombatStateTracker(required_confirmations=3)
    root = Path(__file__).parent / 'fixtures' / 'combat_reference'
    geometry = HUDGeometry()
    expected_buttons = {1: 6, 2: 2, 3: 1, 4: 6}
    previous_buttons = None
    for group in sequence:
        for index, number in enumerate(group):
            frame = cv2.imdecode(np.fromfile(str(root / f'{number}.png'), np.uint8), cv2.IMREAD_COLOR)
            assert frame is not None
            hud = extract_hud_inputs(frame, geometry)
            observation = analyze_combat_toolbar(hud.combat)
            tracker.update(observation)
            if number == 3 and index < 2 and previous_buttons is not None and not tracker.in_combat:
                # An absence-inferred one-button state must NOT shrink the
                # trusted overlay during a still-unconfirmed transition.
                assert tracker.toolbar_buttons == previous_buttons
            else:
                assert tracker.toolbar_buttons == expected_buttons[number]
            if index == 2:
                assert tracker.in_combat == (number >= 3)
            overlays = tracking_screen_rects(
                capture_screen_rect=(0, 0, frame.shape[1], frame.shape[0]),
                capture_image_shape=frame.shape, hud=hud, geometry=geometry,
                toolbar_buttons=tracker.toolbar_buttons,
            )
            assert overlays.combat[2] > 0
        assert tracker.in_combat == (group[-1] >= 3)
        previous_buttons = expected_buttons[group[-1]]


def test_unknown_or_low_confidence_does_not_replace_published_visual_layout():
    tracker = CombatStateTracker(required_confirmations=3)
    tracker.update(observe(False, 2))
    tracker.update(observe(True, 1, .59))
    assert tracker.toolbar_buttons == 2
    tracker.update(observe(None, 1, .99))
    assert tracker.toolbar_buttons == 2
