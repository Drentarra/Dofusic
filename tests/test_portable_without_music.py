"""Exercise portable assembly while replacing only Windows build commands."""
import importlib.util
from pathlib import Path
import zipfile

import pytest


def _builder():
    path = Path(__file__).resolve().parents[1] / 'Data/tools/build_portable.py'
    spec = importlib.util.spec_from_file_location('portable_without_music', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('include_music', [False, True])
def test_portable_archive_respects_music_selection(tmp_path, monkeypatch, include_music):
    builder = _builder()
    source = tmp_path / 'Musiques'
    source.mkdir()
    (source / 'Musique.opus').write_bytes(b'test music')

    def compile_windows(_stage, command, **_kwargs):
        if 'PyInstaller' in command:
            release = tmp_path / 'Release/Dofusic'
            (release / 'Data').mkdir(parents=True)
            (release / 'Dofusic.exe').write_bytes(b'test Windows build output')
            (release / 'Data/runtime.dll').write_bytes(b'test runtime')

    monkeypatch.setattr(builder, 'validate_source', lambda *_: None)
    monkeypatch.setattr(builder, 'prepare_ocr_models', lambda *_: None)
    monkeypatch.setattr(builder, 'prepare_quickjs_runtime', lambda root: root / 'qjs.exe')
    monkeypatch.setattr(builder, '_run', compile_windows)
    monkeypatch.setattr(builder, 'validate_release', lambda *_a, **_k: None)
    monkeypatch.setenv('DOFUSIC_QJS_BINARY', '')

    archive = builder.build(tmp_path, include_music=include_music)

    with zipfile.ZipFile(archive) as result:
        assert result.read('Dofusic/Dofusic.exe') == b'test Windows build output'
        assert result.read('Dofusic/Data/runtime.dll') == b'test runtime'
        music = [name for name in result.namelist() if name.startswith('Dofusic/Musiques/')]
        assert bool(music) is include_music
        if include_music:
            assert result.read('Dofusic/Musiques/Musique.opus') == b'test music'


def test_without_music_validator_rejects_accidentally_bundled_music(tmp_path):
    builder = _builder()
    (tmp_path / 'Musiques').mkdir()
    with pytest.raises(builder.BuildError, match='Musiques'):
        builder.validate_release(tmp_path, include_music=False)
