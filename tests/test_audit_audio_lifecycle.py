"""Régressions de consommation mémoire et de fermeture FFmpeg."""
from pathlib import Path

import numpy as np

from dofusic.audio.player import MusicPlayer, _LOUDNESS_CACHE_LIMIT


def test_loudness_cache_is_bounded_and_keeps_most_recent_track():
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=True)
    for index in range(_LOUDNESS_CACHE_LIMIT + 50):
        player._remember_loudness_gain(Path(f'track-{index}.opus'), 1.25)
    assert len(player._loudness_gain_by_path) == _LOUDNESS_CACHE_LIMIT
    assert Path('track-0.opus') not in player._loudness_gain_by_path
    assert player._loudness_gain_by_path[Path(f'track-{_LOUDNESS_CACHE_LIMIT + 49}.opus')] == 1.25
    player._remember_loudness_gain(Path('track-50.opus'), 0.75)
    assert len(player._loudness_gain_by_path) == _LOUDNESS_CACHE_LIMIT
    assert player._loudness_gain_by_path[Path('track-50.opus')] == 0.75


def test_stop_terminates_an_active_decoder_even_when_audio_mixer_is_uninitialized():
    class Process:
        terminated = False

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

    player = MusicPlayer()
    process = Process()
    with player._spectrum_process_lock:
        player._spectrum_process = process
    player.stop()
    assert process.terminated is True
    assert player.current is None


def test_close_terminates_decoder_and_stops_analysis_thread():
    class Process:
        terminated = False

        def poll(self):
            return None if not self.terminated else 0

        def terminate(self):
            self.terminated = True

    player = MusicPlayer()
    process = Process()
    player._spectrum_process = process
    player.close()
    assert process.terminated
    assert player._analysis_stop.is_set()
