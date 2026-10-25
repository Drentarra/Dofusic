"""Regressions from user-provided October 2026 Dofus HUD corner crops.

Only top-left HUD strips are retained, not personal chat/player screenshots.
Capture/UI geometry is measured in image pixels and mapped to screen pixels.
Neither a 1920x1080 fixed width nor names of maps are used by the algorithm.
"""
from pathlib import Path

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDGeometry, tracking_screen_rects
from tools.hud_reference import HUDTransformSmoother, estimate_hud_transform, measure_toolbar_rim
from tools.hud_reference import extract_reference_hud_inputs as extract_hud_inputs

CORNER = Path(__file__).parent / 'fixtures' / 'hud'
SCENES = (
    ('xelorium_combat_corner.png', (0, 0, 205, 37), True, 6),
    ('havresac_expanded_corner.png', (56, 42, 305, 37), False, 6),
    ('havresac_collapsed_corner.png', (0, 0, 206, 37), False, 2),
)


def read_image(name: str) -> np.ndarray:
    # OpenCV's imread(path) is not Unicode-safe on all Windows installations.
    raw = np.fromfile(str(CORNER / name), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert image is not None
    return image


@pytest.mark.parametrize('name,expected,combat,buttons', SCENES)
def test_measures_real_corner_and_keeps_fixed_hud_line_positions(name, expected, combat, buttons):
    image = read_image(name)
    x, y, width, height = expected
    assert measure_toolbar_rim(image) == (x, y, x + width, y + height)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert transform.origin_x == x
    assert transform.origin_y == y
    assert transform.scale == pytest.approx(1., abs=.02)

    hud = extract_hud_inputs(image, transform=transform)
    assert hud.toolbar_width == width
    assert hud.zone.shape[0] == HUDGeometry().zone_height
    assert hud.position.shape == (HUDGeometry().position_height, 180, 3)
    observed = analyze_combat_toolbar(hud.combat)
    assert observed.in_combat is combat
    assert observed.toolbar_buttons == buttons
    screen_left, screen_top = 200, 130
    rects = tracking_screen_rects(
        capture_screen_rect=(screen_left, screen_top, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape,
        hud=hud, toolbar_buttons=observed.toolbar_buttons,
    )
    assert rects.combat == (screen_left + x, screen_top + y, width, height)
    # OCR origins/heights remain *fixed relative to the calibrated HUD*.
    assert rects.zone[:2] == (screen_left + x, screen_top + y + 40)
    assert rects.zone[3] == 34
    assert rects.position == (screen_left + x, screen_top + y + 69, 180, 29)


@pytest.mark.parametrize('scale', (.6, .8, 1.0, 1.25, 1.5))
@pytest.mark.parametrize('name,expected,combat,buttons', SCENES)
def test_toolbar_right_edge_only_varies_with_real_scale(name, expected, combat, buttons, scale):
    image = read_image(name)
    scaled = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    transform = estimate_hud_transform(scaled)
    assert transform is not None
    assert transform.scale == pytest.approx(scale, abs=.04)
    assert transform.origin_x == pytest.approx(expected[0] * scale, abs=2)
    assert transform.origin_y == pytest.approx(expected[1] * scale, abs=2)
    hud = extract_hud_inputs(scaled, transform=transform)
    assert hud.toolbar_width == pytest.approx(expected[2] * scale, abs=4)
    rects = tracking_screen_rects(
        capture_screen_rect=(0, 0, scaled.shape[1], scaled.shape[0]),
        capture_image_shape=scaled.shape, hud=hud, toolbar_buttons=buttons,
    )
    assert rects.combat[0] == transform.origin_x
    assert rects.combat[1] == transform.origin_y
    assert rects.combat[2] == hud.toolbar_width
    assert rects.combat[3] == round(37 * transform.scale)
    assert rects.zone[1] == transform.point(0, 40)[1]
    assert rects.position[1] == transform.point(0, 69)[1]


def test_xelorium_combat_switches_tracker_instead_of_remaining_unknown():
    image = read_image('xelorium_combat_corner.png')
    hud = extract_hud_inputs(image)
    observed = analyze_combat_toolbar(hud.combat)
    assert observed.in_combat is True
    tracker = CombatStateTracker(required_confirmations=3)
    assert tracker.update(observed) is None
    assert tracker.update(observed) is None
    assert tracker.update(observed) is True


def test_hud_transform_stays_stable_and_accepts_real_relocation():
    normal = read_image('havresac_collapsed_corner.png')
    shifted = read_image('havresac_expanded_corner.png')
    smoother = HUDTransformSmoother(confirmations=2)
    first = smoother.update(estimate_hud_transform(normal))
    assert first.origin_x == 0 and first.origin_y == 0
    # New offset must be confirmed, not ignored forever or adopted from one frame.
    assert smoother.update(estimate_hud_transform(shifted, previous=first)) == first
    second = smoother.update(estimate_hud_transform(shifted, previous=first))
    assert second.origin_x == 56 and second.origin_y == 42


def test_capture_height_includes_translated_hud_without_changing_fixed_lines():
    geometry = HUDGeometry()
    assert geometry.capture_rect(1975, 1121, ui_scale=1, origin_y=42) == (0, 0, 1975, 148)
    assert geometry.capture_rect(1920, 1080, ui_scale=1) == (0, 0, 1920, 106)


def test_zone_capture_debounce_never_loses_measured_toolbar_width():
    """Rebuilding an OCR crop must not erase the physically measured HUD edge."""
    from types import SimpleNamespace

    from dofusic.app import DofusicController
    from dofusic.vision.layout import ZoneCropStabilizer

    image = read_image('havresac_collapsed_corner.png')
    hud = extract_hud_inputs(image)
    assert hud.toolbar_width == 206
    controller = DofusicController.__new__(DofusicController)
    controller._zone_crop_stabilizer = ZoneCropStabilizer(confirmations=2)
    controller.hud_geometry = HUDGeometry()
    controller._zone_crop_stabilizer.observe(50, scale=1., window_id=123, maximum=image.shape[1])
    stable = controller._stabilize_zone_capture(hud, SimpleNamespace(image=image, hwnd=123))
    assert stable.zone.shape[1] == 50
    assert stable.toolbar_width == 206
    rect = tracking_screen_rects(
        capture_screen_rect=(10, 20, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape, hud=stable, toolbar_buttons=2,
    )
    assert rect.combat == (10, 20, 206, 37)
