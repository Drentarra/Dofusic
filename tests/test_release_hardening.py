from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_release_documentation_and_update_checks_are_present():
    """Protect release documentation from regressions in security guidance."""
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    security_policy = REPOSITORY_ROOT / "SECURITY.md"
    dependabot = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(
        encoding="utf-8"
    )

    assert "CONVERTIR_EN_OPUS_64K.bat" not in readme
    assert security_policy.is_file()
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'directory: "/Data"' in dependabot
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'directory: "/"' in dependabot


def test_portable_build_dependency_lock_is_complete_and_avoids_opencv_conflict():
    lock = REPOSITORY_ROOT / 'Data' / 'requirements-lock.txt'

    assert lock.is_file()
    entries = [
        line.strip()
        for line in lock.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert entries
    assert all('==' in entry for entry in entries)
    assert any(entry.lower().startswith('opencv-python-headless==') for entry in entries)
    assert not any(entry.lower().startswith('opencv-python==') for entry in entries)
    assert not any(entry.lower().startswith('rapidocr==') for entry in entries)


def test_music_pack_metadata_pins_the_independent_release():
    import json
    import re

    path = REPOSITORY_ROOT / 'Data' / 'music-pack.json'
    assert path.is_file(), 'music pack metadata is missing'
    metadata = json.loads(path.read_text(encoding='utf-8'))
    assert metadata['tag'] == 'music-v1'
    assert metadata['asset'] == 'Dofusic-Musiques-v1.zip'
    assert re.fullmatch(r'[0-9a-f]{64}', metadata['sha256'])


def _music_pack_module():
    import importlib.util

    path = REPOSITORY_ROOT / '.github' / 'scripts' / 'music_pack.py'
    assert path.is_file(), 'music pack generator is missing'
    spec = importlib.util.spec_from_file_location('music_pack', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _music_source(tmp_path, entries):
    import zipfile

    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in entries:
            item = zipfile.ZipInfo(name)
            item.filename = name  # Preserve malformed paths instead of Windows normalization.
            archive.writestr(item, content)
    return source


def test_music_pack_preserves_only_music_bytes_in_sorted_root_and_is_reproducible(tmp_path, monkeypatch):
    import hashlib
    import zipfile

    module = _music_pack_module()
    source = _music_source(tmp_path, [
        ('Dofusic/Data/secret.txt', b'not music'),
        ('Dofusic/Musiques/z.opus', b'last track'),
        ('Dofusic/Musiques/a/first.opus', b'first track'),
        ('Dofusic/Musiques/', b''),
    ])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    first = tmp_path / 'Dofusic-Musiques-v1.zip'
    second = tmp_path / 'second.zip'
    result = module.build_pack(source, first)
    module.build_pack(source, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ['Musiques/a/first.opus', 'Musiques/z.opus']
        assert archive.read('Musiques/a/first.opus') == b'first track'
        assert archive.read('Musiques/z.opus') == b'last track'
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
    actual = hashlib.sha256(first.read_bytes()).hexdigest()
    assert result == {'sha256': actual, 'file_count': 2, 'uncompressed_bytes': 21}
    assert first.with_suffix('.zip.sha256').read_text(encoding='ascii') == f'{actual}  Dofusic-Musiques-v1.zip\n'


def test_music_pack_rejects_wrong_source_sha_before_writing(tmp_path):
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Musiques/track.opus', b'music')])
    output = tmp_path / 'pack.zip'
    with pytest.raises(ValueError, match='SHA256'):
        module.build_pack(source, output)
    assert not output.exists()
    assert not output.with_suffix('.zip.sha256').exists()


def test_music_pack_rejects_missing_source_before_writing(tmp_path):
    import pytest

    module = _music_pack_module()
    output = tmp_path / 'pack.zip'
    with pytest.raises(FileNotFoundError):
        module.build_pack(tmp_path / 'missing.zip', output)
    assert not output.exists()


def test_music_pack_rejects_empty_selection_before_writing(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Data/app.py', b'app')])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'pack.zip'
    with pytest.raises(ValueError, match='empty'):
        module.build_pack(source, output)
    assert not output.exists()


def test_music_pack_rejects_unsafe_paths_and_collisions_before_writing(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    unsafe_entries = [
        [('Dofusic/Musiques/../outside.opus', b'bad')],
        [('Dofusic/Musiques/a\\outside.opus', b'bad')],
        [('Dofusic/Musiques/C:/outside.opus', b'bad')],
        [('Dofusic/Musiques//outside.opus', b'bad')],
        [('Dofusic/Musiques/CON.opus', b'bad')],
        [('Dofusic/Musiques/a', b'file'), ('Dofusic/Musiques/a/track.opus', b'track')],
        [('Dofusic/Musiques/track.opus', b'1'), ('Dofusic/Musiques/TRACK.opus', b'2')],
    ]
    for entries in unsafe_entries:
        source = _music_source(tmp_path, entries)
        monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
        output = tmp_path / 'pack.zip'
        try:
            module.build_pack(source, output)
        except ValueError as error:
            assert 'path' in str(error) or 'collision' in str(error)
        else:
            pytest.fail(f'Unsafe entries accepted: {entries!r}')
        assert not output.exists()


def test_music_pack_never_overwrites_existing_pack_or_checksum(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Musiques/track.opus', b'music')])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'pack.zip'
    checksum = output.with_suffix('.zip.sha256')
    checksum.write_bytes(b'existing checksum')
    with pytest.raises(FileExistsError):
        module.build_pack(source, output)
    assert not output.exists()
    assert checksum.read_bytes() == b'existing checksum'
    checksum.unlink()
    output.write_bytes(b'existing pack')
    with pytest.raises(FileExistsError):
        module.build_pack(source, output)
    assert output.read_bytes() == b'existing pack'
