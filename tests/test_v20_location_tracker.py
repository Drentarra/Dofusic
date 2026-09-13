from types import SimpleNamespace

from dofusic.app import DofusicController
from dofusic.models import (
    Coordinates, DecisionResult, DecisionState, LocationEvidence, LocationKind,
    LocationMatch, LocationRecord, MatchMode, ZoneOCRResult,
)


def _controller_for_decision(decision_result, evidence, current):
    class Resolver:
        def resolve(self, *_args, **_kwargs):
            return evidence

    class Decision:
        def evaluate(self, observed):
            return DecisionResult(decision_result, evidence.top1.location if evidence.top1 else None, observed, 'test')

    controller = DofusicController.__new__(DofusicController)
    controller.config = SimpleNamespace(
        position_context_max_age_sec=4.0,
        context_position_tolerance=2,
        debug=False,
        pending_confirmation_interval_sec=0.35,
        fallback_unknown_delay_sec=2.5,
    )
    controller.repository = SimpleNamespace(coordinate_candidates=lambda *_a, **_k: ())
    controller.resolver = Resolver()
    controller.decision = Decision()
    controller.state = SimpleNamespace(
        zone_elapsed_ms=0.0, position_x=5, position_y=-19, ocr_text='', confidence=0.0,
        decision_state='', debug_text='', location=current.name if current else '', location_key=current.canonical_key if current else '',
        place=current.name if current else '—', in_combat=False, display_theme='Astrub', status='', position_display='X=5 | Y=-19',
    )
    controller.last_position_at = 9.9
    controller.last_evidence = None
    controller.unknown_since = None
    controller.next_confirmation_at = 0.0
    controller.current_location = current
    controller.context_position = Coordinates(5, -18) if current else None
    controller.logger = SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    controller.music_library = SimpleNamespace(resolve=lambda *_a, **_k: None)
    calls = []
    controller._play_location = lambda *args, **kwargs: calls.append((args, kwargs))
    return controller, calls


def test_strong_new_zone_replaces_old_context_even_when_coordinates_are_near_old_position():
    old = LocationRecord(95, "Astrub (Cité d'Astrub)", LocationKind.SUBAREA, 1, 'Astrub')
    new = LocationRecord(98, "Astrub (Champs d'Astrub)", LocationKind.SUBAREA, 1, 'Astrub')
    match = LocationMatch(new, 100.0, 'exact', new.name, exact=True, mode=MatchMode.EXACT)
    evidence = LocationEvidence(new.name, 0.99, match, None, 100.0, Coordinates(5, -19), 0.0, 10.0)
    controller, calls = _controller_for_decision(DecisionState.CONFIRMED, evidence, old)

    controller.handle_zone_result(ZoneOCRResult(new.name, 0.99, 10.0, 'small-live'), now=10.0, observed_at=10.0)

    assert controller.current_location == new
    assert controller.state.location_key == new.canonical_key
    assert len(calls) == 1


def test_pending_candidate_keeps_current_audio_without_promoting_old_context_to_confirmed():
    old = LocationRecord(95, "Astrub (Cité d'Astrub)", LocationKind.SUBAREA, 1, 'Astrub')
    candidate = LocationRecord(98, "Astrub (Champs d'Astrub)", LocationKind.SUBAREA, 1, 'Astrub')
    match = LocationMatch(candidate, 91.0, 'fuzzy', candidate.name, mode=MatchMode.FUZZY)
    evidence = LocationEvidence(candidate.name, 0.70, match, None, 91.0, Coordinates(5, -19), 0.0, 10.0)
    controller, calls = _controller_for_decision(DecisionState.PENDING, evidence, old)
    # Any legacy coordinate-retention call is a regression in V20.
    controller._retain_confirmed_context = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('legacy retention called'))

    controller.handle_zone_result(ZoneOCRResult(candidate.name, 0.70, 10.0, 'small-live'), now=10.0, observed_at=10.0)

    assert controller.current_location == old
    assert controller.state.location == old.name
    assert controller.state.decision_state == DecisionState.PENDING.value
    assert calls == []
