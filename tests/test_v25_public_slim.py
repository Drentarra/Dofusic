from __future__ import annotations

from pathlib import Path

from dofusic.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_public_config_has_no_developer_mode_and_migrates_legacy_key(tmp_path):
    assert 'developer_mode' not in AppConfig.__dataclass_fields__
    config_path = tmp_path / 'config.json'
    config_path.write_text('{"config_version":23,"developer_mode":true,"volume":41}', encoding='utf-8')
    loaded = load_config(config_path)
    assert loaded.volume == 41
    assert not hasattr(loaded, 'developer_mode')


def test_public_build_has_no_webview_dependency_or_session_button():
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8').casefold()
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8').casefold()
    settings = (DATA / 'dofusic' / 'ui' / 'settings_window.py').read_text(encoding='utf-8').casefold()
    music = (DATA / 'dofusic' / 'ui' / 'music_window.py').read_text(encoding='utf-8').casefold()
    assert 'pywebview' not in requirements
    assert "collect_data_files('webview'" not in spec
    assert "collect_dynamic_libs('webview')" not in spec
    assert "'webview.platforms" not in spec
    assert '0000' not in settings
    assert 'session youtube' not in music


def test_default_performance_profile_is_lightweight():
    cfg = AppConfig()
    assert cfg.ui_tick_ms >= 50
    assert not any(name.startswith('online_') for name in cfg.__dataclass_fields__)


def test_portable_build_downloads_only_live_ocr_model():
    import importlib.util

    path = DATA / 'tools' / 'build_portable.py'
    spec = importlib.util.spec_from_file_location('dofusic_build_v25', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert set(module.OCR_MODELS) == {'PP-OCRv6_rec_small.onnx'}


def test_loading_preferences_never_deletes_unrelated_user_folders(tmp_path):
    # Startup must not recursively delete obsolete directories or user files.
    old_folder = tmp_path / 'webview2'
    old_folder.mkdir()
    payload = old_folder / 'cookies.bin'
    payload.write_bytes(b'user-file')
    (tmp_path / 'config.json').write_text('{"volume":41}', encoding='utf-8')

    loaded = load_config(tmp_path / 'config.json')

    assert loaded.volume == 41
    assert payload.read_bytes() == b'user-file'


def test_ui_logo_asset_is_right_sized_for_actual_64px_usage():
    from PIL import Image

    logo = DATA / 'dofusic' / 'ui' / 'assets' / 'dofusic.png'
    with Image.open(logo) as image:
        assert image.width <= 256
        assert image.height <= 256
    assert logo.stat().st_size < 100_000


def test_windows_builder_and_python_builder_agree_on_versioned_zip_manifest():
    from dofusic.version import __version__, portable_zip_name

    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace')
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert portable_zip_name() == f'Dofusic_{__version__}_LOCAL_WINDOWS_X64_AVEC_MUSIQUES.zip'
    assert portable_zip_name('2.3.4') == 'Dofusic_2.3.4_LOCAL_WINDOWS_X64_AVEC_MUSIQUES.zip'
    assert 'PORTABLE_ARCHIVE_NAME.txt' in batch
    assert 'PORTABLE_ARCHIVE_NAME.txt' in builder
    assert '_1.0.15_LOCAL_WINDOWS' not in batch
    assert '_1.0.15_LOCAL_WINDOWS' not in builder


def test_live_spectrum_decode_is_downsampled_for_low_cpu_cost():
    from dofusic.audio import spectrum

    assert spectrum.SPECTRUM_FPS <= 15
    assert spectrum.SPECTRUM_SAMPLE_RATE <= 16_000


def test_release_validator_rejects_removed_browser_or_medium_artifacts(tmp_path):
    import importlib.util

    import pytest

    path = DATA / 'tools' / 'build_portable.py'
    spec = importlib.util.spec_from_file_location('dofusic_build_removed_artifacts', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    (tmp_path / 'webview').mkdir()
    with pytest.raises(module.BuildError):
        module._check_removed_runtime_artifacts(tmp_path)

    (tmp_path / 'webview').rmdir()
    (tmp_path / 'PP-OCRv6_rec_medium.onnx').write_bytes(b'legacy')
    with pytest.raises(module.BuildError):
        module._check_removed_runtime_artifacts(tmp_path)
