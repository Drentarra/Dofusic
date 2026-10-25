"""HUD regressions for the v1.0.15 local source build.

Run from the patched Dofusic root with PYTHONPATH=Data python -m pytest -q
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from dofusic.vision.layout import (
    HUDTransform,
    combat_toolbar_frame_width,
    extract_hud_inputs,
    tracking_screen_rects,
    trim_zone_line,
)


@pytest.mark.parametrize('scale', (0.6, 0.8, 1.0, 1.25, 1.5, 2.0))
def test_combat_expanded_always_has_same_logical_right_edge(scale):
    assert combat_toolbar_frame_width(6, scale) == round(208 * scale)
    assert combat_toolbar_frame_width(1, scale) == round(38 * scale)
    assert combat_toolbar_frame_width(2, scale) == round(76 * scale)


@pytest.mark.parametrize('scale', (0.6, 0.8, 1.0, 1.25, 1.5))
def test_tracking_rectangle_uses_hud_scale_not_monitor_size(scale):
    image = np.zeros((round(108 * scale), round(600 * scale), 3), np.uint8)
    hud = extract_hud_inputs(image, transform=HUDTransform(scale=scale))
    rects = tracking_screen_rects(
        capture_screen_rect=(200, 80, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape,
        hud=hud,
        toolbar_buttons=6,
    )
    assert rects.combat[0:2] == (200, 80)
    assert rects.combat[2] == round(208 * scale)


def _text_mask_scene(*, width: int, bright: bool) -> np.ndarray:
    # Deliberately varying wallpaper. Only the outlined glyphs are stable.
    h = 34
    frame = np.zeros((h, width, 3), np.uint8)
    if bright:
        y, x = np.indices((h, width))
        frame[:, :, 0] = ((40 + 19 * x + 3 * y) % 190).astype(np.uint8)
        frame[:, :, 1] = ((40 + 13 * x + 5 * y) % 190).astype(np.uint8)
        frame[:, :, 2] = ((30 + 17 * x + 7 * y) % 190).astype(np.uint8)
    # Multiword label; a real gap is ~25 px between its glyph clusters.
    for left in (4, 27, 61, 101, 137, 176, 212):
        cv2.rectangle(frame, (left-1, 3), (left+12, 22), (5, 5, 5), -1)
        cv2.rectangle(frame, (left, 5), (left+10, 18), (245, 245, 245), -1)
    return frame


@pytest.mark.parametrize('bright', (False, True))
def test_whole_multiword_label_not_cut_at_spaces(bright):
    frame = _text_mask_scene(width=400, bright=bright)
    roi = trim_zone_line(frame, gap_stop=42, padding=6)
    assert 220 <= roi.shape[1] < 270


def test_ocr_roi_width_is_identical_to_overlay_width():
    frame = _text_mask_scene(width=400, bright=False)
    hud = extract_hud_inputs(frame, transform=HUDTransform())
    rect = tracking_screen_rects(
        capture_screen_rect=(123, 456, 400, 34),
        capture_image_shape=frame.shape,
        hud=hud,
    )
    assert rect.zone[2] == hud.zone.shape[1]
