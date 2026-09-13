from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from dofusic.audio.library import AUDIO_EXTS
from dofusic.audio.player import MusicPlayer
from dofusic.online.media_cache import MediaCache
from dofusic.online.models import OnlineTrack

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_online_cache_normalizes_download_to_opus_and_library_keeps_opus(tmp_path):
    calls = []

    def downloader(track, cache_dir):
        raw = Path(cache_dir) / f'{track.video_id}.webm'
        raw.write_bytes(b'raw')
        calls.append(('download', raw))
        return raw

    def transcoder(source, target):
        calls.append(('transcode', Path(source), Path(target)))
        Path(target).write_bytes(b'opus64')
        return target

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader, transcoder=transcoder)
    track = OnlineTrack('abcdefghijk', 'Titre')

    cached = cache.ensure_cached(track)
    saved = cache.save_to_library(track, 'Mon titre')

    assert cached == tmp_path / 'cache' / 'abcdefghijk.opus'
    assert saved == tmp_path / 'music' / 'Mon titre.opus'
    assert saved.read_bytes() == b'opus64'
    assert calls == [
        ('download', tmp_path / 'cache' / 'abcdefghijk.webm'),
        ('transcode', tmp_path / 'cache' / 'abcdefghijk.webm', tmp_path / 'cache' / 'abcdefghijk.opus'),
    ]


def test_ffmpeg_opus_transcode_is_explicitly_64_kbps(monkeypatch, tmp_path):
    source = tmp_path / 'source.webm'
    target = tmp_path / 'target.opus'
    source.write_bytes(b'raw')
    captured = {}

    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', types.SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg.exe'))

    def fake_run(command, **kwargs):
        captured['command'] = list(command)
        Path(command[-1]).write_bytes(b'opus')
        return types.SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music')
    produced = cache._transcode_to_opus(source, target)

    assert produced == target
    command = captured['command']
    assert '-c:a' in command and command[command.index('-c:a') + 1] == 'libopus'
    assert '-b:a' in command and command[command.index('-b:a') + 1] == '64k'


def test_local_library_accepts_common_ffmpeg_audio_formats():
    expected = {
        '.mp3', '.ogg', '.oga', '.opus', '.wav', '.flac', '.m4a', '.aac', '.wma',
        '.webm', '.mka', '.mp4', '.aiff', '.aif', '.ac3', '.mp2', '.ape', '.wv', '.tta', '.amr', '.caf',
    }
    assert expected <= AUDIO_EXTS


def test_player_uses_ffmpeg_fallback_when_pygame_cannot_decode_source(tmp_path):
    source = tmp_path / 'track.wma'
    fallback = tmp_path / 'fallback.ogg'
    source.write_bytes(b'wma')
    fallback.write_bytes(b'ogg')
    loaded = []

    class Music:
        def fadeout(self, _ms):
            pass
        def load(self, value):
            loaded.append(Path(value))
            if Path(value) == source:
                raise RuntimeError('unsupported codec')
        def set_volume(self, _value):
            pass
        def play(self, *_args, **_kwargs):
            pass
        def get_busy(self):
            return True

    fake_pygame = types.SimpleNamespace(mixer=types.SimpleNamespace(music=Music()))
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=False)
    player._pygame = fake_pygame
    player._ready = True
    player._transcode_playback_fallback = lambda _path: fallback

    assert player.play(source, loop=False)
    assert loaded == [source, fallback]
    assert player.current == source


def test_spec_bundles_quickjs_binary_only_once_and_builder_guards_large_duplicates():
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')

    assert "collect_data_files('deno')" not in spec
    assert "(str(QUICKJS_BINARY), 'js')" in spec
    assert '_check_large_duplicate_files' in builder


def test_batch_converter_outputs_opus_64k_into_musiques_folder():
    converter = ROOT / 'CONVERTIR_EN_OPUS_64K.bat'
    assert converter.is_file()
    text = converter.read_text(encoding='utf-8', errors='replace').lower()
    assert 'musiques' in text
    assert 'libopus' in text
    assert '64k' in text
    assert 'ffmpeg' in text


def test_frozen_runtime_does_not_bundle_deno_python_package():
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    main = (DATA / 'main.py').read_text(encoding='utf-8')
    assert "'deno', 'yt_dlp_ejs'" not in spec
    assert "'deno', 'imageio_ffmpeg'" not in main
    assert 'resolve_quickjs_executable' in main


def test_quickjs_source_fallback_has_no_python_runtime_package_dependency():
    retrieval = (DATA / 'dofusic' / 'online' / 'retrieval.py').read_text(encoding='utf-8')
    assert 'import deno' not in retrieval
    assert "importlib.import_module('deno')" not in retrieval
    assert "shutil.which('qjs')" in retrieval

