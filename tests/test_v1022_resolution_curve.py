"""Empirical resolution curve regression tests (no runtime image calibration)."""
import pytest

from dofusic.vision.layout import HUDGeometry, hud_transform_from_client_size
from dofusic.vision.resolution_curve import reference_hud_scale


@pytest.mark.parametrize('resolution,bar_height', [
    ((1280, 960), 23),
    ((1600, 900), 30),
    ((1680, 1050), 31),
    ((1920, 1080), 37),
    ((2560, 1440), 37),
])
def test_reference_measurement_anchors(resolution, bar_height):
    mapping = hud_transform_from_client_size(*resolution)
    assert mapping.origin_x == mapping.origin_y == 0
    assert mapping.scale == pytest.approx(bar_height / 37, abs=1e-10)


def test_no_overshoot_or_reversal_at_any_intermediate_ratio():
    scales = [reference_hud_scale(round(1920 * t), round(1080 * t)) for t in
              (0.66 + i * .001 for i in range(341))]
    assert all(0 < v <= 1 for v in scales)
    assert all(x <= y + 1e-12 for x, y in zip(scales, scales[1:]))


def test_plateau_above_reference_and_ultrawide():
    for res in ((2560, 1440), (3840, 2160), (3440, 1440), (1920, 1440)):
        assert reference_hud_scale(*res) == 1.0


def test_both_dimensions_matter_when_aspect_ratio_changes():
    assert reference_hud_scale(1280, 960) == reference_hud_scale(1280, 720)
    assert reference_hud_scale(1680, 1050) == pytest.approx(31 / 37)
    assert reference_hud_scale(1600, 900) == pytest.approx(30 / 37)
    assert reference_hud_scale(1600, 720) < reference_hud_scale(1600, 900)


def test_overlay_and_ocr_geometry_use_identical_transform():
    import numpy as np

    from dofusic.vision.layout import extract_hud_inputs, tracking_screen_rects
    geo = HUDGeometry()
    for w, h in ((1280, 960), (1680, 1050), (2560, 1440)):
        transform = hud_transform_from_client_size(w, h, geometry=geo)
        _x, _y, strip_w, strip_h = geo.capture_rect(w, h, ui_scale=transform.scale)
        frame = np.zeros((strip_h, strip_w, 3), dtype=np.uint8)
        hud = extract_hud_inputs(frame, geo, transform=transform)
        rects = tracking_screen_rects(capture_screen_rect=(0, 0, w, strip_h),
                                       capture_image_shape=frame.shape,
                                       hud=hud, geometry=geo)
        assert rects.combat[3] == round(37 * transform.scale)
        assert rects.zone[1] == round(40 * transform.scale)
        assert rects.zone[3] == round(34 * transform.scale)
        assert hud.zone.shape[0] == rects.zone[3]
