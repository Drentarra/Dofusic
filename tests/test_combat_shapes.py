"""Public regressions use synthetic HUDs; user screenshots remain private."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import CombatObservation, CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import estimate_hud_transform, extract_hud_inputs
from dofusic.vision.combat_shapes import GLYPH_ROWS


def collapsed_toolbar(background=(65, 60, 55), foreground=(200, 200, 200), *, scenery=(0, 0, 0), neighbour=None):
    """Draw a generic plus using vector lines, independently of glyph masks."""
    image = np.full((105, 360, 3), scenery, dtype=np.uint8)
    image[:37, :37] = background
    cv2.line(image, (12, 18), (25, 18), foreground, 1)
    cv2.line(image, (18, 12), (18, 25), foreground, 1)
    if neighbour is not None:
        image[:37, 37:72] = background
        for y, row in enumerate(GLYPH_ROWS[neighbour]):
            for column in range(25):
                if (row >> column) & 1:
                    image[y + 6, column + 42] = foreground
    return image


def expanded_toolbar(*, combat=True, scenery=(0, 0, 0)):
    """Render known HUD glyphs over a scene, without running the classifier."""
    image = np.full((150, 360, 3), scenery, dtype=np.uint8)
    image[:37, :206] = (83, 58, 55)
    names = ('minus', 'combat_eye', 'diamond', 'heart', 'fighter', 'skull') if combat else (
        'minus', 'sword', 'dots', 'info', 'eye', 'havresac_gear',
    )
    for x, name in zip((6, 42, 76, 110, 144, 178), names):
        for y, row in enumerate(GLYPH_ROWS[name]):
            for column in range(25):
                if (row >> column) & 1:
                    image[y + 6, x + column] = (230, 230, 230)
    return image


@pytest.mark.parametrize('combat', [True, False])
@pytest.mark.parametrize('scenery', [(20, 150, 115), (185, 195, 220), (55, 55, 55)])
def test_expanded_toolbar_does_not_require_black_scenery(combat, scenery):
    image = expanded_toolbar(combat=combat, scenery=scenery)
    observation = analyze_combat_toolbar(image[:40, :320])
    assert observation.in_combat is combat
    assert observation.confidence >= 0.60


@pytest.mark.parametrize('scale', [0.75, 0.9, 1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('scenery', [(20, 150, 115), (185, 195, 220), (55, 55, 55)])
def test_calibration_uses_toolbar_background_instead_of_scene(scale, scenery):
    image = cv2.resize(expanded_toolbar(scenery=scenery), None, fx=scale, fy=scale)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert transform.scale == pytest.approx(scale, abs=0.04)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


@pytest.mark.parametrize('scale', [0.75, 0.8, 0.9, 1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('scenery', [(20, 150, 115), (185, 195, 220), (55, 55, 55)])
def test_collapsed_combat_is_detected_on_colored_scenery(scale, scenery):
    image = cv2.resize(collapsed_toolbar(scenery=scenery), None, fx=scale, fy=scale)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


@pytest.mark.parametrize('neighbour', ['eye', 'havresac_gear'])
@pytest.mark.parametrize('scale', [0.75, 0.9, 1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('scenery', [(0, 0, 0), (20, 150, 115), (185, 195, 220)])
def test_collapsed_exploration_icons_take_priority_over_plus(neighbour, scale, scenery):
    image = cv2.resize(collapsed_toolbar(scenery=scenery, neighbour=neighbour), None, fx=scale, fy=scale)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is False


@pytest.mark.parametrize('neighbour,cover_from', [('eye', 57), ('havresac_gear', 61)])
def test_partially_covered_exploration_icon_cannot_start_combat(neighbour, cover_from):
    image = collapsed_toolbar(scenery=(20, 150, 115), neighbour=neighbour)
    image[:37, cover_from:72] = (95, 85, 80)
    observation = analyze_combat_toolbar(extract_hud_inputs(image).combat)
    assert observation.in_combat is None
    tracker = CombatStateTracker()
    for _ in range(3):
        assert tracker.update(observation) is None
    assert tracker.in_combat is False


def test_covered_icon_keeps_expanded_toolbar_unknown_on_colored_scene():
    image = expanded_toolbar(scenery=(20, 150, 115))
    image[:37, 72:106] = (95, 85, 80)
    assert analyze_combat_toolbar(image[:40, :320]).in_combat is None


def test_changing_scene_cannot_block_collapsed_combat_decision():
    image = collapsed_toolbar()
    assert analyze_combat_toolbar(image[:40, :320]).in_combat is True
    image[:, 43:] = (20, 150, 115)
    assert analyze_combat_toolbar(image[:40, :320]).in_combat is True


@pytest.mark.parametrize('collapsed', [False, True])
def test_colored_scene_combat_changes_music_and_exploration_restores_it(tmp_path, collapsed):
    import logging
    from dofusic.app import ControllerState, DofusicController
    from dofusic.audio.library import MusicLibrary

    normal = tmp_path / 'Musique.opus'
    battle = tmp_path / 'Musique Combat.opus'
    normal.touch()
    battle.touch()
    played = []
    controller = DofusicController.__new__(DofusicController)
    controller.state = ControllerState()
    controller.hud_geometry = None
    controller.combat_tracker = CombatStateTracker(required_confirmations=3)
    controller.current_location = None
    controller.logger = logging.getLogger('combat-test')
    controller._game_process_running = True
    controller.music_library = MusicLibrary(SimpleNamespace(all_locations=lambda: ()), tmp_path)
    controller.player = SimpleNamespace(play=lambda path: played.append(path) or True)
    combat = expanded_toolbar(scenery=(20, 150, 115))[:40, :320]
    normal_hud = expanded_toolbar(combat=False, scenery=(185, 195, 220))[:40, :320]
    if collapsed:
        combat = collapsed_toolbar(scenery=(20, 150, 115))[:40, :320]
        normal_hud = collapsed_toolbar(scenery=(185, 195, 220), neighbour='eye')[:40, :320]

    for _ in range(2):
        controller._update_combat_from_toolbar(combat)
    assert played == []
    controller._update_combat_from_toolbar(combat)
    assert controller.state.in_combat is True
    assert played == [battle]
    for _ in range(3):
        controller._update_combat_from_toolbar(normal_hud)
    assert controller.state.in_combat is False
    assert played == [battle, normal]


@pytest.mark.parametrize('background,foreground', [
    ((65, 60, 55), (200, 200, 200)),
    ((190, 195, 200), (55, 55, 55)),
    ((35, 55, 40), (75, 210, 130)),
    ((25, 24, 22), (78, 78, 78)),
])
def test_collapsed_combat_recognizes_plus_shape_across_themes(background, foreground):
    observation = analyze_combat_toolbar(extract_hud_inputs(collapsed_toolbar(background, foreground)).combat)
    assert observation.in_combat is True
    assert observation.confidence >= 0.60


@pytest.mark.parametrize('scale', [0.75, 0.76, 0.8, 0.81, 0.9, 1.1, 1.25, 1.5, 1.75, 2.0])
def test_single_button_calibration_keeps_combat_detectable(scale):
    image = cv2.resize(collapsed_toolbar(), None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    observation = analyze_combat_toolbar(extract_hud_inputs(image).combat)
    assert observation.in_combat is True
    assert observation.confidence >= 0.60


@pytest.mark.parametrize('scale', [0.75, 1.0, 1.25, 1.5, 2.0])
def test_dark_toolbar_still_stops_at_black_scene(scale):
    image = cv2.resize(collapsed_toolbar(background=(20, 20, 20)), None, fx=scale, fy=scale)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert transform.scale == pytest.approx(scale, abs=0.04)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


@pytest.mark.parametrize('left,right', [(38, 320), (312, 320)])
def test_unrelated_panel_beside_plus_does_not_block_detection(left, right):
    image = collapsed_toolbar()
    image[:37, left:right] = (90, 70, 50)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


def test_covered_plus_cannot_start_combat():
    image = collapsed_toolbar(scenery=(20, 150, 115))
    image[:37, :37] = (95, 85, 80)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is None


def test_inventory_covering_toolbar_is_unknown():
    image = collapsed_toolbar()
    image[:40, :320] = (75, 90, 110)
    cv2.putText(image, 'INVENTAIRE', (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (210, 230, 250), 1)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is None


@pytest.mark.parametrize('image', [None, np.zeros((40, 320, 3), np.uint8), np.zeros((10, 12, 3), np.uint8)])
def test_missing_hud_is_unknown(image):
    assert analyze_combat_toolbar(image).in_combat is None


def test_unknown_frames_preserve_confirmed_state_and_reset_transition():
    tracker = CombatStateTracker(required_confirmations=3)
    combat = CombatObservation(True, 0.95, 0.95, 0, 0)
    exploration = CombatObservation(False, 0.95, 0.95, 0, 0)
    hidden = CombatObservation(None, 0, 0, 0, 0)
    assert tracker.update(combat) is None
    assert tracker.update(combat) is None
    assert tracker.update(combat) is True
    assert tracker.update(exploration) is None
    assert tracker.update(hidden) is None
    assert tracker.in_combat is True
    assert tracker.update(exploration) is None
    assert tracker.update(exploration) is None
    assert tracker.update(exploration) is False


def test_low_confidence_cannot_change_stable_combat_state():
    tracker = CombatStateTracker(initial_state=True)
    uncertain = CombatObservation(False, 0.59, 0.59, 0, 0)
    for _ in range(5):
        assert tracker.update(uncertain) is None
    assert tracker.in_combat is True


def test_missing_capture_breaks_pending_confirmation():
    from dofusic.app import ControllerState, DofusicController
    from dofusic.capture.service import CaptureSnapshot

    controller = DofusicController.__new__(DofusicController)
    controller.started = True
    controller._sync_game_process = lambda now: True
    controller._poll_workers = lambda now: None
    controller._automatic_detection_unlocked = True
    controller.hud_geometry = None
    controller.config = SimpleNamespace(capture_missing_grace_sec=1.2)
    controller.state = ControllerState()
    controller.combat_tracker = CombatStateTracker()
    controller._last_capture_sequence = 1
    controller.last_capture_seen_at = 10.0
    controller.capture_service = SimpleNamespace(latest=lambda sequence, now: CaptureSnapshot(2, None, now))
    observation = CombatObservation(True, 0.95, 0.95, 0, 0)
    controller.combat_tracker.update(observation)
    controller.combat_tracker.update(observation)
    controller.tick(now=10.1)
    assert controller.combat_tracker.update(observation) is None
    assert controller.combat_tracker.in_combat is False
