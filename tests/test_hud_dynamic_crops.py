from __future__ import annotations

import numpy as np

from dofusic.location.coordinates import parse_coordinates
from dofusic.models import Coordinates, PositionOCRResult
from dofusic.vision import layout
from dofusic.vision.position_overlay import coordinate_overlay_width


def _glyph_columns(width: int, groups: tuple[tuple[int, int], ...], *, height: int = 29) -> np.ndarray:
    """Synthetic outlined-HUD-like glyph strokes on a black background."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for start, end in groups:
        for x in range(start, end, 4):
            image[7:22, x:x + 2] = 255
    return image


def _position(raw: str, x: int, y: int) -> PositionOCRResult:
    return PositionOCRResult(raw, x, y, 0.99, 1.0, 'test')


def test_position_roi_is_fixed_and_overlay_is_not_pixel_trimmed():
    image = np.zeros((110, 240, 3), dtype=np.uint8)
    hud = layout.extract_hud_inputs(image, layout.HUDGeometry(), transform=layout.HUDTransform())

    assert hud.position.shape[1] == layout.HUDGeometry().position_width
    assert not hasattr(layout, 'trim_position_line')


def test_zone_crop_keeps_fixed_left_origin_and_only_trims_right_edge():
    # A small left margin is part of the fixed calibrated ROI and must be preserved.
    image = _glyph_columns(220, ((8, 48), (58, 104), (116, 158)))

    crop = layout.trim_zone_line(image, gap_stop=24, padding=8, empty_preview_width=96)

    assert crop.shape[1] >= 158
    assert np.all(crop[:, :8] == 0)
    assert crop.shape[1] < 190


def test_coordinate_parser_is_strictly_bounded_to_two_digits_per_axis():
    assert parse_coordinates('-99,99 - Niveau 40%') == Coordinates(-99, 99)
    assert parse_coordinates('99,-99') == Coordinates(99, -99)
    assert parse_coordinates('100,0') is None
    assert parse_coordinates('-100,0') is None
    assert parse_coordinates('0,100') is None
    assert parse_coordinates('0,-100') is None


def test_position_overlay_uses_accepted_coordinates_not_suffix_geometry():
    # The old implementation attempted to segment the `- Niveau` suffix from pixels.
    # V20 deliberately ignores suffix geometry: the accepted X,Y token is the authority.
    a = coordinate_overlay_width(_position('1, -36 - Niveau 140%', 1, -36), 29)
    b = coordinate_overlay_width(_position('1,-36', 1, -36), 29)
    c = coordinate_overlay_width(_position('garbage Niveau 200', 1, -36), 29)

    assert a == b == c
    assert 60 <= a <= 76
