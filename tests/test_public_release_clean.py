from __future__ import annotations

import importlib.util
import json
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
        if 'gh release create' in step.get('run', '')
    ]
    assert len(create_release_steps) == 1
    command = create_release_steps[0]['run']
    assert 'Release/Dofusic.zip' in command
    for forbidden in ('Dofusic.zip.sha256', 'Dofusic.sbom.json', 'BUILD_SIZE_REPORT.txt'):
        assert forbidden not in command


def test_builder_keeps_empty_music_directory_in_zip(tmp_path):
    builder = _load_module(ROOT / 'Data' / 'tools' / 'build_portable.py', 'dofusic_builder_clean')
    release_dir = tmp_path / 'Dofusic'
    (release_dir / 'Data').mkdir(parents=True)
    (release_dir / 'Musiques').mkdir()
    (release_dir / 'Dofusic.exe').write_bytes(b'exe')
    (release_dir / 'Data' / 'runtime.bin').write_bytes(b'data')
    output = tmp_path / 'Dofusic.zip'

    builder._make_zip(release_dir, output)

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert 'Dofusic/Musiques/' in names
    assert 'Dofusic/Dofusic.exe' in names
    assert 'Dofusic/Data/runtime.bin' in names


def test_final_release_validator_accepts_empty_music_directory(tmp_path):
    verifier = _load_module(ROOT / '.github' / 'scripts' / 'verify_release.py', 'release_verifier_clean')
    source = tmp_path / 'Dofusic.zip'
    checksum = tmp_path / 'Dofusic.zip.sha256'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('Dofusic/', b'')
        archive.writestr('Dofusic/Data/', b'')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/', b'')
        archive.writestr('Dofusic/Dofusic.exe', b'exe')

    result = verifier.finalize_release(source, checksum)

    assert result['file_count'] == 2
    assert checksum.is_file()


def test_readme_has_no_signpath_or_obsolete_checksum_companion_instructions():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8').lower()
    assert 'signpath' not in readme
    assert 'dofusic.zip.sha256' not in readme
    assert 'get-filehash' not in readme
    assert 'dossier `musiques`' in readme
