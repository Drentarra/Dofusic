"""Release privacy invariants for the application and its portable artifact."""
import ast
from pathlib import Path

import pytest

from Data.tools.build_portable import BuildError, validate_public_payload

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'Data'


def test_release_runtime_has_no_log_emission_or_trace_recorder():
    assert not (APP / 'dofusic/logging_setup.py').exists()
    assert not (APP / 'dofusic/trace_runtime.py').exists()
    assert not (APP / 'tools/analyze_session_logs.py').exists()
    assert 'faulthandler' not in (APP / 'main.py').read_text(encoding='utf-8')

    for source in (APP / 'dofusic').rglob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {'debug', 'info', 'warning', 'error', 'exception', 'critical'}:
                continue
            target = ast.unparse(node.func.value).lower()
            assert not any(key in target for key in ('logger', '_log', 'getlogger')), (source, node.lineno)


def test_removed_debug_option_does_not_reenable_logging(tmp_path):
    from dofusic.config import AppConfig, load_config
    assert 'debug' not in AppConfig.__dataclass_fields__
    path = tmp_path / 'config.json'
    path.write_text('{"config_version": 27, "debug": true}', encoding='utf-8')
    assert not hasattr(load_config(path), 'debug')


def test_public_payload_requires_exact_three_root_items(tmp_path):
    folder = tmp_path / 'Dofusic'
    (folder / 'Data').mkdir(parents=True)
    (folder / 'Musiques').mkdir()
    (folder / 'Dofusic.exe').write_bytes(b'exe')
    (folder / 'Musiques' / 'Test.opus').write_bytes(b'OggS')
    validate_public_payload(folder)
    (folder / 'notes.txt').write_bytes(b'extra')
    with pytest.raises(BuildError, match='Racine du portable non conforme'):
        validate_public_payload(folder)
