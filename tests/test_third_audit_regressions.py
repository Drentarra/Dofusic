"""Vérifications du troisième audit : caches, destruction Tk et version UI."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.audio.player import MusicPlayer, SPECTRUM_BANDS, _SPECTRUM_CACHE_LIMIT_BYTES
from dofusic.ui.main_window import MainWindow
from dofusic.ui.music_window import MusicWindow
from dofusic.version import __version__


def test_spectrum_cache_has_total_byte_budget_even_for_long_tracks():
    player = MusicPlayer(spectrum_enabled=True)
    frames = np.zeros((30000, SPECTRUM_BANDS), dtype=np.float32)
    for index in range(15):
        path = Path(f'long-track-{index}.opus')
        player.current = path
        player._publish_analysis(path, player._spectrum_generation, frames.copy(), 1000.0, 1.0)
    assert len(player._spectrum_cache) <= 8
    assert sum(array.nbytes for array, _ in player._spectrum_cache.values()) <= _SPECTRUM_CACHE_LIMIT_BYTES
    assert Path('long-track-14.opus') in player._spectrum_cache


def test_oversized_spectrum_remains_visible_but_is_not_kept_in_revisit_cache():
    player = MusicPlayer(spectrum_enabled=True)
    frames = np.zeros((180000, SPECTRUM_BANDS), dtype=np.float32)
    assert frames.nbytes > _SPECTRUM_CACHE_LIMIT_BYTES
    path = Path('very-long-track.opus')
    player.current = path
    player._publish_analysis(path, player._spectrum_generation, frames, 1000.0, 1.0)
    assert player._spectrum_frames.shape == frames.shape
    assert path not in player._spectrum_cache


def test_music_panel_close_is_idempotent():
    closed = []
    window = MusicWindow.__new__(MusicWindow)
    window._closed = False
    window._after_id = None
    window._remember_current_search = lambda: closed.append('remember')
    window.window = SimpleNamespace(destroy=lambda: closed.append('destroy'))
    window.close()
    window.close()
    window._poll()
    assert closed == ['remember', 'destroy']


def test_title_and_version_argument_share_single_release_version(capsys):
    import inspect
    from Data.main import _parse_args

    assert __version__ == '1.0.15'
    init_ast = ast.parse(inspect.getsource(MainWindow.__init__).replace('    def ', 'def ', 1))
    calls = (
        node for node in ast.walk(init_ast)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'title'
    )
    assert any(
        any(isinstance(child, ast.Name) and child.id == '__version__' for child in ast.walk(call))
        for call in calls
    )
    with pytest.raises(SystemExit) as exc:
        _parse_args(['--version'])
    assert exc.value.code == 0
    assert 'Dofusic 1.0.15' in capsys.readouterr().out
