"""Test the actual portable files across both packaging and verification modules."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_clean_portable_passes_final_verification_without_diagnostics(tmp_path):
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_contract')
    verifier = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_contract')
    folder = tmp_path / 'Dofusic'
    (folder / 'Data').mkdir(parents=True)
    (folder / 'Musiques').mkdir()
    (folder / 'Musiques/Incarnam.opus').write_bytes(b'OggS')
    (folder / 'Dofusic.exe').write_bytes(b'exe')
    (folder / 'Data/runtime.bin').write_bytes(b'runtime')
    path = tmp_path / 'Dofusic.zip'
    builder._make_zip(folder, path)
    result = verifier.finalize_release(path, tmp_path / 'Dofusic.zip.sha256')
    assert result['file_count'] == 3
    with zipfile.ZipFile(path) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
        assert set(names) == {'Dofusic/Dofusic.exe', 'Dofusic/Data/runtime.bin',
                              'Dofusic/Musiques/Incarnam.opus'}


@pytest.mark.parametrize('bad_name', [
    'DIAGNOSTIC_DOFUS.bat', 'DIAGNOSTIC_SIMULATEUR.bat',
    'GUIDE_DIAGNOSTIC_WINDOWS.md', 'Dofusic.log', 'README.md',
    'BUILD_PROVENANCE.json', 'BUILD_SIZE_REPORT.txt',
])
def test_unwanted_root_file_is_blocked(tmp_path, bad_name):
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_strict')
    verifier = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_strict')
    folder = tmp_path / 'Dofusic'
    (folder / 'Data').mkdir(parents=True)
    (folder / 'Musiques').mkdir()
    (folder / 'Musiques/Incarnam.opus').write_bytes(b'OggS')
    (folder / 'Dofusic.exe').write_bytes(b'exe')
    (folder / 'Data/runtime.bin').write_bytes(b'runtime')
    (folder / bad_name).write_text('unwanted', encoding='utf-8')
    with pytest.raises(builder.BuildError, match='non conforme'):
        builder._make_zip(folder, tmp_path / 'bad.zip')
    with zipfile.ZipFile(tmp_path / 'bad-manual.zip', 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/Incarnam.opus', b'OggS')
        archive.writestr('Dofusic/' + bad_name, b'bad')
    with pytest.raises(ValueError, match='Unexpected ZIP root/layout'):
        verifier.finalize_release(tmp_path / 'bad-manual.zip', tmp_path / 'hash.txt')


@pytest.mark.parametrize('bad_name', [
    'Data/UserData/logs/dofusic.log',
    'Data/diagnostics/report.json',
    'Data/BUILD_PROVENANCE.json',
    'Musiques/README.txt',
    'Musiques/track.log',
])
def test_developer_payload_is_rejected_deep_in_portable(tmp_path, bad_name):
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_deep')
    verifier = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_deep')
    folder = tmp_path / 'Dofusic'
    (folder / 'Data').mkdir(parents=True)
    (folder / 'Musiques').mkdir()
    (folder / 'Musiques/Incarnam.opus').write_bytes(b'OggS')
    (folder / 'Dofusic.exe').write_bytes(b'exe')
    (folder / 'Data/runtime.bin').write_bytes(b'runtime')
    unwanted = folder / bad_name
    unwanted.parent.mkdir(parents=True, exist_ok=True)
    unwanted.write_bytes(b'unwanted')
    with pytest.raises(builder.BuildError):
        builder._make_zip(folder, tmp_path / 'bad.zip')
    with zipfile.ZipFile(tmp_path / 'bad-manual.zip', 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/Incarnam.opus', b'OggS')
        archive.writestr('Dofusic/' + bad_name, b'bad')
    with pytest.raises(ValueError, match='Forbidden ZIP'):
        verifier.finalize_release(tmp_path / 'bad-manual.zip', tmp_path / 'hash.txt')


def test_source_fingerprint_tracks_distributed_images_not_developer_launchers(tmp_path):
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_assets')
    icon = tmp_path / 'Data/dofusic/ui/assets/icon.png'
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b'icon-v1')
    launch = tmp_path / 'DIAGNOSTIC_DOFUS.bat'
    launch.write_bytes(b'diagnostic-v1')
    original, count = builder._source_fingerprint(tmp_path)
    assert count == 1
    icon.write_bytes(b'icon-v2')
    changed, _ = builder._source_fingerprint(tmp_path)
    assert changed != original
    launch.write_bytes(b'diagnostic-v2')
    assert builder._source_fingerprint(tmp_path)[0] == changed


def test_verifier_refuses_even_empty_user_data_and_logs_directories(tmp_path):
    checker = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_no_empty_debug')
    for name in ('Dofusic/Data/UserData/', 'Dofusic/Data/logs/', 'Dofusic/Data/tests/'):
        archive_path = tmp_path / (name.split('/')[-2] + '.zip')
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr('Dofusic/Dofusic.exe', b'exe')
            archive.writestr('Dofusic/Data/runtime.bin', b'runtime')
            archive.writestr('Dofusic/Musiques/music.opus', b'OggS')
            archive.writestr(name, b'')
        with pytest.raises(ValueError, match='Forbidden ZIP development directory'):
            checker.finalize_release(archive_path, tmp_path / 'sha256.txt')


def test_pyinstaller_dependency_readme_is_removed_not_ffmpeg(tmp_path):
    """Reproduce the exact imageio_ffmpeg README that failed on Windows."""
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_ffmpeg_cleanup')
    verifier = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_ffmpeg_cleanup')
    folder = tmp_path / 'Dofusic'
    data = folder / 'Data'
    ffmpeg = data / 'imageio_ffmpeg' / 'binaries' / 'ffmpeg-win64.exe'
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_bytes(b'FFMPEG-REQUIRED')
    readme = ffmpeg.parent / 'README.md'
    readme.write_text('PyInstaller dependency documentation', encoding='utf-8')
    data_readme = data / 'README.txt'
    data_readme.write_text('unused runtime instructions', encoding='utf-8')
    notices = data / 'THIRD_PARTY_NOTICES.md'
    notices.write_text('licenses', encoding='utf-8')
    (folder / 'Dofusic.exe').write_bytes(b'EXE')
    (folder / 'Musiques').mkdir()
    (folder / 'Musiques' / 'Incarnam.opus').write_bytes(b'OggS')

    # Failure must be reproducible before the cleanup.
    with pytest.raises(builder.BuildError, match='diagnostic/developpement'):
        builder.validate_public_payload(folder)
    removed = builder.remove_bundled_readmes(folder)
    assert set(removed) == {
        Path('Data/imageio_ffmpeg/binaries/README.md'), Path('Data/README.txt'),
    }
    assert builder.remove_bundled_readmes(folder) == []  # repeatable
    assert ffmpeg.read_bytes() == b'FFMPEG-REQUIRED'
    assert notices.is_file()  # preserve licensing information
    zip_path = tmp_path / 'Dofusic.zip'
    builder._make_zip(folder, zip_path)
    result = verifier.finalize_release(zip_path, tmp_path / 'Dofusic.zip.sha256')
    assert result['file_count'] == 4
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert 'Dofusic/Data/imageio_ffmpeg/binaries/ffmpeg-win64.exe' in names
        assert 'Dofusic/Data/THIRD_PARTY_NOTICES.md' in names
        assert not any(name.casefold().endswith('/readme.md') or name.casefold().endswith('/readme.txt') for name in names)


@pytest.mark.parametrize('bad_name', [
    'Data/imageio_ffmpeg/binaries/README.md',
    'Data/imageio_ffmpeg/binaries/README.txt',
    'Data/imageio_ffmpeg/binaries/README.rst',
])
def test_dependency_readmes_must_be_stripped_before_packaging(tmp_path, bad_name):
    builder = _load(ROOT / 'Data/tools/build_portable.py', 'builder_docs_policy')
    verifier = _load(ROOT / '.github/scripts/verify_release.py', 'verifier_docs_policy')
    folder = tmp_path / 'Dofusic'
    (folder / 'Data').mkdir(parents=True)
    (folder / 'Musiques').mkdir()
    (folder / 'Dofusic.exe').write_bytes(b'exe')
    (folder / 'Musiques/Incarnam.opus').write_bytes(b'OggS')
    doc = folder / bad_name
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(b'documentation')
    with pytest.raises(builder.BuildError, match='diagnostic/developpement'):
        builder._make_zip(folder, tmp_path / 'invalid.zip')
    with zipfile.ZipFile(tmp_path / 'invalid-manual.zip', 'w') as archive:
        archive.writestr('Dofusic/Dofusic.exe', b'exe')
        archive.writestr('Dofusic/Data/runtime.bin', b'data')
        archive.writestr('Dofusic/Musiques/Incarnam.opus', b'OggS')
        archive.writestr('Dofusic/' + bad_name, b'doc')
    with pytest.raises(ValueError, match='Forbidden ZIP development file'):
        verifier.finalize_release(tmp_path / 'invalid-manual.zip', tmp_path / 'hash.txt')
