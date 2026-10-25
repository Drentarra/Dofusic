"""Protect the dynamic HUD crop for very long map labels."""

import numpy as np

from dofusic.vision.layout import trim_zone_line


def test_long_title_suffix_stays_in_dynamic_crop_without_icon_analysis():
    # A purely textual fixture, including separate small dash and long suffix.
    line = np.zeros((34, 1000, 3), dtype=np.uint8)
    for start, end in ((6, 200), (215, 380), (394, 440), (476, 650), (667, 762)):
        for x in range(start, end, 6):
            line[5:18, x:x + 4] = 235
            line[10:13, x + 1:x + 2] = 15
    line[12:14, 455:464] = 235
    crop = trim_zone_line(line)
    assert 758 <= crop.shape[1] < 820
