"""Legacy HUD capture and keyboard polling are not part of the published application."""
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parents[1]


def test_no_legacy_manual_diagnostic_launchers_or_code():
    forbidden = (
        'DIAGNOSTIC_DOFUS.bat', 'DIAGNOSTIC_SIMULATEUR.bat',
        'GUIDE_DIAGNOSTIC_WINDOWS.md',
        'Data/dofusic/diagnostics.py', 'Data/dofusic/diagnostic_keys.py',
    )
    assert all(not (ROOT / item).exists() for item in forbidden)
    source = (ROOT / 'Data/dofusic/app.py').read_text(encoding='utf-8')
    entrypoint = (ROOT / 'Data/main.py').read_text(encoding='utf-8')
    assert 'SimulatorKeyTrace' not in source
    assert '_simulator_key_trace' not in source
    assert 'GetAsyncKeyState' not in source
    assert 'run_diagnostics' not in entrypoint


def test_normal_command_line_does_not_expose_capture_diagnostics():
    parsed = main._parse_args([])
    assert not hasattr(parsed, 'debug')
    assert parsed.self_test is False
    import pytest
    with pytest.raises(SystemExit) as error:
        main._parse_args(['--diagnose-hud'])
    assert error.value.code == 2
