"""Configuration recovery and UI work avoided when nothing is visible/changed."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from dofusic.config import AppConfig, load_config
from dofusic.ui.main_window import MainWindow
from dofusic.ui.music_window import MusicWindow
from tools.complexity_report import complexity


@pytest.mark.parametrize('version', ['Infinity', '-Infinity', 'NaN', 'null', '[]'])
def test_invalid_config_version_recovers_user_preferences(tmp_path, version):
    path = tmp_path / 'config.json'
    path.write_text('{"config_version":' + version + ',"volume":42}', encoding='utf-8')
    config = load_config(path)
    assert config.volume == 42
    assert config.config_version == AppConfig().config_version


@pytest.mark.parametrize('field', ['mute', 'show_visualizer', 'normalize_loudness', 'always_on_top'])
@pytest.mark.parametrize('value,expected', [('false', False), ('true', True), (0, False), (1, True)])
def test_sanitized_boolean_settings_have_boolean_values(field, value, expected):
    assert getattr(AppConfig(**{field: value}).sanitized(), field) is expected


def test_disabled_visualizer_does_not_poll_audio_or_draw():
    calls = []
    window = MainWindow.__new__(MainWindow)
    window._closed = False
    window.config = AppConfig(show_visualizer=False)
    window.root = SimpleNamespace(after=lambda delay, callback: calls.append(delay))
    window.controller = SimpleNamespace(player=SimpleNamespace(
        spectrum_levels=lambda *_: pytest.fail('disabled visualizer read audio')))
    window._redraw_visualizer = lambda *_: pytest.fail('disabled visualizer drew bars')
    window._visualizer_tick()
    assert len(calls) == 1 and calls[0] >= 250


def test_unchanged_search_results_preserve_rows_and_scroll_position():
    window = MusicWindow.__new__(MusicWindow)
    window._local_tracks_snapshot = (Path('Astrub.opus'), Path('Bonta.opus'))
    query = ['astr']
    remembered, rendered = [], []
    window.search_var = SimpleNamespace(get=lambda: query[0])
    window.session = SimpleNamespace(remember_search=remembered.append)
    window._render_local_results = rendered.append
    window._filter_local_results()
    query[0] = 'ASTRUB'
    window._filter_local_results()
    assert remembered == ['astr', 'ASTRUB']
    assert rendered == [(Path('Astrub.opus'),)]
    query[0] = 'bonta'
    window._filter_local_results()
    assert rendered[-1] == (Path('Bonta.opus'),)


def test_complexity_does_not_count_nested_function_or_class_bodies():
    tree = ast.parse('''
def outer(flag):
    def inner(value):
        if value:
            return 1
        return 0
    class Local:
        def method(self, value):
            return 1 if value else 0
    return inner(flag)
''')
    assert complexity(tree.body[0]) == 1
    assert complexity(tree.body[0].body[0]) == 2
