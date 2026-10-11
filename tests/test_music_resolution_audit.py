from types import SimpleNamespace
from pathlib import Path

import pytest

from dofusic.audio.library import MusicLibrary
from dofusic.models import LocationKind, LocationRecord


@pytest.mark.parametrize('dungeon,has_combat_music', [(True, True), (True, False), (False, False)])
def test_combat_normal_fallback_preserves_exploration_cycle(tmp_path, dungeon, has_combat_music):
    location = LocationRecord(1, 'Donjon du Test' if dungeon else 'Rivage', LocationKind.SUBAREA)
    repository = SimpleNamespace(all_locations=lambda: (location,))
    for name in ('Musique 1.opus', 'Musique 2.opus'):
        (tmp_path / name).touch()
    if has_combat_music:
        (tmp_path / 'Musique Combat.opus').touch()
    library = MusicLibrary(repository, tmp_path)
    library._random = SimpleNamespace(choice=lambda pool: pool[0])

    exploration = library.resolve(location, new_generic_cycle=True)
    assert library.resolve(location, combat=True, new_generic_cycle=True) == exploration
    assert library.resolve(location) == exploration
    # A genuine new exploration cycle still rotates its generic choice.
    assert library.resolve(location, new_generic_cycle=True) != exploration


def test_repeated_music_resolution_reuses_scores_and_scan_invalidates_results(tmp_path, monkeypatch):
    import dofusic.audio.library as module

    location = LocationRecord(1, 'Rivage', LocationKind.SUBAREA)
    (tmp_path / 'Musique.opus').touch()
    (tmp_path / 'Montagne.opus').touch()
    library = MusicLibrary(SimpleNamespace(all_locations=lambda: (location,)), tmp_path)
    calls = []
    similarity = module.text_similarity

    def observed_similarity(target, observed):
        calls.append((target, observed))
        return similarity(target, observed)

    monkeypatch.setattr(module, 'text_similarity', observed_similarity)
    generic = library.resolve(location)
    first_calls = len(calls)
    assert first_calls > 0
    for _ in range(10):
        assert library.resolve(location) == generic
    assert len(calls) == first_calls

    specific = tmp_path / 'Rivage.opus'
    specific.touch()
    library.scan()
    assert library.resolve(location) == specific
    specific.unlink()
    library.scan()
    assert library.resolve(location) == generic


def test_combat_namespace_is_excluded_from_exploration_candidates(tmp_path):
    normal = tmp_path / 'Astrub.opus'
    combat = tmp_path / 'Musique Combat Astrub.opus'
    normal.touch()
    combat.touch()
    library = MusicLibrary(SimpleNamespace(all_locations=lambda: ()), tmp_path)
    assert library.tracks == (normal,)
    assert combat in library.all_tracks


@pytest.mark.parametrize('prefix', ["Expédition de l'Audace", 'Expédition de la Bravoure'])
def test_expedition_preserves_base_dungeon_music_and_boss_alias(tmp_path, prefix):
    import json
    from dofusic.audio.dungeons import DungeonCatalog

    catalog_file = tmp_path / 'dungeons.json'
    catalog_file.write_text(json.dumps({'dungeons': [{'name': 'Grotte du Bworker', 'bosses': ['Bworker']}]}))
    location = LocationRecord(1, f'{prefix} - Grotte du Bworker', LocationKind.SUBAREA)
    normal = tmp_path / 'Grotte du Bworker.opus'
    normal.touch()
    (tmp_path / 'Musique Combat.opus').touch()
    catalog = DungeonCatalog(catalog_file)
    library = MusicLibrary(SimpleNamespace(all_locations=lambda: (location,)), tmp_path, dungeon_catalog=catalog)
    assert catalog.is_dungeon(location)
    assert 'Bworker' in catalog.audio_aliases(location)
    assert library.resolve(location, combat=True) == normal


def test_catalog_alias_preserves_koulosse_dungeon_combat_music(tmp_path):
    from dofusic.audio.dungeons import DungeonCatalog

    catalog = DungeonCatalog(Path(__file__).resolve().parents[1] / 'Data/dungeons.json')
    location = LocationRecord(1, 'Antre du Koulosse', LocationKind.SUBAREA)
    normal = tmp_path / 'Caverne du Koulosse.opus'
    normal.touch()
    (tmp_path / 'Musique Combat.opus').touch()
    library = MusicLibrary(SimpleNamespace(all_locations=lambda: (location,)), tmp_path, dungeon_catalog=catalog)
    assert catalog.is_dungeon(location)
    assert 'Koulosse' in catalog.audio_aliases(location)
    assert library.resolve(location, combat=True) == normal


def test_expedition_catalog_alias_keeps_sakai_boss_combat_mapping(tmp_path):
    from dofusic.audio.dungeons import DungeonCatalog

    catalog = DungeonCatalog(Path(__file__).resolve().parents[1] / 'Data/dungeons.json')
    location = LocationRecord(1, "Expédition de la Bravoure - Donjon de la mine de Sakaï", LocationKind.SUBAREA)
    combat = tmp_path / 'Musique Combat Grolloum.opus'
    combat.touch()
    (tmp_path / 'Musique Combat.opus').touch()
    library = MusicLibrary(SimpleNamespace(all_locations=lambda: (location,)), tmp_path, dungeon_catalog=catalog)
    assert 'Grolloum' in catalog.audio_aliases(location)
    assert library.resolve(location, combat=True) == combat


def test_every_bundled_expedition_inherits_its_dungeon_catalog_entry():
    from dofusic.audio.dungeons import DungeonCatalog
    from dofusic.location.repository import DofusRepository

    data = Path(__file__).resolve().parents[1] / 'Data'
    catalog = DungeonCatalog(data / 'dungeons.json')
    locations = DofusRepository(data / 'dofus_data.sqlite').all_locations()
    expeditions = [location for location in locations if location.name.startswith('Expédition ')]
    assert expeditions
    assert [location.name for location in expeditions if not catalog.is_dungeon(location)] == []
    for prefix in ("Expédition de l'Audace", 'Expédition de la Bravoure'):
        minotot = LocationRecord(1, f'{prefix} - Salle du Minotot', LocationKind.SUBAREA)
        assert 'Minotot' in catalog.audio_aliases(minotot)


def test_combat_transition_cannot_use_unconfirmed_ocr_text_for_dungeon_music(tmp_path):
    import logging
    from dofusic.app import ControllerState, DofusicController
    from dofusic.vision.combat import CombatStateTracker
    from test_combat_shapes import collapsed_toolbar

    location = LocationRecord(1, 'Donjon du Test', LocationKind.SUBAREA)
    normal = tmp_path / 'Donjon du Test.opus'
    normal.touch()
    (tmp_path / 'Montagne interminable.opus').touch()
    controller = DofusicController.__new__(DofusicController)
    controller.state = ControllerState()
    controller.current_location = location
    controller._confirmed_zone_text = location.name
    controller.state.ocr_text = 'Montagne interminable'  # A pending/rejected OCR observation.
    controller.hud_geometry = None
    controller.combat_tracker = CombatStateTracker()
    controller.logger = logging.getLogger('music-audit')
    controller._game_process_running = True
    controller.music_library = MusicLibrary(SimpleNamespace(all_locations=lambda: (location,)), tmp_path)
    played = []
    controller.player = SimpleNamespace(play=lambda path: played.append(path) or True)
    for _ in range(3):
        controller._update_combat_from_toolbar(collapsed_toolbar()[:40, :320])
    assert controller.state.in_combat
    assert played == [normal]


@pytest.mark.parametrize('rejection', ['confidence', 'score', 'margin', 'future'])
def test_rejected_location_observation_cannot_confirm_next_acceptable_one(rejection):
    from dataclasses import replace
    from dofusic.location.decision import DecisionEngine
    from dofusic.models import DecisionState, LocationEvidence, LocationMatch, MatchMode

    place = LocationRecord(1, 'Rivage', LocationKind.SUBAREA)
    match = LocationMatch(place, 95, 'fuzzy', place.name, mode=MatchMode.FUZZY)
    evidence = LocationEvidence(place.name, 0.7, match, None, 95, None, 0, 10.0)
    rejected = evidence
    if rejection == 'confidence':
        rejected = replace(evidence, ocr_confidence=0.1)
    elif rejection == 'score':
        rejected = replace(evidence, top1=replace(match, score=70))
    elif rejection == 'margin':
        rival = LocationRecord(2, 'Autre rivage', LocationKind.SUBAREA)
        rejected = replace(evidence, top2=replace(match, location=rival), margin=1)
    else:
        rejected = replace(evidence, timestamp=20)
    engine = DecisionEngine()
    engine.evaluate(rejected)
    assert engine.evaluate(replace(evidence, timestamp=10.5)).state is DecisionState.PENDING
    assert engine.evaluate(replace(evidence, timestamp=11)).state is DecisionState.CONFIRMED
