"""Régressions visuelles sans publier de captures privées du joueur.

Deux contextes sont reproduits : HUD sur décor clair/texturé et sur fond noir.
Les masques d'icônes de référence et la reconnaissance de zone restent intacts.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import analyze_combat_toolbar
from dofusic.vision.layout import (
    HUDTransform,
    estimate_hud_transform,
    extract_hud_inputs,
    tracking_screen_rects,
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
            cv2.rectangle(frame, (x, 7), (x + 22, 30), (80, 75, 70), -1)
            cv2.circle(frame, (x + 11, 18), 5, (210, 205, 200), 1)
    return frame



@pytest.mark.parametrize('bright', [False, True])
def test_calibration_stays_independent_of_scene_brightness(bright):
    transform = estimate_hud_transform(_scene(bright=bright))
    assert transform is not None
    assert abs(transform.scale - 1.0) <= .05


@pytest.mark.parametrize('bright', [False, True])
def test_collapsed_combat_is_detected_with_light_or_dark_scene(bright):
    observed = analyze_combat_toolbar(extract_hud_inputs(_scene(bright=bright)).combat)
    assert observed.in_combat is True
    assert observed.toolbar_buttons == 1


@pytest.mark.parametrize('bright', [False, True])
def test_expanded_frame_covers_all_six_buttons(bright):
    from dofusic.vision.combat_shapes import GLYPH_ROWS
    image = _scene(bright=bright, collapsed=True)
    image[:37, :214] = (65, 60, 55)
    for x, name in zip((6, 42, 76, 110, 144, 178),
                       ('minus', 'combat_eye', 'diamond', 'heart', 'fighter', 'skull')):
        rows = GLYPH_ROWS[name]
        mask = np.array([[(row >> bit) & 1 for bit in range(25)] for row in rows], dtype=bool)
        image[6:31, x:x + 25][mask] = (225, 225, 225)
    hud = extract_hud_inputs(image, transform=HUDTransform())
    observation = analyze_combat_toolbar(hud.combat)
    assert observation.in_combat is True
    assert observation.toolbar_buttons == 6
    rect = tracking_screen_rects(
        capture_screen_rect=(100, 200, image.shape[1], image.shape[0]),
        capture_image_shape=image.shape,
        hud=hud,
        toolbar_buttons=observation.toolbar_buttons,
    )
    assert rect.combat[2] == 214


def test_toolbar_rectangle_is_constant_across_coloured_backgrounds():
    from dofusic.vision.layout import combat_toolbar_frame_width
    assert combat_toolbar_frame_width(1, 1.) == 38
    assert combat_toolbar_frame_width(2, 1.) == 76
    assert combat_toolbar_frame_width(6, 1.) == 214
    assert combat_toolbar_frame_width(6, 1.5) == 321


def test_zone_outline_uses_full_confirmed_label_and_ignores_pixel_noise():
    from dofusic.app import DofusicController
    from dofusic.vision.layout import zone_label_frame_width
    controller = DofusicController.__new__(DofusicController)
    controller._zone_overlay_label = "Astrub (Cité d'Astrub)"
    full = controller._stable_zone_overlay_rect((10, 40, 65, 34))
    assert full[2] == zone_label_frame_width("Astrub (Cité d'Astrub)", 34)
    assert full == controller._stable_zone_overlay_rect((10, 40, 200, 34))
    controller._zone_overlay_label = 'Astrub'
    short = controller._stable_zone_overlay_rect((10, 40, 200, 34))
    assert full[2] > short[2]


def test_long_zone_name_does_not_stop_at_first_whitespace():
    from dofusic.vision.layout import zone_label_frame_width
    short = zone_label_frame_width('Astrub', 34)
    full = zone_label_frame_width('Astrub (Cité d\'Astrub)', 34)
    long = zone_label_frame_width('Citadelle des remparts lointains', 34)
    assert short < full < long
