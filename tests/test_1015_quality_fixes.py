"""Independent regressions: invalid OCR inputs and atomic SQLite replacement."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.location.decision import DecisionEngine
from dofusic.location.repository import DofusRepository
from dofusic.location.resolver import LocationResolver
from dofusic.models import Coordinates, DecisionState
from dofusic.numeric import clamp_confidence
from dofusic.vision.engines import PositionOCREngine, ZoneOCREngine, _output_items
from dofusic.vision.position import PositionCandidate, select_position_candidate
from tools.database_maintenance import build_database


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), 'NaN',
                                   'Infinity', None, 'invalid', object()])
def test_non_finite_or_malformed_confidence_defaults_to_zero(value):
    assert clamp_confidence(value) == 0.0


@pytest.mark.parametrize('value, expected', [(2.0, 1.0), (-3.0, 0.0), (0.67, 0.67), (0, 0.0)])
def test_finite_confidences_are_clamped_without_changing_valid_values(value, expected):
    assert clamp_confidence(value) == expected


@pytest.mark.parametrize('score', [float('nan'), float('inf'), float('-inf'),
                                         None, 'unknown', object()])
def test_bad_ocr_score_is_not_promoted_to_certain_zone(score):
    assert _output_items(SimpleNamespace(txts=['Astrub'], scores=[score])) == ()


def test_valid_ocr_scores_and_numpy_arrays_remain_supported():
    output = SimpleNamespace(txts=np.asarray(['Astrub', 'Amakna']), scores=np.asarray([0.88, 1.5]))
    assert _output_items(output) == (('Astrub', 0.88), ('Amakna', 1.0))
    assert _output_items(SimpleNamespace(txts=None, scores=None)) == ()


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), float('-inf'), 'malformed'])
def test_resolver_rejects_invalid_confidence_and_never_confirms(confidence, tmp_path):
    db = build_database(tmp_path / 'zones.sqlite', areas={1: 'Astrub'},
                        subareas={}, subarea_to_area={})
    resolver = LocationResolver(DofusRepository(db))
    evidence = resolver.resolve('Astrub', confidence, timestamp=123.0)
    assert evidence.top1 is not None
    assert evidence.ocr_confidence == 0.0
    decision = DecisionEngine()
    assert decision.evaluate(evidence).state is DecisionState.UNKNOWN
    assert decision.evaluate(replace(evidence, timestamp=124.0)).state is DecisionState.UNKNOWN


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), float('-inf')])
def test_decision_engine_rejects_nonfinite_confidences_even_when_called_directly(confidence, tmp_path):
    db = build_database(tmp_path / 'zones.sqlite', areas={1: 'Astrub'},
                        subareas={}, subarea_to_area={})
    evidence = LocationResolver(DofusRepository(db)).resolve('Astrub', 0.9, timestamp=123.)
    decision = DecisionEngine()
    assert decision.evaluate(replace(evidence, ocr_confidence=confidence)).state is DecisionState.UNKNOWN
    assert decision.evaluate(replace(evidence, ocr_confidence=confidence, timestamp=124.)).state is DecisionState.UNKNOWN


def test_position_candidate_ignores_nan_and_infinite_readings():
    position = Coordinates(1, 2)
    bad = [PositionCandidate('1,2', position, value, 'color') for value in (math.nan, math.inf, -math.inf)]
    assert select_position_candidate(bad) is None
    good = PositionCandidate('3,4', Coordinates(3, 4), 0.55, 'contrast')
    assert select_position_candidate([*bad, good]) is good


def test_ocr_engine_threshold_config_sanitizes_nonfinite():
    assert ZoneOCREngine(fast_accept_confidence=float('nan')).fast_accept_confidence == 0.76
    assert PositionOCREngine(fast_accept_confidence=float('inf')).fast_accept_confidence == 0.84


def test_database_build_preserves_existing_data_on_bad_rows(tmp_path):
    path = build_database(tmp_path / 'zones.sqlite', areas={1: 'Ancienne zone'},
                          subareas={}, subarea_to_area={})
    before = path.read_bytes()
    with pytest.raises(ValueError):
        build_database(path, areas={'bad-id': 'Zone corrompue'}, subareas={}, subarea_to_area={})
    assert path.read_bytes() == before
    assert DofusRepository(path).all_locations()[0].name == 'Ancienne zone'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['zones.sqlite']


def test_database_build_preserves_existing_data_on_replace_failure(tmp_path, monkeypatch):
    path = build_database(tmp_path / 'zones.sqlite', areas={1: 'Ancienne zone'},
                          subareas={}, subarea_to_area={})
    before = path.read_bytes()
    from tools import database_maintenance

    def refused_replace(_source, _target):
        raise PermissionError('destination en lecture seule')

    monkeypatch.setattr(database_maintenance.os, 'replace', refused_replace)
    with pytest.raises(PermissionError, match='lecture seule'):
        build_database(path, areas={2: 'Nouvelle zone'}, subareas={}, subarea_to_area={})
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['zones.sqlite']


def test_database_build_publishes_complete_valid_data_and_metadata(tmp_path):
    path = build_database(tmp_path / 'zones.sqlite', areas={1: 'Ancienne zone'},
                          subareas={}, subarea_to_area={})
    build_database(path, areas={7: 'Amakna'}, subareas={2: 'Plaine'},
                   subarea_to_area={2: 7}, map_coordinates=[(20, 5, -6, 2)],
                   place_aliases=[('Plaine', 'subarea', 2, 100)])
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT id, name FROM areas').fetchall() == [(7, 'Amakna')]
        assert conn.execute('SELECT map_id,x,y,subarea_id,area_id FROM map_coordinates').fetchall() == [(20, 5, -6, 2, 7)]
        assert dict(conn.execute('SELECT key,value FROM metadata'))['coordinates_count'] == '1'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['zones.sqlite']


def test_database_build_does_not_leave_partial_destination_if_never_existed(tmp_path):
    path = tmp_path / 'new.sqlite'
    with pytest.raises(ValueError):
        build_database(path, areas={1: 'ok'}, subareas={'invalid': 'bad'}, subarea_to_area={})
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_large_audio_files_receive_a_longer_but_bounded_ffmpeg_watchdog(tmp_path):
    from dofusic.audio.player import MusicPlayer

    music = tmp_path / 'longue piste.opus'
    assert MusicPlayer._conversion_timeout_seconds(music) == 15.0  # unreadable source
    with music.open('wb') as stream:
        stream.truncate(8 * 1024 * 1024)
    assert MusicPlayer._conversion_timeout_seconds(music) == 16.0
    with music.open('wb') as stream:
        stream.truncate(90 * 1024 * 1024)
    assert MusicPlayer._conversion_timeout_seconds(music) == 120.0


def test_windows_process_detection_mock_enumerates_and_closes_snapshot(monkeypatch):
    """Exercise the native API adapter without requiring a Windows test host."""
    import ctypes

    from dofusic.capture import process_presence as process

    events = []
    names = iter(('Explorer.EXE', 'Dofus.exe'))

    class APIFunction:
        def __init__(self, method):
            self._method = method
        def __call__(self, *args):
            return self._method(*args)

    def fill(_handle, pointer):
        name = next(names, None)
        if name is None:
            return False
        ctypes.cast(pointer, ctypes.POINTER(process._PROCESSENTRY32W)).contents.szExeFile = name
        return True

    fake = SimpleNamespace(
        CreateToolhelp32Snapshot=APIFunction(lambda *_a: 777),
        Process32FirstW=APIFunction(fill),
        Process32NextW=APIFunction(fill),
        CloseHandle=APIFunction(lambda handle: events.append(handle) or True),
    )
    monkeypatch.setattr(process.os, 'name', 'nt')
    monkeypatch.setattr(process.ctypes, 'WinDLL', lambda *_a, **_kw: fake, raising=False)
    assert process.is_process_running('dofus.EXE') is True
    assert events == [777]


def test_windows_process_detection_invalid_snapshot_returns_false(monkeypatch):
    from dofusic.capture import process_presence as process
    calls = []

    class APIFunction:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    fake = SimpleNamespace(
        CreateToolhelp32Snapshot=APIFunction(lambda *_a: -1),
        Process32FirstW=APIFunction(lambda *_a: pytest.fail('no scanning')),
        Process32NextW=APIFunction(lambda *_a: pytest.fail('no scanning')),
        CloseHandle=APIFunction(lambda handle: calls.append(handle)),
    )
    monkeypatch.setattr(process.os, 'name', 'nt')
    monkeypatch.setattr(process.ctypes, 'WinDLL', lambda *_a, **_kw: fake, raising=False)
    assert process.is_process_running('Dofus.exe') is False
    assert calls == []
