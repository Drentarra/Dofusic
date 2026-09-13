from __future__ import annotations

from pathlib import Path

import numpy as np


def _stroke(image, x1, x2, y1, y2):
    image[y1:y2, x1:x2] = 255


def test_position_overlay_stops_before_niveau_even_if_suffix_dash_is_thin_or_missing():
    """V20 overlay geometry must be independent of the rendered suffix."""
    from dofusic.models import PositionOCRResult
    from dofusic.vision.position_overlay import coordinate_overlay_width

    with_suffix = PositionOCRResult('4, -19 - Niveau 10', 4, -19, 0.99, 1.0, 'test')
    without_suffix = PositionOCRResult('4, -19', 4, -19, 0.99, 1.0, 'test')

    assert coordinate_overlay_width(with_suffix, 29) == coordinate_overlay_width(without_suffix, 29)
    assert coordinate_overlay_width(with_suffix, 29) < 76

def test_normal_area_resolution_never_depends_on_removed_legacy_dungeon_helper(tmp_path):
    """Leaving a dungeon must be able to resolve Astrub instead of keeping combat audio."""
    from dofusic.audio.library import MusicLibrary
    from dofusic.models import LocationKind, LocationRecord

    astrub = LocationRecord(10, 'Astrub', LocationKind.AREA)

    class Repository:
        def all_locations(self):
            return (astrub,)
        def parent_area(self, _location):
            return None

    (tmp_path / 'Astrub.mp3').write_bytes(b'audio')
    library = MusicLibrary(Repository(), tmp_path)

    resolved = library.resolve(astrub, 'Astrub (Cité d\'Astrub)', combat=False)

    assert resolved is not None
    assert resolved.stem == 'Astrub'


def test_queue_remove_button_stays_inside_narrow_row_with_long_title():
    """The remove control must keep a reserved right-hand column."""
    import tkinter as tk
    import pytest

    from dofusic.online.models import PlaylistItem
    from dofusic.ui.music_window import MusicWindow
    from dofusic.ui.themes import get_theme

    class Session:
        current = None
        repeat_current = False
        pending = (
            PlaylistItem(source='local', title='Titre extrêmement long qui ne doit jamais pousser le bouton de suppression hors de la file', local_path=Path('x.mp3')),
        )
        def remove_pending(self, _index):
            return None

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'affichage Tk indisponible: {exc}')
    root.geometry('260x180')
    try:
        window = MusicWindow.__new__(MusicWindow)
        window.session = Session()
        window.p = get_theme('emerald')
        window.current_var = tk.StringVar(master=root)
        window.repeat_button = tk.Button(root)
        window._last_queue_signature = None
        window.queue_canvas = tk.Canvas(root, width=150, height=100, highlightthickness=0)
        window.queue_canvas.pack()
        window.queue_rows = tk.Frame(window.queue_canvas, width=150)
        window._queue_window = window.queue_canvas.create_window((0, 0), window=window.queue_rows, anchor='nw', width=150)
        window._remove_queue_index = lambda _index: None
        window._queue_mousewheel = lambda _event: None

        MusicWindow._refresh_playback_panel(window)
        root.update_idletasks()

        row = window.queue_rows.winfo_children()[0]
        root.update_idletasks()
        remove = next(child for child in row.winfo_children() if isinstance(child, tk.Button) and child.cget('text') == '×')

        assert remove.winfo_width() >= 18
        assert remove.winfo_x() + remove.winfo_width() <= row.winfo_width()
        assert remove.winfo_ismapped()
    finally:
        root.destroy()
