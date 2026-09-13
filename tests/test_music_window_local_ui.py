from __future__ import annotations

import inspect

from dofusic.ui.music_window import MusicWindow


def test_local_results_are_mousewheel_scrollable_and_have_queue_button_only():
    source = inspect.getsource(MusicWindow._render_local_results)
    assert '_bind_results_mousewheel' in source
    assert '_enqueue_local' in source
    assert "text='+'" in source
    assert '_download' not in source


def test_local_mode_keeps_repeat_and_playlist_visible():
    source = inspect.getsource(MusicWindow._set_source_mode)
    assert "repeat_button.configure(state='disabled')" not in source
    assert 'queue_label.grid_remove()' not in source
    assert 'queue_holder.grid_remove()' not in source
    assert 'queue_buttons.grid_remove()' not in source
