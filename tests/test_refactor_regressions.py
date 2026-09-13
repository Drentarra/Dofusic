from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


def test_volume_gesture_drag_must_start_on_slider():
    from dofusic.ui.volume import VolumeGesture

    gesture = VolumeGesture(x1=55, x2=415, y=500, hit_half_height=10)
    assert gesture.press(500, 500) is False
    assert gesture.drag(200, 500) is None

    assert gesture.press(200, 500) is True
    assert gesture.drag(415, 500) == 100
    gesture.release()
    assert gesture.drag(55, 500) is None


def test_mute_visual_is_active_when_muted_or_volume_zero():
    from dofusic.ui.volume import mute_visual_active

    assert mute_visual_active(muted=True, volume=80) is True
    assert mute_visual_active(muted=False, volume=0) is True
    assert mute_visual_active(muted=False, volume=1) is False


def test_live_online_typing_requests_only_suggestions_not_full_search():
    from dofusic.ui.music_window import online_typing_actions

    assert online_typing_actions('poker face', suggestions_enabled=True) == ('suggest',)
    assert online_typing_actions('poker face', suggestions_enabled=False) == tuple()
    assert online_typing_actions('p', suggestions_enabled=True) == tuple()


def _stroke(image, x1, x2, y1, y2):
    image[y1:y2, x1:x2] = 255


def test_position_overlay_keeps_second_y_digit_independent_of_suffix_pixels():
    from dofusic.models import PositionOCRResult
    from dofusic.vision.position_overlay import coordinate_overlay_width

    result = PositionOCRResult('3, -20 - Niveau 200', 3, -20, 0.99, 1.0, 'test')
    width = coordinate_overlay_width(result, roi_height=29)

    # Width is derived from the complete accepted token `3, -20`; no pixel-run
    # segmentation can cut the final zero or include the following `Niveau`.
    assert 60 <= width <= 76

def test_position_reference_roi_is_wide_enough_before_dynamic_trim():
    from dofusic.vision.layout import HUDGeometry

    assert HUDGeometry().position_width >= 160


def test_dungeon_catalog_recognizes_real_dungeon_and_expedition_alias(tmp_path):
    from dofusic.audio.dungeons import DungeonCatalog
    from dofusic.models import LocationKind, LocationRecord

    catalog_file = tmp_path / 'dungeons.json'
    catalog_file.write_text(
        '{"dungeons":[{"name":"Grange du Tournesol Affamé","bosses":["Tournesol Affamé"]}]}',
        encoding='utf-8',
    )
    catalog = DungeonCatalog(catalog_file)
    normal = LocationRecord(1, 'Grange du Tournesol Affamé', LocationKind.SUBAREA, 2, 'Astrub')
    expedition = LocationRecord(2, 'Expédition - Grange du Tournesol Affamé', LocationKind.SUBAREA, 3, 'Expéditions')

    assert catalog.is_dungeon(normal)
    assert catalog.is_dungeon(expedition)
    assert 'Tournesol Affamé' in catalog.audio_aliases(normal)
    assert 'Tournesol Affamé' in catalog.audio_aliases(expedition)


def test_dungeon_combat_keeps_normal_dungeon_music_when_no_specific_combat_track(tmp_path):
    from dofusic.audio.dungeons import DungeonCatalog
    from dofusic.audio.library import MusicLibrary
    from dofusic.models import LocationKind, LocationRecord

    class Repository:
        def __init__(self, location):
            self.location = location
        def all_locations(self):
            return (self.location,)
        def parent_area(self, _location):
            return LocationRecord(10, 'Astrub', LocationKind.AREA)

    location = LocationRecord(1, 'Grange du Tournesol Affamé', LocationKind.SUBAREA, 10, 'Astrub')
    (tmp_path / 'Grange du Tournesol Affamé.mp3').write_bytes(b'x')
    (tmp_path / 'Musique Combat.mp3').write_bytes(b'x')
    catalog_file = tmp_path / 'dungeons.json'
    catalog_file.write_text(
        '{"dungeons":[{"name":"Grange du Tournesol Affamé","bosses":["Tournesol Affamé"]}]}',
        encoding='utf-8',
    )
    library = MusicLibrary(Repository(location), tmp_path, dungeon_catalog=DungeonCatalog(catalog_file))

    resolved = library.resolve(location, combat=True)
    assert resolved is not None
    assert resolved.stem == 'Grange du Tournesol Affamé'


def test_dungeon_specific_combat_track_beats_dungeon_normal_music(tmp_path):
    from dofusic.audio.dungeons import DungeonCatalog
    from dofusic.audio.library import MusicLibrary
    from dofusic.models import LocationKind, LocationRecord

    class Repository:
        def __init__(self, location):
            self.location = location
        def all_locations(self):
            return (self.location,)
        def parent_area(self, _location):
            return LocationRecord(10, 'Astrub', LocationKind.AREA)

    location = LocationRecord(1, 'Grange du Tournesol Affamé', LocationKind.SUBAREA, 10, 'Astrub')
    (tmp_path / 'Grange du Tournesol Affamé.mp3').write_bytes(b'x')
    (tmp_path / 'Musique Combat Tournesol Affamé.mp3').write_bytes(b'x')
    (tmp_path / 'Musique Combat.mp3').write_bytes(b'x')
    catalog_file = tmp_path / 'dungeons.json'
    catalog_file.write_text(
        '{"dungeons":[{"name":"Grange du Tournesol Affamé","bosses":["Tournesol Affamé"]}]}',
        encoding='utf-8',
    )
    library = MusicLibrary(Repository(location), tmp_path, dungeon_catalog=DungeonCatalog(catalog_file))

    resolved = library.resolve(location, combat=True)
    assert resolved is not None
    assert resolved.stem == 'Musique Combat Tournesol Affamé'


def test_track_loudness_gain_is_bounded_and_targets_common_level():
    from dofusic.audio.player import loudness_gain_from_dbfs

    # Quiet track receives boost; loud track is attenuated, with bounded gain.
    assert 1.0 < loudness_gain_from_dbfs(-24.0) <= 4.0
    assert 0.0 < loudness_gain_from_dbfs(-8.0) < 1.0
    assert loudness_gain_from_dbfs(None) == 1.0


def test_zone_crop_stops_at_first_real_gap_instead_of_following_scenery_noise():
    from dofusic.vision.layout import trim_zone_line

    image = np.zeros((34, 260, 3), dtype=np.uint8)
    # Representative connected glyph groups with normal small inter-letter gaps.
    for x in (3, 10, 17, 24, 31, 38, 45, 52, 59, 66, 73, 80, 87, 94, 101, 108, 115, 122):
        _stroke(image, x, x + 4, 7, 27)
    # Scenery/HUD noise starts only after a real whitespace gap.
    _stroke(image, 148, 153, 5, 29)
    _stroke(image, 160, 165, 4, 28)

    crop = trim_zone_line(image, padding=8)

    assert 130 <= crop.shape[1] < 148


def test_music_player_master_volume_is_scaled_by_current_track_normalization_gain():
    from dofusic.audio.player import MusicPlayer

    player = MusicPlayer(volume=50, spectrum_enabled=False)
    player._current_loudness_gain = 1.5
    assert player.effective_volume() == 0.75
    player._current_loudness_gain = 3.0
    assert player.effective_volume() == 1.0  # limiter / pygame ceiling
    player.muted = True
    assert player.effective_volume() == 0.0


def test_track_analysis_does_no_second_decode_when_visualizer_and_normalization_are_disabled(tmp_path):
    from dofusic.audio.player import MusicPlayer

    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=False)
    called = []
    player._start_loudness_analysis = lambda path: called.append(path)
    track = tmp_path / 'track.mp3'
    player._start_track_analysis(track)

    assert called == []


def test_track_analysis_can_opt_in_to_light_loudness_normalization(tmp_path):
    from dofusic.audio.player import MusicPlayer

    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=True)
    called = []
    player._start_loudness_analysis = lambda path: called.append(path)
    track = tmp_path / 'track.mp3'
    player._start_track_analysis(track)

    assert called == [track]
    assert player._analysis_thread is None


def test_online_discovery_search_and_suggestion_generations_are_independent():
    from concurrent.futures import Future
    from dofusic.online.search import SearchController

    class ManualExecutor:
        def __init__(self): self.jobs = []
        def submit(self, fn, *args, **kwargs):
            future = Future(); self.jobs.append(future); return future
        def shutdown(self, **kwargs): pass

    browser = type('Browser', (), {
        'search': lambda self, query, limit=8: tuple(),
        'suggestions': lambda self, query, limit=8: tuple(),
        'prewarm': lambda self: True,
    })()
    search_executor = ManualExecutor()
    suggestion_executor = ManualExecutor()
    state = SearchController(browser, executor=search_executor, suggestion_executor=suggestion_executor)

    search_revision = state.submit_search('orel san')
    suggestion_revision = state.submit_suggestions('orel')
    later_suggestion_revision = state.submit_suggestions('orelsan')

    assert search_revision == 1
    assert suggestion_revision != later_suggestion_revision
    assert state.snapshot().revision == search_revision
    assert state.snapshot().query == 'orel san'

def test_return_key_never_schedules_live_suggestions():
    from dofusic.ui.music_window import online_typing_actions

    assert online_typing_actions('orel san', suggestions_enabled=True, key='Return') == tuple()
    assert online_typing_actions('orel san', suggestions_enabled=True, key='KP_Enter') == tuple()
    assert online_typing_actions('orel san', suggestions_enabled=True, key='n') == ('suggest',)


def test_duplicate_running_search_is_reused_without_invalidating_its_generation():
    from concurrent.futures import Future
    from dofusic.online.search import SearchController

    class ManualExecutor:
        def __init__(self): self.jobs = []
        def submit(self, fn, *args, **kwargs):
            future = Future(); self.jobs.append(future); return future
        def shutdown(self, **kwargs): pass
    browser = type('Browser', (), {
        'search': lambda self, query, limit=8: tuple(),
        'suggestions': lambda self, query, limit=8: tuple(),
        'prewarm': lambda self: True,
    })()
    executor = ManualExecutor()
    state = SearchController(browser, executor=executor, suggestion_executor=executor)

    first = state.submit_search('Orel San')
    duplicate = state.submit_search('  orel   san ')

    assert duplicate == first
    assert len(executor.jobs) == 1

def test_online_enter_searches_only_and_never_autoplays_existing_result():
    from dofusic.online.models import OnlineTrack
    from dofusic.ui.music_window import MusicWindow

    class Var:
        def get(self):
            return 'grim salvo'

    class FakeWindow:
        _source_mode = 'online'
        search_var = Var()
        _results_query = 'grim salvo'
        _results = (OnlineTrack('abcdefghijk', 'THE GIVER'),)

        def __init__(self):
            self.searches = []
            self.plays = []

        def _begin_search(self, query, **kwargs):
            self.searches.append((query, kwargs))

        def _play(self, track):
            self.plays.append(track)

    fake = FakeWindow()
    MusicWindow._enter_play(fake)

    assert fake.searches == [('grim salvo', {})]
    assert fake.plays == []


def test_online_suggestion_selection_searches_only_without_autoplay():
    from dofusic.ui.music_window import MusicWindow

    class Var:
        def __init__(self):
            self.value = ''
        def set(self, value):
            self.value = value

    class Listbox:
        def curselection(self):
            return (0,)
        def get(self, index):
            assert index == 0
            return 'bob l\'éponge'

    class FakeWindow:
        suggestion_list = Listbox()
        search_var = Var()
        def __init__(self):
            self.searches = []
        def _begin_search(self, query, **kwargs):
            self.searches.append((query, kwargs))

    fake = FakeWindow()
    MusicWindow._suggestion_clicked(fake)

    assert fake.search_var.value == "bob l'éponge"
    assert fake.searches == [("bob l'éponge", {})]


def test_unknown_online_duration_is_not_displayed_as_zero_seconds():
    from dofusic.online.models import OnlineTrack

    assert OnlineTrack('abcdefghijk', 'Titre', duration_seconds=0).duration_text == '—'


def test_online_visualizer_does_not_fake_activity_when_player_has_no_spectrum(tmp_path):
    from concurrent.futures import Future
    from dofusic.online.models import OnlineTrack
    from dofusic.online.playback import PlaybackController

    class Player:
        def is_playing(self): return True
    class Controller:
        player = Player()
        music_library = type('Library', (), {'all_tracks': tuple(), 'scan': lambda self: None})()
        def play_online_track(self, path, title): return True
        def resume_local_audio(self): return True
    class Media:
        def ensure_cached(self, track):
            path = tmp_path / f'{track.video_id}.mp3'; path.write_bytes(b'audio'); return path
    class Executor:
        def submit(self, fn, *args, **kwargs):
            f=Future()
            try: f.set_result(fn(*args, **kwargs))
            except Exception as exc: f.set_exception(exc)
            return f
        def shutdown(self, **kwargs): pass
    config = type('Config', (), {'volume':70, 'mute':False})()
    playback = PlaybackController(Controller(), config, Media(), executor=Executor())

    assert playback.visualizer_levels(24) == (0.0,) * 24
    playback.play_now(OnlineTrack('abcdefghijk', 'Titre'))
    assert playback.visualizer_levels(24) == (0.0,) * 24

def test_main_visualizer_uses_online_activity_when_online_track_is_current():
    import inspect
    from dofusic.ui.main_window import MainWindow

    source = inspect.getsource(MainWindow._visualizer_tick)
    assert 'online_visualizer_levels' in source


def test_online_visualizer_is_not_synthetic_and_delegates_to_player(tmp_path):
    from concurrent.futures import Future
    from dofusic.online.models import OnlineTrack
    from dofusic.online.playback import PlaybackController

    class Library:
        all_tracks = ()
        def scan(self): pass
    class Player:
        def __init__(self): self.busy = True
        def is_playing(self): return self.busy
        def spectrum_levels(self, count=24): return (0.42,) * count
    class Controller:
        def __init__(self): self.music_library = Library(); self.player = Player()
        def play_online_track(self, path, title): return True
        def resume_local_audio(self): return True
    class Media:
        def ensure_cached(self, track):
            path = tmp_path / f'{track.video_id}.mp3'; path.write_bytes(b'audio'); return path
    class Config:
        volume = 70; mute = False
    class ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):
            f=Future()
            try: f.set_result(fn(*args, **kwargs))
            except Exception as exc: f.set_exception(exc)
            return f
        def shutdown(self, **kwargs): pass

    playback = PlaybackController(Controller(), Config(), Media(), executor=ImmediateExecutor())
    playback.play_now(OnlineTrack('abcdefghijk', 'Track A'))
    assert playback.visualizer_levels(24) == (0.42,) * 24

