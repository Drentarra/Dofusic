from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
import time

from dofusic.online.models import OnlineTrack
from dofusic.online.playback import PlaybackController


class _Library:
    all_tracks = ()
    def scan(self):
        pass


class _Player:
    def __init__(self):
        self.busy = True
        self.levels = tuple((i + 1) / 24 for i in range(24))
    def is_playing(self):
        return self.busy
    def spectrum_levels(self, count=24):
        return tuple(self.levels[:count])


class _Controller:
    def __init__(self):
        self.music_library = _Library()
        self.player = _Player()
        self.calls = []
    def play_online_track(self, path, title):
        self.calls.append(('online-file', Path(path), title))
        self.player.busy = True
        return True
    def resume_local_audio(self):
        self.calls.append(('resume',))
        return True


class _MediaCache:
    def __init__(self, tmp_path):
        self.tmp_path = Path(tmp_path)
    def ensure_cached(self, track):
        path = self.tmp_path / f'{track.video_id}.mp3'
        path.write_bytes(b'audio')
        return path


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future
    def shutdown(self, **kwargs):
        pass


class _Config:
    volume = 70
    mute = False


def _playback(tmp_path):
    return PlaybackController(_Controller(), _Config(), _MediaCache(tmp_path), executor=_ImmediateExecutor())


def test_online_track_is_not_advanced_while_real_audio_player_is_busy(tmp_path):
    playback = _playback(tmp_path)
    track = OnlineTrack('abcdefghijk', 'Titre')
    playback.play_now(track)
    playback.controller.player.busy = True
    playback.tick(now=time.monotonic() + 1.0)
    assert playback.current is not None
    assert playback.current.online_track == track


def test_online_queue_advances_when_real_audio_player_finishes(tmp_path):
    playback = _playback(tmp_path)
    first = OnlineTrack('abcdefghijk', 'Premier')
    second = OnlineTrack('lmnopqrstuv', 'Second')
    playback.play_now(first)
    playback.enqueue(second)
    playback.controller.player.busy = False
    playback.tick(now=time.monotonic() + 1.0)
    assert playback.current is not None
    assert playback.current.online_track == second
    assert playback.controller.calls[-1][2] == 'Second'


def test_online_visualizer_uses_real_audio_player_fft(tmp_path):
    playback = _playback(tmp_path)
    playback.play_now(OnlineTrack('abcdefghijk', 'Titre'))
    levels = playback.visualizer_levels(24)
    assert levels == tuple((i + 1) / 24 for i in range(24))
