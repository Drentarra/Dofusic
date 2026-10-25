from __future__ import annotations

import importlib.util
import json
import sqlite3
import zipfile
from pathlib import Path

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


def test_music_is_trackable_in_git_and_location_fixture_remains_available():
    ignored = (ROOT / '.gitignore').read_text(encoding='utf-8')
    assert not any(line.startswith('Musiques/') for line in ignored.splitlines())
    # Keep a real known location as an offline matching fixture.
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
    assert 'Dofusic/Musiques/Incarnam.opus' in names
    assert 'Dofusic/Dofusic.exe' in names
    assert 'Dofusic/Data/runtime.bin' in names
    assert {name for name in names if name.startswith('Dofusic/Musiques/') and not name.endswith('/')} == {
        'Dofusic/Musiques/Incarnam.opus'
    }
    # Test the real builder/verifier contract, not two isolated assumptions.
    verifier = _load_module(ROOT / '.github/scripts/verify_release.py', 'dofusic_verifier_clean')
    checksum = tmp_path / 'Dofusic.zip.sha256'
    assert verifier.finalize_release(output, checksum)['file_count'] >= 3
    assert checksum.exists()


def test_readme_has_no_signpath_or_obsolete_checksum_companion_instructions():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8').lower()
    assert 'signpath' not in readme
    assert 'dofusic.zip.sha256' not in readme
    assert 'get-filehash' not in readme
    assert 'le zip de la release github actions contient' in readme
