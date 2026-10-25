"""Regression tests for changes to playback requests while FFmpeg prepares a file."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.player import MusicPlayer


class _FakeMusic:
    def __init__(self, unsupported: Path):
        self.unsupported = unsupported
        self.play_calls: list[int] = []
        self.loaded: list[str] = []

    def load(self, path):
        self.loaded.append(path)
        if Path(path) == self.unsupported:
            raise RuntimeError('codec not supported')

    def stop(self):
        pass

    def set_volume(self, value):
        pass

    def play(self, repeats, fade_ms=0):
        self.play_calls.append(repeats)

    def get_busy(self):
        return bool(self.play_calls)


def test_repeat_mode_changes_during_async_codec_preparation(tmp_path):
    source = tmp_path / 'unhandled.wma'
    converted = tmp_path / 'prepared.ogg'
    converted.write_bytes(b'fake audio data')
    player = MusicPlayer(fade_ms=0, spectrum_enabled=False, normalize_loudness=False)
    fake_music = _FakeMusic(source)
    player._ready = True
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=fake_music))
    started = threading.Event()
    release = threading.Event()
    calls: list[Path] = []

    def convert(path: Path) -> Path:
        calls.append(path)
        started.set()
        assert release.wait(timeout=4.0)
        return converted

    player._transcode_playback_fallback = convert
    try:
        assert player.play(source, loop=False)
        assert started.wait(timeout=2.0)
        assert player.is_preparing(source)
        assert player.play(source, loop=True)  # no new FFmpeg conversion
        assert player._preparing_loop is True
        release.set()
        player._decode_thread.join(timeout=3.0)
        player.tick()
        assert calls == [source]
        assert player.current == source
        assert player._looping is True
        assert fake_music.play_calls == [-1]
    finally:
        release.set()
        player._ready = False
        player.close()
