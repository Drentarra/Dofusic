from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_runtime_requirements_do_not_include_psutil():
    text = (DATA / 'requirements.txt').read_text(encoding='utf-8').lower()
    assert 'psutil' not in text


def test_capture_module_does_not_import_psutil():
    text = (DATA / 'dofusic' / 'capture' / 'dofus_window.py').read_text(encoding='utf-8').lower()
    assert 'import psutil' not in text
    assert 'self.psutil' not in text
