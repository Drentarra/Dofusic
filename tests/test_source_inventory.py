"""Couverture du contrôle syntaxique et des collisions de définitions."""
from __future__ import annotations

from pathlib import Path

from Data.tools.audit_sources import analyze_python, collect, report


def test_source_inventory_checks_all_production_and_test_files():
    root = Path(__file__).resolve().parents[1]
    result = collect(root)
    assert result['python_files_checked'] >= 90
    assert result['invalid_python_files'] == {}
    assert result['duplicate_definitions'] == {}
    assert 'Data/dofusic/ui/main_window.py' in result['files']
    assert 'Data/dofusic/audio/player.py' in result['files']
    assert '.github/scripts/verify_release.py' in result['files']
    assert 'tests/test_source_inventory.py' in result['files']
    assert 'main_window.py' in report(result)


def test_source_inventory_detects_silent_method_shadowing():
    code = 'class Example:\n    def run(self): return 1\n    def run(self): return 2\n'
    result = analyze_python(code, filename='sample.py')
    assert result['masked_definitions'] == [
        {'name': 'run', 'first_line': 2, 'second_line': 3, 'scope': 'Example'}
    ]


def test_source_inventory_accepts_property_setter():
    code = (
        'class Example:\n'
        '    @property\n'
        '    def volume(self): return self._volume\n'
        '    @volume.setter\n'
        '    def volume(self, value): self._volume = value\n'
    )
    assert analyze_python(code, filename='sample.py')['masked_definitions'] == []
