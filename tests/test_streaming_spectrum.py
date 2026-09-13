from __future__ import annotations

from pathlib import Path

import numpy as np

from dofusic.audio import player


def _tone(hz: float, seconds: float = 2.0, sample_rate: int = 32000) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate), dtype=np.float32) / float(sample_rate)
    return np.sin(2.0 * np.pi * hz * t).astype(np.float32)


def test_chunked_spectrum_matches_whole_track_shape_and_energy():
    sample_rate = 32000
    samples = _tone(120.0, sample_rate=sample_rate)
    chunks = [samples[i:i + 1379] for i in range(0, len(samples), 1379)]

    frames, sample_count = player.compute_spectrum_chunks(chunks, sample_rate, bands=24, fps=30)

    assert sample_count == len(samples)
    assert frames.shape[1] == 24
    assert 58 <= len(frames) <= 62
    # A 120 Hz pure tone belongs to the low-frequency side of the display.
    assert float(np.mean(frames[:, :7])) > float(np.mean(frames[:, 14:])) * 4.0


def test_spectrum_worker_source_no_longer_decodes_entire_track_with_pygame_sound():
    source = Path(player.__file__).read_text(encoding='utf-8')
    assert 'mixer.Sound(' not in source
    assert 'sndarray.array(' not in source


def test_disabled_visualizer_does_not_start_analysis_thread(tmp_path):
    music = tmp_path / 'dummy.mp3'
    music.write_bytes(b'not-used')
    instance = player.MusicPlayer(spectrum_enabled=False)
    instance._start_spectrum_analysis(music)
    assert instance._analysis_thread is None


def test_visualizer_can_be_disabled_live():
    instance = player.MusicPlayer(spectrum_enabled=True)
    instance.set_spectrum_enabled(False)
    assert instance.spectrum_enabled is False
    assert instance._spectrum_frames.size == 0


def test_visualizer_toggle_keeps_worker_reusable_until_player_close():
    instance = player.MusicPlayer(spectrum_enabled=True)
    instance.set_spectrum_enabled(False)
    assert instance._analysis_stop.is_set() is False
    instance.set_spectrum_enabled(True)
    assert instance._analysis_stop.is_set() is False
