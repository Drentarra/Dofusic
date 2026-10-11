import hashlib
import sys
import zipfile

import pytest

from test_release_hardening import _verifier_module, _workflow


def test_music_free_release_is_validated_and_hashed_by_cli(tmp_path, monkeypatch):
    verifier = _verifier_module()
    source = tmp_path / 'Dofusic.zip'
    checksum = tmp_path / 'Dofusic.zip.sha256'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
    monkeypatch.setattr(sys, 'argv', ['verify_release.py', 'final', str(source), str(checksum), '--without-music'])
    verifier.main()
    assert checksum.read_text() == f'{hashlib.sha256(source.read_bytes()).hexdigest()}  Dofusic.zip\n'


@pytest.mark.parametrize('music_entry', ['Dofusic/Musiques/', 'Dofusic/Musiques/Musique.opus'])
def test_music_free_release_rejects_accidentally_bundled_music(tmp_path, music_entry):
    verifier = _verifier_module()
    source = tmp_path / 'Dofusic.zip'
    checksum = tmp_path / 'Dofusic.zip.sha256'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr(music_entry, b'music' if not music_entry.endswith('/') else b'')
    with pytest.raises(ValueError):
        verifier.finalize_release(source, checksum, include_music=False)
    assert not checksum.exists()


def test_public_release_build_and_verification_agree_on_music_free_zip():
    workflow = _workflow('build-release.yml')
    build = next(step for step in workflow['jobs']['build-windows']['steps'] if step['name'] == 'Build and test portable version')
    verify = next(step for step in workflow['jobs']['release-windows']['steps'] if step['name'] == 'Verify final public ZIP and compute SHA256')
    assert '--without-music' in build['run']
    assert '--without-music' in verify['run']
