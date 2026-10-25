"""Vérification de l'inventaire Git et des empreintes de ressources."""
from __future__ import annotations

import hashlib
from pathlib import Path

from Data.tools.audit_repository import category, fingerprint, summary


def test_repository_inventory_recognizes_sources_and_assets():
    assert category('Data/dofusic/app.py') == 'source Python'
    assert category('Data/dofus_data.sqlite') == 'base SQLite'
    assert category('Musiques/Incarnam.opus') == 'musique'
    assert category('Data/dofusic/ui/assets/dofusic.ico') == 'image'


def test_file_hash_matches_real_content(tmp_path):
    path = tmp_path / 'example.txt'
    path.write_bytes(b'abc')
    result = fingerprint(path)
    assert result == {'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}


def test_repository_manifest_has_all_file_types(tmp_path):
    from Data.tools.audit_repository import collect
    root = Path(__file__).resolve().parents[1]
    result = collect(root)
    assert result['tracked_files'] >= 120
    assert result['invalid_files'] == []
    assert result['verified_files'] == result['tracked_files']
    if (root / 'Musiques' / 'Incarnam.opus').exists():
        assert 'Musiques/Incarnam.opus' in result['files']
    else:
        assert not any(name.startswith('Musiques/') and name.endswith('.opus') for name in result['files'])
    assert 'Data/dofusic/version.py' in result['files']
    assert 'Data/dofus_data.sqlite' in result['files']
    assert 'README.md' in result['files']
    assert 'SHA-256' in summary(result)


def test_archive_without_git_still_audits_real_files(tmp_path):
    from Data.tools.audit_repository import collect
    (tmp_path / 'Data').mkdir()
    (tmp_path / 'Data' / 'app.py').write_text('print(1)')
    (tmp_path / '.portable-build').mkdir()
    (tmp_path / '.portable-build' / 'junk.txt').write_text('ignore')
    result = collect(tmp_path)
    assert result['inventory_source'] == 'filesystem'
    assert list(result['files']) == ['Data/app.py']
    assert result['verified_files'] == 1
