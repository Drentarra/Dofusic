"""Temporal confirmation and coordinate-independent text reuse."""
from __future__ import annotations

from dataclasses import replace

import pytest

from dofusic.location.decision import DecisionEngine
from dofusic.location.repository import DofusRepository
from dofusic.location.resolver import LocationResolver
from dofusic.models import Coordinates, DecisionState, LocationEvidence, LocationKind, LocationMatch, LocationRecord
from tools.database_maintenance import build_database


def _evidence(timestamp):
    place = LocationRecord(1, 'Astrub', LocationKind.AREA)
    match = LocationMatch(place, 95.0, 'fuzzy', 'Astrub')
    return LocationEvidence('Astrub', .85, match, None, 95., None, 0., timestamp)


@pytest.mark.parametrize('required', [2, 6, 10])
def test_every_supported_confirmation_count_can_complete(required):
    engine = DecisionEngine(required_confirmations=required)
    for index in range(required):
        result = engine.evaluate(_evidence(10.0 + index * .6))
        assert result.state is (DecisionState.CONFIRMED if index == required - 1 else DecisionState.PENDING)


def test_duplicate_observation_is_not_an_independent_confirmation():
    engine = DecisionEngine()
    first = _evidence(10.0)
    assert engine.evaluate(first).state is DecisionState.PENDING
    assert engine.evaluate(first).state is DecisionState.UNKNOWN
    assert len(engine.history) == 1
    assert engine.evaluate(replace(first, timestamp=10.6)).state is DecisionState.CONFIRMED


@pytest.mark.parametrize('timestamp', [float('nan'), float('inf'), float('-inf')])
def test_invalid_timestamps_do_not_poison_confirmation_history(timestamp):
    engine = DecisionEngine()
    assert engine.evaluate(_evidence(timestamp)).state is DecisionState.UNKNOWN
    assert not engine.history
    assert engine.evaluate(_evidence(10.0)).state is DecisionState.PENDING
    assert engine.evaluate(_evidence(10.6)).state is DecisionState.CONFIRMED


def test_a_gap_between_consecutive_observations_breaks_streak():
    engine = DecisionEngine(required_confirmations=3)
    for timestamp in (10., 10.6, 15.):
        assert engine.evaluate(_evidence(timestamp)).state is DecisionState.PENDING
    assert engine.evaluate(_evidence(15.6)).state is DecisionState.PENDING
    assert engine.evaluate(_evidence(16.2)).state is DecisionState.CONFIRMED


def test_repeated_text_reuses_matches_but_reranks_new_coordinates(tmp_path, monkeypatch):
    db = build_database(tmp_path / 'places.sqlite', areas={1: 'North', 2: 'South'},
                        subareas={11: 'Square', 22: 'Square'}, subarea_to_area={11: 1, 22: 2},
                        map_coordinates=[(1, 0, 0, 11), (2, 20, 20, 22)])
    repo = DofusRepository(db)
    resolver = LocationResolver(repo)
    original = resolver._best_text_match
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(resolver, '_best_text_match', counted)
    first = resolver.resolve('Square', .9, Coordinates(0, 0), timestamp=1.)
    first_count = len(calls)
    assert first.top1.location.id == 11
    second = resolver.resolve('Square', .8, Coordinates(20, 20), timestamp=2.)
    assert len(calls) == first_count
    assert second.top1.location.id == 22
    assert second == LocationResolver(repo).resolve('Square', .8, Coordinates(20, 20), timestamp=2.)
