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


def test_library_reuses_fuzzy_matches_and_invalidates_cache_on_rescan(tmp_path):
    from dofusic.audio.library import MusicLibrary
    from dofusic.models import LocationKind, LocationRecord

    place = LocationRecord(7, 'Astrub', LocationKind.AREA)

    class Repository:
        def all_locations(self):
            return (place,)

    first = tmp_path / 'Astrub.opus'
    first.write_bytes(b'fake')
    library = MusicLibrary(Repository(), tmp_path)
    calculations = []
    original = library._rank_track_candidates

    def counting(anchors):
        calculations.append(anchors)
        return original(anchors)

    library._rank_track_candidates = counting
    assert library.resolve(place, raw_text='Astrub') == first
    assert library.resolve(place, raw_text='Astrub') == first
    assert len(calculations) == 1
    library.scan()
    assert library.resolve(place, raw_text='Astrub') == first
    assert len(calculations) == 2


def test_music_library_generic_cycle_is_not_cached(tmp_path):
    from dofusic.audio.library import MusicLibrary

    class Repository:
        def all_locations(self):
            return ()

    for stem in ('Musique', 'Musique1'):
        (tmp_path / f'{stem}.opus').write_bytes(b'fake')
    library = MusicLibrary(Repository(), tmp_path)
    assert library.resolve(None, combat=False) in library.generic_tracks
    first = library.resolve(None, combat=False)
    assert library.resolve(None, combat=False) == first
    second = library.resolve(None, combat=False, new_generic_cycle=True)
    assert second != first
