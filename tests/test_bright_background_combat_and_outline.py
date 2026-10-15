"""Régressions visuelles sans publier de captures privées du joueur.

Deux contextes sont reproduits : HUD sur décor clair/texturé et sur fond noir.
Les masques d'icônes de référence et la reconnaissance de zone restent intacts.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from dofusic.app import DofusicController
from dofusic.vision.combat import analyze_combat_toolbar
from dofusic.vision.layout import (
    HUDGeometry,
    HUDTransform,
    _visible_combat_toolbar_width,
    estimate_hud_transform,
    extract_hud_inputs,
    tracking_screen_rects,
    visible_zone_text_width,
)


def _scene(*, bright: bool, collapsed: bool = True):
    frame = np.zeros((112, 390, 3), dtype=np.uint8)
    if bright:
        yy, xx = np.indices(frame.shape[:2])
        frame[:, :, 0] = (125 + (xx * 3 + yy) % 70).astype(np.uint8)
        frame[:, :, 1] = (150 + (xx + yy * 2) % 85).astype(np.uint8)
        frame[:, :, 2] = (140 + (xx * 2 + yy * 3) % 95).astype(np.uint8)
    width = 37 if collapsed else 214
    frame[:37, :width] = (65, 60, 55)
    if collapsed:
        cv2.line(frame, (12, 18), (25, 18), (230, 220, 220), 1)
        cv2.line(frame, (18, 12), (18, 25), (230, 220, 220), 1)
    else:
        for x in (49, 85, 121, 157, 193):
            cv2.rectangle(frame, (x, 7), (x + 22, 30), (115, 108, 100), -1)
    return frame


@pytest.mark.parametrize('bright', [False, True])
def test_calibration_succeeds_independently_of_scene_brightness(bright):
    image = _scene(bright=bright)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert abs(transform.scale - 1.0) <= 0.05
    assert transform.origin_y == 0


@pytest.mark.parametrize('bright', [False, True])
def test_collapsed_combat_is_detected_on_dark_and_bright_maps(bright):
    image = _scene(bright=bright)
    hud = extract_hud_inputs(image)
    observation = analyze_combat_toolbar(hud.combat)
    assert observation.in_combat is True
    assert observation.confidence >= 0.60


@pytest.mark.parametrize('bright', [False, True])
def test_toolbar_frame_stops_at_last_icon_even_with_bright_scenery(bright):
    image = _scene(bright=bright, collapsed=False)
    hud = extract_hud_inputs(image, transform=HUDTransform())
    width = _visible_combat_toolbar_width(hud.combat, 1.0)
    assert 205 <= width <= 222
    rects = tracking_screen_rects(
        capture_screen_rect=(100, 200, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape,
        hud=hud,
    )
    assert rects.combat[2] == width
    assert rects.combat[2] < 250


def test_panel_after_plus_still_prevents_false_combat_on_colored_map():
    image = _scene(bright=True)
    image[:37, 42:116] = (80, 72, 66)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is None


def test_zone_display_width_excludes_bright_scenery_beyond_label():
    image = _scene(bright=True)
    cv2.putText(image, "Astrub", (8, 65), cv2.FONT_HERSHEY_SIMPLEX, .66, (18, 18, 18), 4)
    cv2.putText(image, "Astrub", (8, 65), cv2.FONT_HERSHEY_SIMPLEX, .66, (245, 245, 245), 2)
    # Textured background and light patches away from the actual zone name.
    cv2.rectangle(image, (200, 44), (300, 67), (234, 233, 232), -1)
    width = visible_zone_text_width(image[40:74], scale=1.0)
    assert width is not None
    assert 55 <= width <= 120
    hud = extract_hud_inputs(image, transform=HUDTransform())
    assert hud.zone_display_width == width
    rects = tracking_screen_rects(
        capture_screen_rect=(0, 0, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape,
        hud=hud,
    )
    assert rects.zone[2] == width
    # OCR still receives its original dynamic crop, not the visual-width crop.
    assert hud.zone.shape[1] > 0


def test_zone_overlay_changes_on_confirmed_ocr_and_on_persistent_recalibration():
    controller = DofusicController.__new__(DofusicController)
    controller._zone_overlay_width_px = 110
    controller._zone_overlay_text_key = 'astrub'
    controller._zone_overlay_refresh_requested = False
    normal = (10, 40, 180, 34)
    assert controller._stable_zone_overlay_rect(normal, text_present=False)[2] == 110
    assert controller._stable_zone_overlay_rect(normal, text_present=True)[2] == 110
    assert controller._stable_zone_overlay_rect(normal, text_present=True)[2] == 110
    assert controller._stable_zone_overlay_rect(normal, text_present=True)[2] == 180

    controller._zone_overlay_refresh_requested = True
    assert controller._stable_zone_overlay_rect((10, 40, 90, 34), text_present=True)[2] == 90
    assert not controller._zone_overlay_refresh_requested
    # Single noisy frames never resize the outline.
    assert controller._stable_zone_overlay_rect(normal, text_present=True)[2] == 90
    assert controller._stable_zone_overlay_rect((10, 40, 90, 34), text_present=True)[2] == 90
