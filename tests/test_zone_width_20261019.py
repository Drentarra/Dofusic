"""October 19 regressions: long names and oversized passive zone outlines."""
import numpy as np

from dofusic.vision.layout import ZoneOverlayWidthTracker, _zone_label_typographic_width, trim_zone_line


def test_confirmed_enutrosor_does_not_inherit_scenery_overhang():
    tracker = ZoneOverlayWidthTracker()
    tracker.observe(578, window_id=15, scale=1)
    assert tracker.confirm(
        'Enutrosor (Creuset des Fortunés)', 578,
        window_id=15, scale=1, confidence=.999,
    )
    assert 368 <= tracker.width <= 400  # previous log: 374; anomalous: 578
    assert tracker.observe(578, window_id=15, scale=1) == tracker.width


def test_normal_zone_widths_remain_pixel_measured():
    for label, width in (
        ("Astrub (Cité d'Astrub)", 254),
        ('Amakna (Plaine des Scarafeuilles)', 388),
        ('Donjon des Scarafeuilles - Sixième salle', 450),
    ):
        tracker = ZoneOverlayWidthTracker()
        tracker.observe(width, window_id=1, scale=1)
        assert tracker.confirm(label, width, window_id=1, scale=1, confidence=.99)
        assert tracker.width == width


def test_long_zone_title_is_not_assigned_an_arbitrary_small_cap():
    text = 'Tour des Voyageurs Dimensionnels - Salle du portail'
    tracker = ZoneOverlayWidthTracker()
    tracker.observe(610, window_id=1, scale=1)
    assert tracker.confirm(text, 610, window_id=1, scale=1, confidence=.99)
    assert tracker.width == 610
    assert _zone_label_typographic_width(text, 1) > 560


def test_hyphenated_long_zone_retains_a_separated_suffix():
    # Coherent outlined glyph groups: a detached dash followed by "Salle du
    # portail"; the map is black in this synthetic case to isolate geometry.
    image = np.zeros((34, 820, 3), dtype=np.uint8)
    def strokes(start, end):
        for x in range(start, end, 5):
            image[5:17, x:x + 3] = 235
            image[10:13, x + 1:x + 2] = 20
    strokes(6, 155)
    strokes(169, 329)
    strokes(344, 415)
    image[13:15, 460:467] = 235  # separated punctuation
    strokes(486, 610)
    result = trim_zone_line(image, gap_stop=42, padding=6)
    assert result.shape[1] >= 600  # no cut before "- Salle du portail"
    assert result.shape[1] < 650


def test_unrelated_isolated_glints_do_not_expand_a_zone():
    image = np.zeros((34, 780, 3), dtype=np.uint8)
    for x in range(6, 205, 6):
        image[5:17, x:x + 3] = 235
        image[10:12, x + 1:x + 2] = 20
    image[5:17, 268:271] = 235
    image[10:12, 269:270] = 20
    result = trim_zone_line(image, gap_stop=42, padding=6)
    assert 195 <= result.shape[1] <= 240
