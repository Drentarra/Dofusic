"""Behavioral regressions for source-only CI and portable release verification."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from Data.tools import build_portable as builder

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location(
        'v200_release_verifier', ROOT / '.github/scripts/verify_release.py',
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name', ['tests/conftest.py', 'tests/helpers/fixtures.py',
                                  'pyproject.toml', 'requirements-dev.txt'])
def test_provenance_changes_when_test_environment_changes(tmp_path, name):
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('first\n', encoding='utf-8')
    before, count = builder._source_fingerprint(tmp_path)
    assert count == 1
    source.write_text('changed\n', encoding='utf-8')
    assert builder._source_fingerprint(tmp_path)[0] != before


def _music_release(tmp_path):
    source = tmp_path / 'source/Musiques'
    source.mkdir(parents=True)
    (source / 'theme.opus').write_bytes(b'OggS-opus')
    (source / 'bonus.mp3').write_bytes(b'mp3-data')
    output = tmp_path / 'release/Dofusic'
    (output / 'Data').mkdir(parents=True)
    (output / 'Dofusic.exe').write_bytes(b'exe')
    (output / 'Data/runtime.bin').write_bytes(b'runtime')
    builder._copy_music(source.parent, output)
    archive = tmp_path / 'portable.zip'
    builder._make_zip(output, archive)
    return source, archive


@pytest.mark.parametrize('change', ['same-size-content', 'missing', 'extra'])
def test_release_verifies_every_packaged_audio_format(tmp_path, change):
    source, archive = _music_release(tmp_path)
    check = _verifier()
    assert check.verify_music_sources(archive, source)['opus_count'] == 1
    if change == 'same-size-content':
        (source / 'bonus.mp3').write_bytes(b'bad-data')
    elif change == 'missing':
        (source / 'bonus.mp3').unlink()
    else:
        (source / 'extra.wav').write_bytes(b'new audio')
    with pytest.raises(ValueError, match='(?:Audio|Music) .*mismatch'):
        check.verify_music_sources(archive, source)


def _source_tree(root):
    for name in (
        'Data/main.py', 'Data/dofus_data.sqlite', 'Data/place_aliases.json',
        'Data/dungeons.json', 'Data/dofusic/ui/assets/dofusic.ico',
        'Data/Dofusic.spec', 'Data/requirements.txt', 'Data/requirements-lock.txt',
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    (root / 'Musiques').mkdir()
    (root / 'Musiques/README.txt').write_text('Add the soundtrack before release.\n')


def test_source_only_ci_skips_packaging_but_default_release_stays_strict(tmp_path):
    _source_tree(tmp_path)
    with pytest.raises(builder.BuildError, match='Aucun fichier OPUS'):
        builder.validate_source(tmp_path, tmp_path / 'Data')
    assert builder.validate_source(tmp_path, tmp_path / 'Data', require_music=False) is False
    (tmp_path / 'Musiques/track.opus').write_bytes(b'OggS')
    assert builder.validate_source(tmp_path, tmp_path / 'Data', require_music=False) is True
    assert builder.validate_source(tmp_path, tmp_path / 'Data') is True


@pytest.mark.parametrize('name, data', [('empty.opus', b''), ('only.mp3', b'music')])
def test_source_only_ci_never_ignores_incomplete_soundtrack(tmp_path, name, data):
    _source_tree(tmp_path)
    (tmp_path / 'Musiques' / name).write_bytes(data)
    with pytest.raises(builder.BuildError):
        builder.validate_source(tmp_path, tmp_path / 'Data', require_music=False)


@pytest.mark.parametrize('with_music', [False, True])
def test_ci_music_detection_emits_build_decision(tmp_path, monkeypatch, with_music):
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    step = next(step for step in workflow['jobs']['checks']['steps']
                if step.get('id') == 'soundtrack')
    _source_tree(tmp_path)
    # Execute the actual workflow Python with the real builder, without invoking
    # PyInstaller or fetching external models.
    tools = tmp_path / 'Data/tools'
    tools.mkdir()
    (tools / 'build_portable.py').write_bytes((ROOT / 'Data/tools/build_portable.py').read_bytes())
    if with_music:
        (tmp_path / 'Musiques/track.opus').write_bytes(b'OggS')
    output = tmp_path / 'output.txt'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.chdir(tmp_path)
    exec(compile(step['run'], '<soundtrack-step>', 'exec'), {})
    assert output.read_text(encoding='utf-8').strip() == f'available={str(with_music).lower()}'
