"""Régressions locales v1.0.15 : mêmes pixels OCR/contour, stabilité, DPI."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.vision.layout import (
    HUDGeometry,
    HUDInputs,
    HUDTransform,
    ZoneCropStabilizer,
    combat_toolbar_frame_width,
    tracking_screen_rects,
)
from dofusic.vision.zone_stability import ZoneTextContinuity
from tools.hud_reference import HUDTransformSmoother


def test_partial_read_does_not_overwrite_full_confirmed_name():
    gate = ZoneTextContinuity()
    gate.confirm("Astrub (Cité d'Astrub)")
    assert gate.hold('Astrub')
    assert gate.hold("Astrub (Cité d'")
    assert not gate.hold("Astrub (Cité d'Astrub)")
    assert gate.confirmed == "Astrub (Cité d'Astrub)"
    assert gate.hold('Astrub')
    assert not gate.hold('Astrub')
    gate.confirm('Astrub', same_place=True)
    assert gate.confirmed == "Astrub (Cité d'Astrub)"


def test_genuine_shorter_zone_can_be_accepted_after_confirmation():
    gate = ZoneTextContinuity()
    gate.confirm("Astrub (Cité d'Astrub)")
    assert gate.hold('Astrub')
    assert not gate.hold('Astrub')
    gate.confirm('Astrub', same_place=False)
    assert gate.confirmed == 'Astrub'
    assert not gate.hold('Astrub (Champs d’Astrub)')


def test_new_full_name_never_waits_on_old_prefix():
    gate = ZoneTextContinuity()
    gate.confirm("Astrub (Cité d'Astrub)")
    assert not gate.hold("Astrub (Champs d'Astrub)")
    gate.confirm("Astrub (Champs d'Astrub)")
    assert gate.confirmed == "Astrub (Champs d'Astrub)"


@pytest.mark.parametrize('states', [(1, 38), (2, 76), (6, 208)])
@pytest.mark.parametrize('scale', [0.6, 1.0, 1.5, 2.0])
def test_toolbar_single_source_of_geometry_across_states(states, scale):
    buttons, expected = states
    assert combat_toolbar_frame_width(buttons, scale) == round(expected * scale)


def test_zone_capture_width_resists_single_frame_noise_and_accepts_changes():
    tracker = ZoneCropStabilizer(confirmations=2)
    def observe(width, hwnd=7, scale=1.0):
        return tracker.observe(
            width, scale=scale, window_id=hwnd, maximum=600
        )
    assert observe(204) == 204
    assert observe(80) == 204       # OCR receives the full old line
    assert observe(204) == 204      # transient disappearance is rejected
    assert observe(270) == 204      # genuine longer zone, first frame
    assert observe(270) == 270      # accepted without permanent freezing
    assert observe(155) == 270
    assert observe(155) == 155      # genuine shorter zone also permitted
    assert observe(280, hwnd=8) == 280  # new window must recalibrate
    assert observe(180, hwnd=8, scale=1.4) == 180  # new HUD scale resets


def test_stabilized_crop_is_identical_to_overlay_input_and_can_expand():
    from dofusic.app import DofusicController

    controller = DofusicController.__new__(DofusicController)
    controller.hud_geometry = HUDGeometry()
    controller._zone_crop_stabilizer = ZoneCropStabilizer()
    image = np.zeros((110, 420, 3), dtype=np.uint8)
    for j in range(420):
        image[40:74, j] = (j % 255, j % 255, j % 255)
    frame = SimpleNamespace(image=image, hwnd=3)

    def hud(width):
        return HUDInputs(
            zone=image[40:74, :width].copy(),
            combat=image[:40], position=image[69:98, :180],
            transform=HUDTransform(),
        )

    assert controller._stabilize_zone_capture(hud(225), frame).zone.shape[1] == 225
    captured = controller._stabilize_zone_capture(hud(120), frame)
    assert captured.zone.shape[1] == 225
    assert np.array_equal(captured.zone, image[40:74, :225])
    rect = tracking_screen_rects(
        capture_screen_rect=(172, 90, 420, 110), capture_image_shape=image.shape,
        hud=captured, toolbar_buttons=6,
    )
    assert rect.zone == (172, 130, 225, 34)
    assert controller._stabilize_zone_capture(hud(120), frame).zone.shape[1] == 120


def test_screen_mapping_handles_window_movement_and_screen_pixel_scale():
    hud = HUDInputs(
        zone=np.zeros((34, 200, 3), np.uint8),
        position=np.zeros((29, 180, 3), np.uint8),
        combat=np.zeros((40, 300, 3), np.uint8),
        transform=HUDTransform(scale=1., origin_x=3, origin_y=2),
    )
    shape = (110, 600, 3)
    a = tracking_screen_rects(
        capture_screen_rect=(100, 200, 600, 110), capture_image_shape=shape, hud=hud,
        toolbar_buttons=6,
    )
    b = tracking_screen_rects(
        capture_screen_rect=(340, 90, 600, 110), capture_image_shape=shape, hud=hud,
        toolbar_buttons=6,
    )
    assert a.zone == (103, 242, 200, 34)
    assert b.zone == (343, 132, 200, 34)
    assert a.combat[2] == b.combat[2] == 208
    dpi = tracking_screen_rects(
        capture_screen_rect=(100, 200, 1200, 220), capture_image_shape=shape, hud=hud,
        toolbar_buttons=6,
    )
    assert dpi.zone == (106, 284, 400, 68)
    assert dpi.combat[2] == 416


def test_hud_scale_hysteresis_and_resize_reset():
    tracker = HUDTransformSmoother()
    stable = HUDTransform(scale=1.)
    assert tracker.update(stable) == stable
    assert tracker.update(HUDTransform(scale=1.06)) == stable
    assert tracker.update(HUDTransform(scale=1.)) == stable
    assert tracker.update(HUDTransform(scale=1.25)) == stable
    assert tracker.update(HUDTransform(scale=1.25)).scale == 1.25
    assert tracker.update(None).scale == 1.25
    assert tracker.update(None).scale == 1.25
    assert tracker.update(None).scale == 1.25  # temporary toolbar occlusion is not a resize
    for _ in range(20):
        assert tracker.update(None) == HUDTransform(scale=1.25)
    tracker.reset()  # manual resize or different window
    assert tracker.update(HUDTransform(scale=.75)).scale == .75


def test_capture_rect_uses_client_size_and_observed_hud_scale():
    geo = HUDGeometry()
    assert geo.capture_rect(1920, 1080, ui_scale=1.) == (0, 0, 1920, 106)
    assert geo.capture_rect(860, 590, ui_scale=1.) == (0, 0, 860, 106)
    assert geo.capture_rect(860, 590, ui_scale=.75) == (0, 0, 860, 80)
    assert geo.capture_rect(860, 590, ui_scale=None) == (0, 0, 860, 220)


def test_zone_recognition_receives_untruncated_unicode_crop_with_mocked_model():
    from dofusic.vision.engines import ZoneOCREngine

    class Output:
        txts = ["Forêt d'Île (Cité d'Astrub)"]
        scores = [.96]

    seen = []

    def recognition(crop, **kwargs):
        seen.append((crop.shape, kwargs))
        return Output()

    engine = ZoneOCREngine(fast_backend=recognition)
    image = np.zeros((27, 249, 3), dtype=np.uint8)
    result = engine.read(image)
    assert result.text == "Forêt d'Île (Cité d'Astrub)"
    assert seen[0][0] == (27, 249, 3)
    assert seen[0][1] == {'use_det': False, 'use_cls': False, 'use_rec': True}
