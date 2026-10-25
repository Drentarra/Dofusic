"""Régressions des cycles de vie capture/lecture et des états concurrents."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.session import MusicSession
from dofusic.capture.service import CaptureService
from dofusic.config import AppConfig


def _eventually(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_capture_restarts_after_close_timeout_and_rapid_reopening():
    """Reopening during a blocked Win32 capture must not permanently stop capture."""
    entered = threading.Event()
    release = threading.Event()
    backend_closed = threading.Event()
    creates = []

    class Backend:
        def capture(self):
            entered.set()
            release.wait(timeout=1.5)
            return None

        def close(self):
            backend_closed.set()

    def factory():
        creates.append(len(creates))
        return Backend()

    service = CaptureService(factory, fps=30)
    service.start()
    try:
        assert entered.wait(1)
        service.close(timeout=0)
        assert service.alive
        service.start()  # request automatic restart once the old backend closes
        assert len(creates) == 1  # never concurrently open the same Win32 capture
        release.set()
        assert _eventually(lambda: backend_closed.is_set() and len(creates) == 2)
        assert service.alive
        assert _eventually(lambda: service.latest() is not None and service.latest().sequence >= 2)
    finally:
        release.set()
        service.close(timeout=2)
    assert not service.alive


def test_capture_close_cancels_pending_restart():
    entered = threading.Event()
    release = threading.Event()
    creations = []

    class Backend:
        def capture(self):
            entered.set()
            release.wait(1.0)

        def close(self):
            pass

    def factory():
        creations.append(True)
        return Backend()

    service = CaptureService(factory, fps=30)
    service.start()
    try:
        assert entered.wait(1)
        service.close(timeout=0)
        service.start()
        service.close(timeout=0)  # user closes the application before restart
        release.set()
        assert _eventually(lambda: not service.alive)
        assert len(creations) == 1
    finally:
        release.set()
        service.close()


def test_failed_return_to_automatic_audio_does_not_claim_success(tmp_path):
    class Controller:
        player = SimpleNamespace(is_playing=lambda: False)
        music_library = SimpleNamespace(all_tracks=())

        def resume_local_audio(self):
            return False

    session = MusicSession(Controller(), AppConfig())
    assert session.resume_dofus_music() is False
    assert session.status != 'Retour à la musique Dofus'
    assert 'impossible' in session.status.casefold()


def test_finished_manual_queue_reports_failed_auto_recovery(tmp_path):
    track = tmp_path / 'mélodie.ogg'
    track.touch()

    class Controller:
        player = SimpleNamespace(is_playing=lambda: False)
        music_library = SimpleNamespace(all_tracks=(track,))

        def play_manual_local_track(self, path, title, *, loop=False):
            return True

        def resume_local_audio(self):
            return False

    session = MusicSession(Controller(), AppConfig())
    assert session.play_local_track(track)
    session.tick(now=session._busy_grace_until + 1)
    assert session.current is None
    assert not session._playing
    assert 'impossible' in session.status.casefold()


def test_glyph_partial_selection_matches_original_98th_percentile():
    """Optimization must preserve mask pixels, not merely final state labels."""
    import numpy as np

    from dofusic.vision.combat import _glyph_mask

    rng = np.random.default_rng(42)
    for index in range(220):
        patch = rng.integers(0, 256, (25, 25, 3), dtype=np.uint8)
        if index % 4 == 0:
            patch[:] = (35, 65, 80)
            patch[4:22, 8:17] = (225, 230, 235)
        elif index % 4 == 1:
            patch[:] = (10, 10, 10)
        corners = np.concatenate((
            patch[:2, :2].reshape(-1, 3), patch[:2, -2:].reshape(-1, 3),
            patch[-2:, :2].reshape(-1, 3), patch[-2:, -2:].reshape(-1, 3),
        ))
        background = np.median(corners, axis=0)
        contrast = np.abs(patch.astype(np.float32) - background).max(axis=2)
        peak = float(np.percentile(contrast, 98))
        original = None
        if peak >= 5:
            mask = (contrast > max(4.0, peak * 0.34)).astype(np.uint8)
            if 8 <= int(mask.sum()) <= 480:
                original = mask
        optimized = _glyph_mask(patch)
        assert (original is None) == (optimized is None)
        if original is not None:
            np.testing.assert_array_equal(optimized, original)


def test_unreliable_ocr_does_not_count_as_confirmation_of_next_reading():
    from dofusic.location.decision import DecisionEngine
    from dofusic.models import DecisionState, LocationEvidence, LocationKind, LocationMatch, LocationRecord, MatchMode

    place = LocationRecord(7, 'Longue zone', LocationKind.AREA)
    top = LocationMatch(place, 95.0, 'fuzzy', place.name, mode=MatchMode.FUZZY)

    def evidence(confidence, timestamp):
        return LocationEvidence(place.name, confidence, top, None, 95., None, 0., timestamp)

    decision = DecisionEngine(required_confirmations=2)
    assert decision.evaluate(evidence(0.1, 1.0)).state is DecisionState.UNKNOWN
    assert decision.evaluate(evidence(0.75, 1.4)).state is DecisionState.PENDING
    assert decision.evaluate(evidence(0.75, 1.8)).state is DecisionState.CONFIRMED


def test_ambiguous_candidate_does_not_confirm_next_single_reading():
    from dofusic.location.decision import DecisionEngine
    from dofusic.models import DecisionState, LocationEvidence, LocationKind, LocationMatch, LocationRecord, MatchMode

    first = LocationRecord(7, 'Les îles', LocationKind.AREA)
    second = LocationRecord(8, 'Les îles', LocationKind.SUBAREA)
    top1 = LocationMatch(first, 95.0, 'fuzzy', first.name, mode=MatchMode.FUZZY)
    top2 = LocationMatch(second, 94.0, 'fuzzy', second.name, mode=MatchMode.FUZZY)

    def evidence(ambiguous, timestamp):
        return LocationEvidence(first.name, 0.8, top1, top2 if ambiguous else None,
                                1.0 if ambiguous else 95.0, None, 0.0, timestamp)

    decision = DecisionEngine(required_confirmations=2)
    assert decision.evaluate(evidence(True, 2.0)).state is DecisionState.AMBIGUOUS
    assert decision.evaluate(evidence(False, 2.4)).state is DecisionState.PENDING
    assert decision.evaluate(evidence(False, 2.8)).state is DecisionState.CONFIRMED


def test_out_of_order_observations_do_not_form_temporal_confirmation():
    from dofusic.location.decision import DecisionEngine
    from dofusic.models import DecisionState, LocationEvidence, LocationKind, LocationMatch, LocationRecord, MatchMode
    place = LocationRecord(3, 'Village', LocationKind.AREA)
    top = LocationMatch(place, 95., 'fuzzy', place.name, mode=MatchMode.FUZZY)

    def evidence(timestamp):
        return LocationEvidence(place.name, 0.8, top, None, 95., None, 0., timestamp)

    decision = DecisionEngine(required_confirmations=2)
    assert decision.evaluate(evidence(10.)).state is DecisionState.PENDING
    assert decision.evaluate(evidence(9.)).state is DecisionState.UNKNOWN
    assert decision.evaluate(evidence(11.)).state is DecisionState.CONFIRMED


def test_controller_never_declares_success_when_player_rejects_current_track(tmp_path):
    import logging

    from dofusic.app import DofusicController

    target = tmp_path / 'combat.ogg'
    controller = DofusicController.__new__(DofusicController)
    controller.player = SimpleNamespace(current=target, play=lambda *_a, **_k: False,
                                        is_playing=lambda: False)
    controller.config = AppConfig()
    controller.logger = logging.getLogger('test.failed-current')
    controller.state = SimpleNamespace(music='', status='', display_theme='')
    controller._audio_override_active = False
    controller._automatic_detection_unlocked = True
    controller._game_process_running = True
    controller._play_track(target, status='Combat détecté')
    assert controller.state.music == 'Aucune musique'
    assert 'impossible' in controller.state.status.casefold()


def test_startup_music_never_claims_success_when_player_rejects_current_track(tmp_path):
    import logging

    from dofusic.app import DofusicController

    target = tmp_path / 'Musique.ogg'
    controller = DofusicController.__new__(DofusicController)
    controller.player = SimpleNamespace(current=target, play=lambda *_a, **_k: False,
                                        is_playing=lambda: False)
    controller.music_library = SimpleNamespace(resolve=lambda *_a, **_k: target)
    controller.logger = logging.getLogger('test.failed-startup')
    controller.state = SimpleNamespace(music='', status='', display_theme='')
    controller._audio_override_active = False
    controller._automatic_detection_unlocked = False
    controller._game_process_running = True
    assert not controller._play_startup_generic(restart=False)
    assert controller.state.music == 'Aucune musique'


def test_real_hud_combat_changes_desired_local_audio_after_three_confirmations(tmp_path):
    """Image enregistrée -> classification -> décision -> commande audio simulée."""
    import logging

    from dofusic.app import DofusicController
    from dofusic.models import LocationKind, LocationRecord
    from dofusic.vision.combat import CombatStateTracker
    from dofusic.vision.layout import HUDGeometry
    from tools.hud_reference import extract_reference_hud_inputs as extract_hud_inputs
    from tools.image_io import read_image

    location = LocationRecord(42, 'Zone générique', LocationKind.AREA)
    combat_file = tmp_path / 'Musique Combat.ogg'
    normal_file = tmp_path / 'Musique.ogg'
    played = []
    resolved = []

    class Player:
        current = None
        def play(self, path, **options):
            self.current = Path(path)
            played.append((Path(path), options))
            return True

    def resolve(_location, raw_text='', *, combat=False, **kwargs):
        resolved.append((combat, kwargs))
        return combat_file if combat else normal_file

    controller = DofusicController.__new__(DofusicController)
    controller.config = AppConfig()
    controller.player = Player()
    controller.music_library = SimpleNamespace(
        dungeon_catalog=SimpleNamespace(is_dungeon=lambda loc: False), resolve=resolve,
    )
    controller.hud_geometry = HUDGeometry()
    controller.combat_tracker = CombatStateTracker(required_confirmations=3, initial_state=False)
    controller.state = SimpleNamespace(in_combat=False, combat_confidence=0., ocr_text=location.name,
                                       music='', status='', display_theme='', place='')
    controller.logger = logging.getLogger('test.combat-integration')
    controller.current_location = location
    controller.combat_tracker.toolbar_buttons = None
    controller._combat_suspended_for_dungeon = False
    controller._audio_override_active = False
    controller._automatic_detection_unlocked = True
    controller._game_process_running = True

    fixture_dir = Path(__file__).parent / 'fixtures' / 'hud'
    combat = extract_hud_inputs(read_image(fixture_dir / 'xelorium_combat_corner.png')).combat
    exploring = extract_hud_inputs(read_image(fixture_dir / 'havresac_expanded_corner.png')).combat
    for _ in range(2):
        controller._update_combat_from_toolbar(combat)
    assert not played
    controller._update_combat_from_toolbar(combat)
    assert controller.state.in_combat is True
    assert played == [(combat_file, {})]
    assert resolved == [(True, {'new_generic_cycle': True})]
    for _ in range(3):
        controller._update_combat_from_toolbar(exploring)
    assert controller.state.in_combat is False
    assert played[-1] == (normal_file, {})
    assert len(played) == 2
