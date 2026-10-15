from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from dofusic.audio.library import AUDIO_EXTS
from dofusic.audio.player import MusicPlayer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


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


def test_frozen_runtime_does_not_bundle_deno_python_package():
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    main = (DATA / 'main.py').read_text(encoding='utf-8')
    assert "'deno', 'yt_dlp_ejs'" not in spec
    assert "'deno', 'imageio_ffmpeg'" not in main
    assert 'resolve_quickjs_executable' in main


