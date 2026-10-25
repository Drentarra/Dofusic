"""End-to-end guarantees for the checked-out OPUS soundtrack."""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

from Data.tools import build_portable as builder

ROOT = Path(__file__).resolve().parents[1]


def verifier():
    spec = importlib.util.spec_from_file_location('music_release_verify', ROOT / '.github/scripts/verify_release.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_fixture(tmp_path):
    source = tmp_path / 'repo/Musiques'
    source.mkdir(parents=True)
    (source / 'Incarnam.opus').write_bytes(b'OggS' * 100)
    (source / 'Donjon').mkdir()
    (source / 'Donjon/Astrub.opus').write_bytes(b'OggS' * 50)
    (source / 'README.txt').write_text('pistes audio', encoding='utf-8')
    output = tmp_path / 'Release/Dofusic'
    (output / 'Data').mkdir(parents=True)
    (output / 'Dofusic.exe').write_bytes(b'fake exe')
    (output / 'Data/runtime.dat').write_bytes(b'data')
    return source, output


def test_musics_are_copied_preserving_names_and_content(tmp_path):
    source, output = release_fixture(tmp_path)
    assert builder._copy_music(source.parent, output) == 2
    zip_path = tmp_path / 'release.zip'
    builder._make_zip(output, zip_path)
    builder.validate_archive(output, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        assert z.read('Dofusic/Musiques/Incarnam.opus') == b'OggS' * 100
        assert z.read('Dofusic/Musiques/Donjon/Astrub.opus') == b'OggS' * 50
        assert z.getinfo('Dofusic/Musiques/Incarnam.opus').compress_type == zipfile.ZIP_STORED
        assert 'Dofusic/Musiques/README.txt' not in z.namelist()
    result = verifier().verify_music_sources(zip_path, source)
    assert result == {'opus_count': 2, 'opus_bytes': 600}


def test_empty_music_folder_refuses_build(tmp_path):
    music = tmp_path / 'Musiques'
    music.mkdir()
    (music / 'README.txt').write_text('no audio', encoding='utf-8')
    with pytest.raises(builder.BuildError, match='Aucun fichier OPUS'):
        builder._copy_music(tmp_path, tmp_path / 'Release/Dofusic')


def test_missing_and_modified_opus_fail_publication(tmp_path):
    source, output = release_fixture(tmp_path)
    builder._copy_music(source.parent, output)
    zip_path = tmp_path / 'release.zip'
    builder._make_zip(output, zip_path)
    check = verifier()
    (source / 'Incarnam.opus').write_bytes(b'OggS' * 99)
    with pytest.raises(ValueError, match='OPUS size mismatch'):
        check.verify_music_sources(zip_path, source)
    (source / 'Incarnam.opus').unlink()
    with pytest.raises(ValueError, match='OPUS list mismatch'):
        check.verify_music_sources(zip_path, source)


def test_final_verifier_rejects_empty_opus_list(tmp_path):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('Dofusic/Dofusic.exe', b'exe')
        z.writestr('Dofusic/Data/data.bin', b'data')
    with pytest.raises(ValueError, match='no OPUS'):
        verifier().finalize_release(path, tmp_path / 'bad.sha256')
