from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def _stroke(image, x1, x2, y1, y2, value=255):
    image[y1:y2, x1:x2] = value


def _position_frame() -> np.ndarray:
    """Reference-scale HUD strip containing `1, -36 - Niveau 140`."""
    image = np.zeros((110, 240, 3), dtype=np.uint8)
    y = 69
    _stroke(image, 2, 5, y + 6, y + 23)
    _stroke(image, 9, 11, y + 20, y + 24)
    _stroke(image, 15, 20, y + 14, y + 17)
    _stroke(image, 24, 27, y + 6, y + 23)
    _stroke(image, 31, 34, y + 6, y + 23)
    _stroke(image, 40, 45, y + 14, y + 17)
    _stroke(image, 50, 54, y + 6, y + 23)
    _stroke(image, 58, 61, y + 6, y + 23)
    return image


def test_position_ocr_roi_remains_full_width_in_v20():
    from dofusic.vision.layout import HUDGeometry, HUDTransform, extract_hud_inputs

    geometry = HUDGeometry()
    hud = extract_hud_inputs(_position_frame(), geometry, transform=HUDTransform())

    assert hud.position.shape[1] == geometry.position_width
    assert not hasattr(hud, 'position_overlay')


def test_tracking_rect_maps_full_position_roi_and_controller_shortens_overlay_from_result():
    from dofusic.app import DofusicController
    from dofusic.models import PositionOCRResult
    from dofusic.vision.layout import HUDInputs, HUDTrackingRects, HUDTransform, tracking_screen_rects

    full = np.zeros((29, 180, 3), dtype=np.uint8)
    zone = np.zeros((34, 120, 3), dtype=np.uint8)
    combat = np.zeros((40, 160, 3), dtype=np.uint8)
    hud = HUDInputs(zone=zone, position=full, combat=combat, transform=HUDTransform())

    rects = tracking_screen_rects(
        capture_screen_rect=(100, 200, 1920, 100),
        capture_image_shape=(100, 1920, 3),
        hud=hud,
    )
    assert isinstance(rects, HUDTrackingRects)
    assert rects.position[2] == 180

    result = PositionOCRResult('1, -36 - Niveau 140', 1, -36, 0.99, 1.0, 'test')
    overlay = DofusicController._position_overlay_screen_rect(
        full_rect=rects.position, result=result, roi_height=29, roi_width=180,
    )
    assert overlay is not None
    assert overlay[0:2] == rects.position[0:2]
    assert 60 <= overlay[2] <= 76


def test_v20_removes_live_hud_signature_scheduling_helpers():
    from dofusic.capture import dofus_window

    assert not hasattr(dofus_window, 'hud_text_signature')
    assert not hasattr(dofus_window, 'signature_diff')
    assert not hasattr(dofus_window, 'frame_signature')


def test_reconfirmed_same_location_does_not_reapply_local_music():
    from dofusic.app import DofusicController
    from dofusic.models import DecisionResult, DecisionState, LocationEvidence, LocationKind, LocationMatch, LocationRecord, MatchMode, ZoneOCRResult

    location = LocationRecord(95, "Astrub (Cité d'Astrub)", LocationKind.SUBAREA, 1, 'Astrub')
    match = LocationMatch(location, 100.0, 'name', location.name, exact=True, mode=MatchMode.EXACT)
    evidence = LocationEvidence(location.name, 0.99, match, None, 100.0, None, 0.0, 1.0)

    class Resolver:
        def resolve(self, *_args, **_kwargs):
            return evidence

    class Decision:
        def evaluate(self, observed):
            return DecisionResult(DecisionState.CONFIRMED, location, observed, 'exact')

    controller = DofusicController.__new__(DofusicController)
    controller.config = SimpleNamespace(position_context_max_age_sec=4.0, debug=False, pending_confirmation_interval_sec=0.35)
    controller.resolver = Resolver()
    controller.decision = Decision()
    controller.state = SimpleNamespace(
        zone_elapsed_ms=0.0, position_x=None, position_y=None, ocr_text='', confidence=0.0,
        decision_state='', debug_text='', location='', location_key='', place='—', in_combat=False,
        display_theme='', status='',
    )
    controller.last_position_at = 0.0
    controller.last_evidence = None
    controller.unknown_since = None
    controller.next_confirmation_at = 0.0
    controller.current_location = location
    controller.context_position = None
    controller.logger = SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None)
    calls = []
    controller._play_location = lambda *args, **kwargs: calls.append((args, kwargs))

    controller.handle_zone_result(ZoneOCRResult(location.name, 0.99, 3.0, 'test'), now=2.0, observed_at=2.0)

    assert calls == []


def test_position_overlay_is_invariant_to_missing_separator_and_background_bridging():
    from dofusic.models import PositionOCRResult
    from dofusic.vision.position_overlay import coordinate_overlay_width

    clean = PositionOCRResult('1, -36', 1, -36, 0.99, 1.0, 'test')
    missing_dash = PositionOCRResult('1, -36 Niveau 140', 1, -36, 0.99, 1.0, 'test')
    noisy = PositionOCRResult('1, -36xxNiveau 140', 1, -36, 0.99, 1.0, 'test')

    assert coordinate_overlay_width(clean, 29) == coordinate_overlay_width(missing_dash, 29)
    assert coordinate_overlay_width(clean, 29) == coordinate_overlay_width(noisy, 29)
