from pathlib import Path
import json
import zipfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str):
    return yaml.load((ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8-sig'), Loader=yaml.BaseLoader)


def _verifier_module():
    import importlib.util
    path = ROOT / '.github' / 'scripts' / 'verify_release.py'
    spec = importlib.util.spec_from_file_location('verify_release', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_security_docs_dependabot_and_lock_are_present():
    assert (ROOT / 'SECURITY.md').is_file()
    dependabot = (ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'package-ecosystem: "github-actions"' in dependabot

    lock = ROOT / 'Data' / 'requirements-lock.txt'
    entries = [line.strip() for line in lock.read_text(encoding='utf-8').splitlines() if line.strip() and not line.startswith('#')]
    assert entries
    assert all('==' in entry for entry in entries)
    assert any(entry.lower().startswith('opencv-python-headless==') for entry in entries)
    assert not any(entry.lower().startswith('opencv-python==') for entry in entries)


def test_ci_is_read_only_and_runs_on_main_and_pull_requests():
    workflow = _workflow('ci.yml')
    assert workflow['permissions'] == {}
    assert set(workflow['on']) == {'push', 'pull_request'}
    assert workflow['on']['push']['branches'] == ['main']
    for job in workflow['jobs'].values():
        assert job['permissions'] == {'contents': 'read'}


def test_release_workflow_is_pinned_and_public_release_has_one_asset():
    workflow = _workflow('build-release.yml')
    assert workflow['permissions'] == {}
    assert workflow['on']['push']['tags'] == ['v*']
    assert set(workflow['jobs']) == {'build-windows', 'release-windows'}

    build = workflow['jobs']['build-windows']
    final = workflow['jobs']['release-windows']
    assert build['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert final['permissions'] == {
        'contents': 'write',
        'actions': 'read',
        'id-token': 'write',
        'attestations': 'write',
    }

    serialized = json.dumps(workflow).lower()
    assert 'signpath' not in serialized
    assert 'music-v1' not in serialized

    expected_pins = {
        'actions/checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
        'actions/setup-python': '5fda3b95a4ea91299a34e894583c3862153e4b97',
        'actions/upload-artifact': '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
        'actions/download-artifact': '37930b1c2abaa49bbe596cd826c3c89aef350131',
        'actions/attest': '1e69f48acb82d1966a394da916b4c1698aa569d6',
    }
    for job in workflow['jobs'].values():
        for step in job['steps']:
            if 'uses' not in step:
                continue
            action, sha = step['uses'].split('@', 1)
            if action in expected_pins:
                assert sha == expected_pins[action]

    publish = next(step for step in final['steps'] if 'gh release create' in step.get('run', ''))
    command = publish['run']
    assert 'Release/Dofusic.zip' in command
    assert 'Dofusic.zip.sha256' not in command
    assert 'Dofusic.sbom.json' not in command
    assert 'BUILD_SIZE_REPORT.txt' not in command


def test_final_zip_validator_accepts_clean_layout_with_music_marker(tmp_path):
    verifier = _verifier_module()
    source = tmp_path / 'Dofusic.zip'
    checksum = tmp_path / 'Dofusic.zip.sha256'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/README.txt', b'Ajoutez vos musiques ici.')

    result = verifier.finalize_release(source, checksum)
    assert result['file_count'] == 3
    assert checksum.is_file()


def test_final_zip_validator_rejects_unexpected_root(tmp_path):
    verifier = _verifier_module()
    source = tmp_path / 'bad.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/README.txt', b'info')
        archive.writestr('extra.txt', b'bad')

    with pytest.raises(ValueError):
        verifier.finalize_release(source, tmp_path / 'bad.sha256')


def test_sbom_validation_requires_locked_packages(tmp_path):
    verifier = _verifier_module()
    lock = tmp_path / 'lock.txt'
    lock.write_text('numpy==2.4.6\nopencv-python-headless==4.14.0.94\n', encoding='utf-8')
    sbom = tmp_path / 'sbom.json'
    sbom.write_text(json.dumps({
        'bomFormat': 'CycloneDX',
        'specVersion': '1.6',
        'components': [
            {'name': 'numpy', 'version': '2.4.6'},
            {'name': 'opencv-python-headless', 'version': '4.14.0.94'},
            {'name': 'RapidOCR', 'version': '3.9.2'},
        ],
    }), encoding='utf-8')

    assert verifier.validate_sbom(sbom, lock)['spec_version'] == '1.6'
