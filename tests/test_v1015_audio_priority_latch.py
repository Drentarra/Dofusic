"""Regression tests for confirmed combat/dungeon soundtrack priority.

Only a proven dungeon before battle may override the generic combat rule.
OCR of a masked location during a battle is diagnostic, not audio evidence.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dofusic import app
from dofusic.models import DecisionResult, DecisionState, LocationEvidence, LocationKind, LocationRecord, ZoneOCRResult
from dofusic.vision.combat import CombatObservation, CombatStateTracker
from dofusic.vision.layout import HUDGeometry

NORMAL = LocationRecord(95, "Astrub (Cité d'Astrub)", LocationKind.SUBAREA)
DUNGEON = LocationRecord(333, 'Grange du Tournesol Affamé', LocationKind.SUBAREA)


def _controller(tmp_path: Path, current: LocationRecord | None):
    control = app.DofusicController.__new__(app.DofusicController)
    control.current_location = current
    control._confirmed_zone_raw_text = current.name if current else ''
    control.state = app.ControllerState(
        location=current.name if current else 'En attente',
        location_key=current.canonical_key if current else '',
        place=current.name if current else '—',
        ocr_text=current.name if current else '',
        in_combat=False, automatic_detection_unlocked=True,
    )
    control.logger = logging.getLogger('test.audio-priority')
    control.hud_geometry = HUDGeometry()
    control.combat_tracker = CombatStateTracker(required_confirmations=3)
    control._audio_override_active = False
    control._automatic_detection_unlocked = True
    control._game_process_running = True
    control._zone_min_observed_at = float('-inf')
    control.last_zone_observed_at = float('-inf')
    control.last_position_at = 0
    control.unknown_since = None
    control.next_confirmation_at = 0
    control.config = SimpleNamespace(debug=False, position_context_max_age_sec=4,
                                     pending_confirmation_interval_sec=0.35)
    control.zone_change_gate = Mock()
    control._zone_text_continuity = None
    control._desired_local_track = None
    control._last_audio_retry_at = float('-inf')
    control.context_position = None
    control.last_evidence = None
    control.player = Mock()
    control.player.play.return_value = True
    control.player.current = None
    control.repository = Mock()
    control.resolver = Mock()
    control.decision = Mock()
    tracks = {mode: tmp_path / (mode + '.mp3') for mode in ('zone', 'combat', 'dungeon')}
    def resolve(location, raw_text='', *, combat=False, new_generic_cycle=False):
        return tracks['dungeon' if location == DUNGEON else 'combat' if combat else 'zone']
    control.music_library = SimpleNamespace(
        dungeon_catalog=SimpleNamespace(is_dungeon=lambda loc: loc == DUNGEON),
        resolve=Mock(side_effect=resolve),
    )
    return control, tracks


def _combat(control, monkeypatch, present: bool | None, *, count: int = 3):
    observation = CombatObservation(present, .94 if present is not None else 0.,
                                    1)
    monkeypatch.setattr(app, 'analyze_combat_toolbar', lambda *_: observation)
    for _ in range(count):
        control._update_combat_from_toolbar(None)


def _zone(control, text: str, when=900.):
    control.handle_zone_result(ZoneOCRResult(text, .99, 22., 'fake'),
                               now=when, observed_at=when)


def test_normal_combat_ignores_false_dungeon_ocr_and_restores_original_zone(tmp_path, monkeypatch):
    control, tracks = _controller(tmp_path, NORMAL)
    _combat(control, monkeypatch, True)
    assert control.state.in_combat
    assert control.player.play.call_args.args[0] == tracks['combat']
    before = control.player.play.call_count
    _zone(control, DUNGEON.name)
    assert control.state.ocr_text == DUNGEON.name  # UI/diagnostics still see raw OCR
    assert control.state.decision_state == DecisionState.UNKNOWN.value
    assert control.current_location == NORMAL  # audio context is still confirmed normal place
    assert control.state.location == NORMAL.name
    assert control.player.play.call_count == before
    control.resolver.resolve.assert_not_called()
    control.decision.evaluate.assert_not_called()
    _combat(control, monkeypatch, False)
    assert control.state.in_combat is False
    assert control.player.play.call_args.args[0] == tracks['zone']
    assert control.player.play.call_count == before + 1
    assert control.state.ocr_text == NORMAL.name
    assert control.music_library.resolve.call_args.kwargs['raw_text'] == NORMAL.name
    control.zone_change_gate.reset.assert_called_once()


def test_dungeon_combat_remains_visible_but_audio_never_switches(tmp_path, monkeypatch):
    control, tracks = _controller(tmp_path, DUNGEON)
    control._play_location(DUNGEON, DUNGEON.name)
    before = control.player.play.call_count
    assert control.player.play.call_args.args[0] == tracks['dungeon']
    _combat(control, monkeypatch, True)
    assert control.state.in_combat is True
    assert control.state.combat_confidence == pytest.approx(.94)
    assert control.player.play.call_count == before
    _zone(control, '')
    _zone(control, 'Astrub (fausse zone)', 901.)
    assert control.current_location == DUNGEON
    assert control.player.play.call_count == before
    _combat(control, monkeypatch, None, count=6)
    assert control.state.in_combat is True
    _combat(control, monkeypatch, False)
    assert control.state.in_combat is False
    assert control.player.play.call_count == before


def test_unconfirmed_zone_not_promoted_from_combat(tmp_path, monkeypatch):
    control, tracks = _controller(tmp_path, None)
    _combat(control, monkeypatch, True)
    before = control.player.play.call_count
    assert control.player.play.call_args.args[0] == tracks['combat']
    _zone(control, DUNGEON.name)
    assert control.current_location is None
    assert control.player.play.call_count == before


def test_late_zone_result_captured_while_fighting_cannot_cross_end_boundary(tmp_path, monkeypatch):
    control, tracks = _controller(tmp_path, NORMAL)
    monkeypatch.setattr(app.time, 'monotonic', lambda: 1000.)
    _combat(control, monkeypatch, True)
    _combat(control, monkeypatch, False)
    calls = control.music_library.resolve.call_count
    _zone(control, DUNGEON.name, 999.)  # returned only after combat ends
    assert control.current_location == NORMAL
    assert control.music_library.resolve.call_count == calls
    assert control.last_zone_observed_at == float('-inf')
    evidence = LocationEvidence(DUNGEON.name, .99, None, None, 100., None, 0., 1001.)
    control.resolver.resolve.return_value = evidence
    control.decision.evaluate.return_value = DecisionResult(
        DecisionState.CONFIRMED, DUNGEON, evidence, 'nouveau lieu confirmé après combat',
    )
    _zone(control, DUNGEON.name, 1001.)
    control.resolver.resolve.assert_called_once()
    assert control.current_location == DUNGEON
    assert control.player.play.call_args.args[0] == tracks['dungeon']


def test_play_location_never_overwrites_visual_combat_inside_dungeon(tmp_path):
    control, tracks = _controller(tmp_path, DUNGEON)
    control.state.in_combat = True
    control._play_location(DUNGEON, DUNGEON.name)
    assert control.state.in_combat is True
    assert control.music_library.resolve.call_args.kwargs['combat'] is False
    assert control.player.play.call_args.args[0] == tracks['dungeon']


def test_combat_badge_reports_combat_in_confirmed_dungeon(tmp_path):
    from dofusic.ui.main_window import combat_badge
    control, _ = _controller(tmp_path, DUNGEON)
    control.state.window_found = True
    control.state.in_combat = True
    assert combat_badge(control.state) == ('Combat détecté', 'combat')
