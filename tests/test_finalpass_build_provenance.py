"""Traçabilité incluant les musiques distribuées et le commit Git."""
import json
from pathlib import Path

from Data.tools.build_portable import write_build_provenance


def test_source_fingerprint_tracks_audio_shipped_with_sources(tmp_path):
    root = tmp_path / 'source'
    (root / 'Data' / 'dofusic').mkdir(parents=True)
    (root / 'Data' / 'dofusic' / 'app.py').write_text('print(1)\n', encoding='utf-8')
    music = root / 'Musiques'
    music.mkdir()
    release = tmp_path / 'release'

    first = write_build_provenance(root, release)
    assert first['git_commit'] is None  # no Git required when building a source ZIP
    assert first['github_source_url'] is None
    assert first['source_files'] == 1
    assert len(first['source_sha256']) == 64
    assert json.loads((release / 'BUILD_PROVENANCE.json').read_text()) == first

    (music / 'incarnam.opus').write_bytes(b'public audio')
    with_music = write_build_provenance(root, release)['source_sha256']
    assert with_music != first['source_sha256']
    (music / 'incarnam.opus').write_bytes(b'changed public audio')
    assert write_build_provenance(root, release)['source_sha256'] != with_music

    (root / 'Data' / 'dofusic' / 'app.py').write_text('print(2)\n', encoding='utf-8')
    assert write_build_provenance(root, release)['source_sha256'] != first['source_sha256']


def test_git_commit_is_embedded_only_when_locally_verified(tmp_path, monkeypatch):
    from Data.tools import build_portable as builder
    (tmp_path / '.git').mkdir()
    (tmp_path / 'Data' / 'dofusic').mkdir(parents=True)
    (tmp_path / 'Data' / 'dofusic' / 'main.py').write_bytes(b'print(1)')
    commit = 'a' * 40
    monkeypatch.setattr(builder.shutil, 'which', lambda _: 'git')
    class Result:
        stdout = commit + '\n'
        returncode = 0
    monkeypatch.setattr(builder.subprocess, 'run', lambda *a, **k: Result())

    record = builder.write_build_provenance(tmp_path, tmp_path / 'Release')
    assert record['git_commit'] == commit
    assert record['github_source_url'] == 'https://github.com/Drentarra/Dofusic/tree/' + commit

    class InvalidResult:
        stdout = '../bad-commit'
        returncode = 0
    monkeypatch.setattr(builder.subprocess, 'run', lambda *a, **k: InvalidResult())
    assert builder.write_build_provenance(tmp_path, tmp_path / 'Release')['git_commit'] is None


def test_release_verifier_rejects_unrelated_source_tree(tmp_path):
    import importlib.util
    import zipfile
    checker_path = Path(__file__).resolve().parents[1] / '.github' / 'scripts' / 'verify_release.py'
    spec = importlib.util.spec_from_file_location('release_provenance_check', checker_path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    source = tmp_path / 'source'
    module = source / 'Data' / 'dofusic' / 'app.py'
    module.parent.mkdir(parents=True)
    module.write_text('VERSION=1', encoding='utf-8')
    import shutil
    builder_source = Path(__file__).resolve().parents[1] / 'Data/tools/build_portable.py'
    builder_target = source / 'Data/tools/build_portable.py'
    builder_target.parent.mkdir(parents=True)
    shutil.copyfile(builder_source, builder_target)
    archive = tmp_path / 'portable.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('Dofusic/Dofusic.exe', b'exe')
    record = write_build_provenance(source, tmp_path / 'release', archive=archive)
    manifest = tmp_path / 'release' / 'BUILD_PROVENANCE.json'
    assert checker.verify_source_provenance(archive, source, manifest)['source_sha256'] == record['source_sha256']
    module.write_text('VERSION=2', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError, match='does not match'):
        checker.verify_source_provenance(archive, source, manifest)
    archive.write_bytes(b'tampered-archive')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        checker.verify_source_provenance(archive, source, manifest)
