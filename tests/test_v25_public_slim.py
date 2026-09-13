from __future__ import annotations

from pathlib import Path

from dofusic.config import AppConfig, load_config
from dofusic.online.models import OnlineTrack


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


def test_discovery_client_uses_injected_search_runner_without_browser():
    from dofusic.online.discovery import YouTubeDiscoveryClient

    calls = []

    def runner(query: str, limit: int):
        calls.append((query, limit))
        return [
            {
                'id': 'abc123xyz00',
                'title': 'Track test',
                'uploader': 'Auteur',
                'duration': 123,
                'thumbnail': 'https://img.test/a.jpg',
                'webpage_url': 'https://www.youtube.com/watch?v=abc123xyz00',
            }
        ]

    client = YouTubeDiscoveryClient(search_runner=runner, suggestion_runner=lambda _q, _n: [])
    result = client.search('  test   dofus  ', limit=5)
    assert calls == [('test dofus', 5)]
    assert result == (
        OnlineTrack(
            video_id='abc123xyz00',
            title='Track test',
            author='Auteur',
            duration_seconds=123,
            thumbnail_url='https://img.test/a.jpg',
            source_url='https://www.youtube.com/watch?v=abc123xyz00',
        ),
    )


def test_default_performance_profile_is_lightweight():
    cfg = AppConfig()
    assert cfg.ui_tick_ms >= 50
    assert cfg.online_cache_mb <= 128


def test_portable_build_downloads_only_live_ocr_model():
    import importlib.util

    path = DATA / 'tools' / 'build_portable.py'
    spec = importlib.util.spec_from_file_location('dofusic_build_v25', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert set(module.OCR_MODELS) == {'PP-OCRv6_rec_small.onnx'}


def test_thumbnail_service_does_not_leak_per_video_locks(tmp_path):
    from dofusic.online.thumbnails import ThumbnailService

    service = ThumbnailService(tmp_path, fetcher=lambda *_args: b'not-an-image', max_items=16)
    track = OnlineTrack(video_id='abc123xyz00', title='x')
    assert service.get_png(track) == b''
    assert service.active_lock_count == 0


def test_legacy_webview_profile_cleanup_is_scoped_to_dofusic_userdata(tmp_path):
    from dofusic.config import cleanup_legacy_user_data

    legacy = tmp_path / 'webview2'
    legacy.mkdir()
    (legacy / 'cookies.bin').write_bytes(b'x')
    keep = tmp_path / 'keep.txt'
    keep.write_text('keep', encoding='utf-8')

    cleanup_legacy_user_data(tmp_path)

    assert not legacy.exists()
    assert keep.read_text(encoding='utf-8') == 'keep'


def test_ui_logo_asset_is_right_sized_for_actual_64px_usage():
    from PIL import Image

    logo = DATA / 'dofusic' / 'ui' / 'assets' / 'dofusic.png'
    with Image.open(logo) as image:
        assert image.width <= 256
        assert image.height <= 256
    assert logo.stat().st_size < 100_000


def test_windows_builder_and_python_builder_agree_on_v25_zip_name():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace')
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    expected = 'Dofusic_V25_1_ECO_WINDOWS_X64.zip'
    assert expected in batch
    assert expected in builder
    assert 'Dofusic_RC14_PORTABLE_WINDOWS_X64.zip' not in batch


def test_live_spectrum_decode_is_downsampled_for_low_cpu_cost():
    from dofusic.audio import player

    assert player.SPECTRUM_FPS <= 15
    assert player.SPECTRUM_SAMPLE_RATE <= 16_000


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
