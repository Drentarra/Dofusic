from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _workflow(path: str):
    return yaml.load((ROOT / path).read_text(encoding='utf-8-sig'), Loader=yaml.BaseLoader)


def test_public_repository_contains_no_internal_signpath_or_music_pack_scaffolding():
    for path in (
        ROOT / '.signpath',
        ROOT / 'docs' / 'superpowers',
        ROOT / 'AUDIT_V25_1_ECO.md',
        ROOT / '.github' / 'scripts' / 'music_pack.py',
        ROOT / '.github' / 'workflows' / 'publish-music-pack.yml',
        ROOT / 'Data' / 'music-pack.json',
    ):
        assert not path.exists(), f'Public repository should not contain {path.relative_to(ROOT)}'


def test_public_music_folder_contains_bundled_tracks():
    music = ROOT / 'Musiques'
    assert music.is_dir()
    tracks = sorted(music.rglob('*.opus'))
    # Ajouter Incarnam doit incrémenter le nombre d'Opus embarqués.
    assert len(tracks) == 162


def test_incarnam_audio_is_present_and_unmodified():
    """Vérifier l'Opus fourni avec la release avant toute compilation."""
    audio = ROOT / 'Musiques' / 'Incarnam.opus'
    assert audio.is_file(), 'Ajouter Musiques/Incarnam.opus à la branche GitHub avant la release'
    assert audio.stat().st_size == 3739762
    with audio.open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == (
            '789466c02c02878db107b040cbc021f02bc298b7bdfc874a67a7e623882160cb'
        )

    # La correspondance Incarnam est déjà disponible dans le mapping SQLite.
    with sqlite3.connect(ROOT / 'Data' / 'dofus_data.sqlite') as database:
        assert database.execute('SELECT name FROM areas WHERE id = 45').fetchone() == ('Incarnam',)


def test_incarnam_area_resolves_to_named_track(tmp_path):
    from dofusic.audio.library import MusicLibrary
    from dofusic.models import LocationKind, LocationRecord

    area = LocationRecord(45, 'Incarnam', LocationKind.AREA)

    class Repository:
        def all_locations(self):
            return (area,)

        def parent_area(self, _location):
            return None

    expected = tmp_path / 'Incarnam.opus'
    expected.write_bytes(b'OggS')  # La bibliothèque trie les chemins sans décoder le fichier.
    library = MusicLibrary(Repository(), tmp_path)
    assert library.resolve(area, raw_text='Incarnam', combat=False) == expected


def test_ci_runs_only_on_main_and_pull_requests():
    workflow = _workflow('.github/workflows/ci.yml')
    assert set(workflow['on']) == {'push', 'pull_request'}
    assert workflow['on']['push']['branches'] == ['main']


def test_release_workflow_has_no_music_download_or_signing_and_publishes_only_dofusic_zip():
    workflow = _workflow('.github/workflows/build-release.yml')
    serialized = json.dumps(workflow).lower()
    assert 'signpath' not in serialized
    assert 'music-v1' not in serialized
    assert 'music-pack' not in serialized
    assert 'dofusic-musiques-v1.zip' not in serialized

    create_release_steps = [
        step
        for job in workflow['jobs'].values()
        for step in job.get('steps', [])
        if step.get('name') == 'Publish GitHub release'
    ]
    assert len(create_release_steps) == 1
    command = create_release_steps[0]['run']
    assert 'Release/Dofusic.zip' in command
    for forbidden in ('Dofusic.zip.sha256', 'Dofusic.sbom.json', 'BUILD_SIZE_REPORT.txt'):
        assert forbidden not in command


def test_builder_keeps_music_directory_in_zip(tmp_path):
    builder = _load_module(ROOT / 'Data' / 'tools' / 'build_portable.py', 'dofusic_builder_clean')
    release_dir = tmp_path / 'Dofusic'
    (release_dir / 'Data').mkdir(parents=True)
    (release_dir / 'Musiques').mkdir()
    (release_dir / 'Dofusic.exe').write_bytes(b'exe')
    (release_dir / 'Data' / 'runtime.bin').write_bytes(b'data')
    (release_dir / 'Musiques' / 'Incarnam.opus').write_bytes(b'OggS')
    output = tmp_path / 'Dofusic.zip'

    builder._make_zip(release_dir, output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert 'Dofusic/Musiques/' in names
    assert 'Dofusic/Dofusic.exe' in names
    assert 'Dofusic/Data/runtime.bin' in names
    assert 'Dofusic/Musiques/Incarnam.opus' in names


def test_readme_has_no_signpath_or_obsolete_checksum_companion_instructions():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8').lower()
    assert 'signpath' not in readme
    assert 'dofusic.zip.sha256' not in readme
    assert 'get-filehash' not in readme
    assert 'contient directement les musiques utilisées par dofusic' in readme
