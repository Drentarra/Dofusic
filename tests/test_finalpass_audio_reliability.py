"""Tests de non-régression audio (vérification réelle d'horodatage et arrêt FFmpeg)."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import numpy as np

from dofusic.audio.player import MusicPlayer


class _FakeMixerMusic:
    def load(self, path):
        self.loaded = path

    def fadeout(self, milliseconds):
        pass

    def set_volume(self, volume):
        pass

    def play(self, loops, fade_ms):
        pass

    def get_busy(self):
        return False


def test_overwritten_local_track_never_reuses_old_loudness_or_spectrum(tmp_path, monkeypatch):
    path = tmp_path / 'Musique locale.opus'
    path.write_bytes(b'ancienne version')
    player = MusicPlayer(spectrum_enabled=True, normalize_loudness=True)
    player._remember_loudness_gain(path, 3.0)
    player._spectrum_cache[path] = (np.ones((3, 24), np.float32), 300.0)
    path.write_bytes(b'une nouvelle piste audio beaucoup plus grande')
    player._ready = True
    music = _FakeMixerMusic()
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
    monkeypatch.setattr(player, '_start_track_analysis', lambda p: None)

    assert player.play(path) is True
    assert music.loaded == str(path)
    assert player._current_loudness_gain == 1.0
    assert path not in player._loudness_gain_by_path
    assert path not in player._spectrum_cache


def test_loudness_ffmpeg_is_terminated_when_analysis_fails(tmp_path, monkeypatch):
    path = tmp_path / 'corrompu.opus'
    path.write_bytes(b'audio')
    process = SimpleNamespace(terminated=False, waited=False)

    def poll():
        return None if not process.terminated else -15

    def terminate():
        process.terminated = True

    def wait(timeout=None):
        process.waited = True
        return -15

    def communicate(*, timeout):
        raise subprocess.TimeoutExpired('ffmpeg', timeout)

    process.poll, process.terminate = poll, terminate
    process.wait, process.communicate = wait, communicate
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_k: process)
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg'))

    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=True)
    player.current = path
    player._loudness_generation = 1
    player._loudness_worker(path, 1)
    assert process.terminated
    assert process.waited
    assert player._loudness_process is None
