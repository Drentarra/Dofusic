from pathlib import Path

import cv2
import numpy as np

from dofusic.models import PositionOCRResult


def _result(raw: str, x: int, y: int) -> PositionOCRResult:
    return PositionOCRResult(raw, x, y, 0.99, 12.0, 'test')


def test_coordinate_token_is_built_from_accepted_coordinates_not_raw_suffix():
    from dofusic.vision.position_overlay import coordinate_token

    assert coordinate_token(_result('1, -36 - Niveau 140%', 1, -36)) == '1, -36'
    assert coordinate_token(_result('OCR garbage 4,-19 Niveau 10', 4, -19)) == '4, -19'
    assert coordinate_token(_result('-26,37 whatever', -26, 37)) == '-26, 37'


def test_overlay_width_is_deterministic_for_same_coordinates_independent_of_raw_text():
    from dofusic.vision.position_overlay import coordinate_overlay_width

    a = coordinate_overlay_width(_result('1, -36 - Niveau 140%', 1, -36), roi_height=29)
    b = coordinate_overlay_width(_result('1,-36', 1, -36), roi_height=29)
    c = coordinate_overlay_width(_result('Niveau 200 1 ; -36', 1, -36), roi_height=29)

    assert a == b == c
    assert 60 <= a <= 76


def test_overlay_width_covers_complete_two_digit_coordinates_without_using_full_roi():
    from dofusic.vision.position_overlay import coordinate_overlay_width

    widths = {
        token: coordinate_overlay_width(result, roi_height=29)
        for token, result in {
            '4,-19': _result('4,-19 - Niveau 10', 4, -19),
            '-26,37': _result('-26,37 - Niveau 200', -26, 37),
            '-99,-99': _result('-99,-99 - Niveau 200', -99, -99),
        }.items()
    }

    assert 60 <= widths['4,-19'] <= 76
    assert widths['-26,37'] > widths['4,-19']
    assert widths['-99,-99'] >= widths['-26,37']
    assert widths['-99,-99'] < 120


def test_real_dofus_crop_overlay_stops_before_level_separator():
    from dofusic.vision.position_overlay import coordinate_overlay_width

    fixture = Path(__file__).parent / 'fixtures' / 'hud' / 'astrub_6_-23_level15.png'
    # cv2.imread() can fail on Windows when the absolute path contains
    # non-ASCII characters (for example `Téléchargements`). Read the bytes
    # with pathlib first, then decode them in memory so the test is path-safe.
    encoded = np.frombuffer(fixture.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert image is not None
    # User-provided real Dofus HUD crop. Position row is the lower half and the
    # visible `- Niveau` separator begins around x=72 in this reference capture.
    width = coordinate_overlay_width(_result('6, -23 - Niveau 15', 6, -23), roi_height=29)
    assert 64 <= width < 72


def test_layout_no_longer_computes_position_overlay_from_pixels():
    from dofusic.vision import layout

    assert not hasattr(layout, 'trim_position_line')
    assert not hasattr(layout, '_position_coordinate_end')


def test_controller_maps_full_position_roi_to_accepted_coordinate_overlay_width():
    from dofusic.app import DofusicController

    result = _result('1, -36 - Niveau 140', 1, -36)
    rect = DofusicController._position_overlay_screen_rect(
        full_rect=(100, 200, 180, 29),
        result=result,
        roi_height=29,
        roi_width=180,
    )

    assert rect[:2] == (100, 200)
    assert 60 <= rect[2] <= 76
    assert rect[3] == 29
