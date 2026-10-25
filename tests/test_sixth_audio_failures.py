"""Règles de réussite réelle de lecture et nettoyage FFmpeg sans périphérique audio."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dofusic.audio.player import MusicPlayer


class _FakeMusic:
    def __init__(self):
        self.played = 0
    def load(self, path):
        pass
    def fadeout(self, ms):
        pass
    def set_volume(self, value):
        pass
    def play(self, *_a, **_k):
        self.played += 1
    def get_busy(self):
        return True


def test_optional_audio_analysis_failure_does_not_undo_started_mixer_playback(tmp_path, monkeypatch):
    events = []
    player = MusicPlayer(volume=50, on_status=events.append)
    fake = _FakeMusic()
    player._ready = True
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=fake))
    monkeypatch.setattr(player, '_start_track_analysis', lambda path: (_ for _ in ()).throw(OSError('FFT impossible')))
    path = tmp_path / 'épreuve sonore.mp3'
    assert player.play(path)
    assert fake.played == 1
    assert player.current == path
    assert any('secondaire' in event for event in events)
    # No real mixer was started: avoid pygame.quit in fake backend.
    player._ready = False
    player._pygame = None
    player.close()


def test_ffmpeg_timeout_does_not_leave_temporary_audio_files(tmp_path, monkeypatch):
    folder = tmp_path / 'temp'
    folder.mkdir()
    monkeypatch.setattr('tempfile.gettempdir', lambda: str(folder))
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: str(tmp_path / 'ffmpeg.exe')))
    class StalledProcess:
        returncode = None
        killed = False

        def communicate(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired(['ffmpeg'], timeout)
            return None, ''

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = StalledProcess()
    monkeypatch.setattr(subprocess, 'Popen', lambda *_a, **_kw: process)
    player = MusicPlayer()
    with pytest.raises(RuntimeError, match='interrompue'):
        player._transcode_playback_fallback(tmp_path / 'mauvais fichier.opus')
    assert process.killed
    assert list((folder / 'DofusicAudioFallback').iterdir()) == []
