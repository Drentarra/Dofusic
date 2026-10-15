"""Icon-level regressions from the four reported toolbar states.

Only 25x25 *binary silhouettes* are retained from the provided screenshots.
No screenshot, map, character, text or scene pixels are stored in this file.
The outer scenery is generated independently and varies across scenarios.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.combat_shapes import GLYPH_ROWS

# Quantized outlines of the specific icon states reported in October 2026,
# represented as 25-bit rows, not image assets. These expose resampling errors
# (particularly on the thin coloured diamond) missed by idealized templates.
_OBSERVED = {
    'exploration_plus': (
        0, 0, 0, 0, 0, 6144, 14336, 14336, 14336, 14336, 524160,
        1048544, 1048544, 14336, 14336, 14336, 14336, 14336, 6144,
        0, 0, 0, 0, 0, 0,
    ),
    'exploration_eye': (
        0, 0, 0, 0, 126976, 261632, 1048320, 1048320, 4194176,
        8359904, 16764912, 16764912, 16777200, 16777200, 8363968,
        4194176, 2096896, 1048320, 261632, 122880, 0, 0, 0, 0, 0,
    ),
    'combat_plus': (
        0, 0, 0, 0, 0, 14336, 14336, 14336, 14336, 14336, 524160,
        1048544, 1048544, 31744, 14336, 14336, 14336, 14336, 14336,
        0, 0, 0, 0, 0, 0,
    ),
    'combat_minus': (
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 258048, 524256, 524256,
        0, 256, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ),
    'combat_eye': (
        0, 0, 0, 126976, 129024, 524544, 1597824, 1700224, 261120,
        26156828, 20842380, 20710350, 20448206, 20842380, 26189336,
        8649728, 1700224, 1573248, 0, 129024, 61440, 0, 0, 0, 0,
    ),
    'combat_diamond': (
        0, 0, 0, 0, 130560, 261632, 524160, 4161472, 8376296,
        33489918, 33292798, 33030398, 32505982, 33030271, 33292799,
        33424382, 16756728, 4194288, 2097088, 524032, 121856, 28672,
        0, 0, 0,
    ),
    'combat_heart': (
        0, 0, 0, 0, 0, 1048544, 1048560, 2097136, 2097144, 2097144,
        2097144, 2097136, 2097136, 1048544, 524224, 262016, 130816,
        65024, 31744, 4096, 0, 0, 0, 0, 0,
    ),
    'combat_fighter': (
        0, 0, 0, 0, 1031936, 1048448, 2097024, 2064256, 769920,
        441984, 32640, 65408, 65504, 65520, 131064, 262140, 262140,
        229356, 196592, 24480, 7936, 3584, 0, 0, 0,
    ),
    'combat_skull': (
        0, 0, 0, 0, 0, 106392, 262140, 262140, 262140, 262140,
        131068, 131064, 126840, 116280, 116280, 65520, 65520,
        65520, 131064, 16, 0, 0, 0, 0, 0,
    ),
}
_X = (6, 42, 76, 110, 144, 178)


def _paint_icon(image, x, rows):
    for dy, row in enumerate(rows):
        for dx in range(25):
            if int(row) & (1 << dx):
                image[6 + dy, x + dx] = (225, 225, 225)


def _toolbar(state: str, *, landscape: str):
    rng = np.random.default_rng(120)
    frame = np.zeros((40, 320, 3), dtype=np.uint8)
    if landscape == 'volcano':
        frame[:] = (30, 65, 170)
        frame[4:, 76:] = rng.integers(20, 190, size=(36, 244, 3), dtype=np.uint8)
    elif landscape == 'space':
        frame[:] = (3, 5, 8)
    elif landscape == 'astrub':
        frame[:] = (140, 190, 210)
        frame[2:, 70:] = rng.integers(70, 240, size=(38, 250, 3), dtype=np.uint8)
    elif landscape == 'ocean':
        frame[:] = (165, 100, 35)
    else:
        raise AssertionError(landscape)

    # Only the actual button background is painted; the rest is generated map.
    if state == 'exploration_collapsed':
        frame[:37, :76] = (65, 60, 55)
        pairs = [('exploration_plus', 0), ('exploration_eye', 1)]
    elif state == 'combat_collapsed':
        frame[:37, :37] = (65, 60, 55)
        pairs = [('combat_plus', 0)]
    elif state == 'combat_expanded':
        frame[:37, :214] = (65, 60, 55)
        pairs = [(name, i) for i, name in enumerate((
            'combat_minus', 'combat_eye', 'combat_diamond',
            'combat_heart', 'combat_fighter', 'combat_skull',
        ))]
    elif state == 'exploration_expanded':
        frame[:37, :214] = (65, 60, 55)
        pairs = [('minus', 0), ('sword', 1), ('dots', 2), ('info', 3)]
    else:
        raise AssertionError(state)
    for name, index in pairs:
        _paint_icon(frame, _X[index], _OBSERVED.get(name, GLYPH_ROWS.get(name)))
    return frame


@pytest.mark.parametrize('landscape', ('volcano', 'space', 'astrub', 'ocean'))
@pytest.mark.parametrize('state,expected', (
    ('exploration_collapsed', False),
    ('combat_collapsed', True),
    ('exploration_expanded', False),
    ('combat_expanded', True),
))
def test_four_icon_states_work_with_any_scene_behind_toolbar(state, expected, landscape):
    observed = analyze_combat_toolbar(_toolbar(state, landscape=landscape))
    assert observed.in_combat is expected
    assert observed.confidence >= .60


def test_coloured_diamond_can_be_imperfect_when_four_other_icons_are_clear():
    from dofusic.vision.combat import _glyph_mask, _shape_score
    patch = _toolbar('combat_expanded', landscape='space')[6:31, 76:101]
    score = _shape_score(_glyph_mask(patch), 'diamond')
    assert .60 <= score < .72
    assert analyze_combat_toolbar(_toolbar('combat_expanded', landscape='space')).in_combat is True


def test_missing_neighbouring_button_confirms_combat_even_if_scenery_matches_hud():
    image = _toolbar('combat_collapsed', landscape='astrub')
    # Distant material-coloured walls were responsible for the false "unknown"
    # result in earlier versions; none form an actual adjacent Dofus button.
    image[:37, 165:200] = (65, 60, 55)
    assert analyze_combat_toolbar(image).in_combat is True


def test_extra_button_or_partial_combat_toolbar_is_never_a_positive_combat():
    image = _toolbar('combat_collapsed', landscape='ocean')
    image[:37, 43:69] = (65, 60, 55)
    assert analyze_combat_toolbar(image).in_combat is None
    expanded = _toolbar('combat_expanded', landscape='volcano')
    expanded[6:31, 110:135] = (65, 60, 55)
    assert analyze_combat_toolbar(expanded).in_combat is None


def test_three_confirmed_images_change_state_and_keep_dungeon_priority_separate():
    tracker = CombatStateTracker(required_confirmations=3)
    for name, state in (
        ('combat_collapsed', True), ('exploration_expanded', False),
        ('combat_expanded', True), ('exploration_collapsed', False),
    ):
        observation = analyze_combat_toolbar(_toolbar(name, landscape='astrub'))
        assert tracker.update(observation) is None
        assert tracker.update(observation) is None
        assert tracker.update(observation) is state
        assert tracker.in_combat is state
