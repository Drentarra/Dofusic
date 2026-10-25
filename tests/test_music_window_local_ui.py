"""Interface locale : aucun onglet de diffusion ni élément réseau."""
from __future__ import annotations

import inspect

from dofusic.ui.music_window import MusicWindow, filter_local_tracks


def test_music_window_exposes_local_controls_without_online_mode():
    code = inspect.getsource(MusicWindow)
    assert 'MUSIQUES LOCALES' in code
    assert 'Reprendre Dofus' in code
    assert 'Boucle OFF' in code
    assert 'FILE D’ATTENTE' in code
    assert 'ONLINE' not in code
    assert 'yt_dlp' not in code
    assert 'search_client' not in code


def test_music_window_local_queue_and_scroll_are_accessible():
    methods = vars(MusicWindow)
    for name in (
        '_results_mousewheel', '_queue_mousewheel',
        '_play_local', '_enqueue_local', '_remove_queue_index',
        '_clear_queue', '_toggle_repeat', '_resume_dofus',
    ):
        assert name in methods


def test_filter_local_tracks_does_not_use_network(tmp_path):
    files = (tmp_path / 'Incarnam.opus', tmp_path / 'Astrub.mp3', tmp_path / 'Combat.ogg')
    assert filter_local_tracks(files, 'carnam') == files[:1]
    assert filter_local_tracks(files, '') == files
