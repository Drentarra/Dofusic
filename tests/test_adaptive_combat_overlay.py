from types import MethodType, SimpleNamespace

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDGeometry, HUDInputs, HUDTransform, extract_hud_inputs, tracking_screen_rects
from test_combat_shapes import collapsed_toolbar, expanded_toolbar


@pytest.mark.parametrize('scale', [0.75, 0.9, 1.0, 1.5, 2.0])
def test_combat_outline_covers_expanded_icons_and_shrinks_when_collapsed(scale):
    rects = []
    for image in (expanded_toolbar(scenery=(20, 150, 115)), collapsed_toolbar(scenery=(20, 150, 115))):
        hud = extract_hud_inputs(cv2.resize(image, None, fx=scale, fy=scale))
        observation = analyze_combat_toolbar(hud.combat)
        assert observation.in_combat is True
        x, y, width, height = observation.icon_rect
        assert 0 <= x < 16 * scale
        assert 0 <= y < 14 * scale
        assert 0 < height < 32 * scale
        rects.append((x, y, width, height))
    assert rects[0][0] + rects[0][2] > 195 * scale
    assert rects[0][2] < 210 * scale
    assert rects[1][2] < 24 * scale


@pytest.mark.parametrize('neighbour', ['eye', 'havresac_gear'])
def test_collapsed_exploration_outline_includes_its_recognized_icon(neighbour):
    observation = analyze_combat_toolbar(collapsed_toolbar(neighbour=neighbour)[:40, :320])
    assert observation.in_combat is False
    x, y, width, height = observation.icon_rect
    assert x < 13
    assert 62 < x + width < 76
    assert y < 12
    assert height < 30


def test_unknown_toolbar_has_no_recognized_icon_outline():
    observation = analyze_combat_toolbar(np.zeros((40, 320, 3), dtype=np.uint8))
    assert observation.in_combat is None
    assert observation.icon_rect is None


def test_icon_outline_maps_capture_pixels_to_screen_with_scale_and_negative_origin():
    hud = HUDInputs(
        zone=np.zeros((51, 180, 3), np.uint8),
        position=np.zeros((44, 270, 3), np.uint8),
        combat=np.zeros((60, 480, 3), np.uint8),
        transform=HUDTransform(scale=1.5, origin_x=4, origin_y=6),
    )
    rects = tracking_screen_rects(
        capture_screen_rect=(-200, 400, 1800, 600),
        capture_image_shape=(300, 900, 3),
        hud=hud, combat_bounds=(15, 10, 290, 33),
    )
    assert rects.combat == (-162, 432, 580, 66)
    unknown = tracking_screen_rects(
        capture_screen_rect=(-200, 400, 1800, 600),
        capture_image_shape=(300, 900, 3), hud=hud,
    )
    assert unknown.combat is None
    assert unknown.zone == rects.zone
    assert unknown.position == rects.position


def test_visible_controller_outline_uses_current_observation_once_per_frame(monkeypatch):
    import dofusic.app as app_module
    from dofusic.app import DofusicController
    from dofusic.capture.dofus_window import CapturedFrame
    from dofusic.capture.service import CaptureSnapshot
    from test_startup_automation_gate import _tick_controller

    controller, _ = _tick_controller(monkeypatch)
    controller.hud_geometry = HUDGeometry()
    controller._automatic_detection_unlocked = True
    controller.state.in_combat = False
    controller.combat_tracker = CombatStateTracker()
    controller._update_combat_from_toolbar = MethodType(DofusicController._update_combat_from_toolbar, controller)
    monkeypatch.setattr(app_module, 'extract_hud_inputs', extract_hud_inputs)
    images = [expanded_toolbar(), collapsed_toolbar(), np.zeros((105, 360, 3), np.uint8)]
    calls = []

    def observe(image, geometry):
        calls.append(image)
        return analyze_combat_toolbar(image, geometry)

    monkeypatch.setattr(app_module, 'analyze_combat_toolbar', observe)
    widths = []
    for index, image in enumerate(images, 1):
        frame = CapturedFrame(image=image, source='visible', hwnd=123,
                              screen_rect=(100, 200, image.shape[1], image.shape[0]),
                              hud_transform=HUDTransform())
        controller.capture_service = SimpleNamespace(
            latest=lambda _sequence, now: CaptureSnapshot(index, frame, now),
        )
        controller.tick(now=10.0 + index)
        rect = controller.state.combat_overlay_rect
        widths.append(None if rect is None else rect[2])
    assert widths[0] > 180
    assert widths[1] < 24
    assert widths[2] is None
    assert len(calls) == 3
    assert controller.state.in_combat is False


def test_unknown_combat_keeps_zone_and_position_outlines_visible(monkeypatch):
    from dofusic.ui.overlay import TrackingOverlay, build_merged_outline_mask, build_tracking_outline_mask

    zone = (100, 240, 120, 34)
    position = (100, 269, 70, 29)
    overlay = TrackingOverlay.__new__(TrackingOverlay)
    overlay.enabled = True
    overlay.window = SimpleNamespace(
        canvas=SimpleNamespace(configure=lambda **kw: None, delete=lambda *a: None),
        top=SimpleNamespace(deiconify=lambda: None, withdraw=lambda: None),
        native_hwnd=10, visible=False, geometry=None, drawn_rects=None,
        capture_excluded=True,
    )
    overlay._foreground_role = lambda hwnd: 'dofus'
    rendered = []

    def render(canvas, combat, zone, position, *, geometry):
        mask = build_tracking_outline_mask(combat, zone, position, geometry)
        rendered.append(mask)
        return mask

    monkeypatch.setattr(overlay, '_draw_tracking_outline', render)
    overlay.update(123, None, zone, position)
    assert overlay.window.visible is True
    assert overlay.window.drawn_rects[0] is None
    bounds = (0, 0, *overlay.window.geometry[2:])
    expected = build_merged_outline_mask(*overlay.window.drawn_rects[1:], bounds)
    assert np.array_equal(np.asarray(rendered[-1]), np.asarray(expected))
    assert np.count_nonzero(np.asarray(rendered[-1])) > 0
    overlay.update(None, None, zone, position)
    assert overlay.window.visible is False
