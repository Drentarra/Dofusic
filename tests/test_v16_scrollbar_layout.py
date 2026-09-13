from __future__ import annotations

import inspect

from dofusic.ui.music_window import MusicWindow


def test_results_scrollbar_has_readable_width_and_right_gutter_for_both_modes():
    assert MusicWindow.RESULTS_SCROLLBAR_WIDTH >= 12
    assert MusicWindow.RESULTS_SCROLLBAR_RIGHT_PAD >= 2

    build = inspect.getsource(MusicWindow._build)
    assert 'width=self.RESULTS_SCROLLBAR_WIDTH' in build
    assert "padx=(self.RESULTS_SCROLLBAR_GAP, self.RESULTS_SCROLLBAR_RIGHT_PAD)" in build
