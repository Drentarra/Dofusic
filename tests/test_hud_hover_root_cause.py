"""Régressions : survol du HUD, preuve OCR et recalage réel des rectangles."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry, HUDInputs, HUDTransform, ZoneOverlayWidthTracker
from dofusic.vision.overlay_projection import project_overlays
from tools.hud_reference import HUDTransformSmoother, estimate_hud_transform


def test_overlay_width_does_not_follow_hover_pixels_without_valid_zone_change():
    tracker = ZoneOverlayWidthTracker()
    def observe(n):
        return tracker.observe(n, window_id=42, scale=1.)
    assert observe(252) == 252
    assert tracker.confirm("Bonta (Cœur immaculé)", 252, window_id=42, scale=1., confidence=.99)
    for width in (80, 400, 65, 520, 90, 710, 250) * 6:
        assert observe(width) == 252
    # Same name in a different crop width cannot move the outline.
    assert not tracker.confirm("Bonta (Cœur immaculé)", 500, window_id=42, scale=1., confidence=.99)
    assert observe(500) == 252
    # A new recognized name actually changes the visual width.
    assert tracker.confirm("Bonta (Havres d'ivoire)", 271, window_id=42, scale=1., confidence=.98)
    assert observe(98) == 271


def test_overlay_does_not_trust_old_ocr_wrong_hwnd_scale_or_weak_read():
    tracker = ZoneOverlayWidthTracker()
    assert tracker.observe(240, window_id=20, scale=1.) == 240
    assert not tracker.confirm('invalide', 500, window_id=20, scale=1., confidence=.50)
    assert not tracker.confirm('invalide', 500, window_id=19, scale=1., confidence=1.)
    assert not tracker.confirm('invalide', 500, window_id=20, scale=1.8, confidence=1.)
    assert tracker.observe(125, window_id=20, scale=1.) == 240
    assert tracker.observe(310, window_id=21, scale=1.) == 310
    assert tracker.observe(350, window_id=21, scale=1.35) == 350


def test_white_zone_outline_width_is_independent_of_live_ocr_pixel_width():
    def hud(width):
        return HUDInputs(
            zone=np.zeros((34, width, 3), np.uint8),
            combat=np.zeros((40, 300, 3), np.uint8),
            position=np.zeros((29, 180, 3), np.uint8),
            transform=HUDTransform(),
        )
    common = dict(source='visible', hwnd=55, screen_rect=(250, 100, 650, 110),
                  image_shape=(110, 650, 3), geometry=HUDGeometry(),
                  toolbar_buttons=6, position_result=None,
                  zone_display_width=225)
    first = project_overlays(hud=hud(225), **common)
    hovered = project_overlays(hud=hud(75), **common)
    highlighted = project_overlays(hud=hud(450), **common)
    assert first.zone == hovered.zone == highlighted.zone == (250, 140, 225, 34)
    assert first.combat == hovered.combat == highlighted.combat


# Six 720x106 corner strips already included in the project from the user's
# Windows diagnostics. No private desktop image or chat data is required.
REAL_CORNERS = Path(__file__).parent / 'fixtures' / 'hud_live_october_2026'


def read_real_corner(index: int):
    raw = np.fromfile(str(REAL_CORNERS / f'case_{index}.png'), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    assert image is not None
    return image


def test_all_six_real_hud_states_allow_strict_recalibration():
    for index in range(1, 7):
        image = read_real_corner(index)
        actual = estimate_hud_transform(
            image, previous=HUDTransform(), require_rim=True,
        )
        assert actual == HUDTransform(), (index, actual)


def test_mouseover_occlusion_does_not_reset_valid_hud_scale():
    image = read_real_corner(4)
    tracker = HUDTransformSmoother(confirmations=4)
    initial = HUDTransform()
    assert tracker.update(estimate_hud_transform(image)) == initial
    # SIMULATED overlay that covers the toolbar with an arbitrary tooltip.
    # Such an image is not a further Dofus capture or real hover observation.
    obscured = image.copy()
    obscured[:84, :480] = (90, 120, 140)
    measured = estimate_hud_transform(obscured, previous=initial, require_rim=True)
    assert measured is None
    for _ in range(30):
        assert tracker.update(measured) == initial


def test_toolbar_rescale_can_still_be_confirmed_when_physical_shape_is_valid():
    image = read_real_corner(3)
    enlarged = cv2.resize(image, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)
    measured = estimate_hud_transform(enlarged, previous=HUDTransform(), require_rim=True)
    assert measured is not None
    assert 1.4 <= measured.scale <= 1.6
    tracker = HUDTransformSmoother(confirmations=4)
    assert tracker.update(HUDTransform()) == HUDTransform()
    assert [tracker.update(measured).scale for _ in range(4)] == [1., 1., 1., measured.scale]


def test_real_toolbar_relocation_can_still_be_confirmed():
    source = Path(__file__).parent / 'fixtures' / 'hud' / 'havresac_expanded_corner.png'
    image = cv2.imdecode(np.fromfile(str(source), np.uint8), cv2.IMREAD_COLOR)
    measured = estimate_hud_transform(image, previous=HUDTransform(), require_rim=True)
    assert measured == HUDTransform(scale=1., origin_x=56, origin_y=42)
    tracker = HUDTransformSmoother(confirmations=4)
    assert tracker.update(HUDTransform()) == HUDTransform()
    assert [tracker.update(measured).origin_x for _ in range(4)] == [0, 0, 0, 56]


def test_fake_textured_popup_cannot_impersonate_toolbar_icon():
    from tools.hud_reference import toolbar_icon_consistent, toolbar_icon_signature
    source = read_real_corner(3)
    anchor = toolbar_icon_signature(source, HUDTransform())
    candidate = source.copy()
    # Synthetic tooltip with a plausible border and noisy interior. Despite
    # passing the geometric candidate detector, it must fail the icon check.
    candidate[:84, :480] = (90, 120, 140)
    rng = np.random.default_rng(123)
    candidate[14:62, 14:62] = rng.integers(80, 151, size=(48, 48, 3), dtype=np.uint8)
    false_transform = estimate_hud_transform(candidate, previous=HUDTransform(), require_rim=True)
    assert false_transform is not None
    assert false_transform.scale > 2
    assert not toolbar_icon_consistent(anchor, toolbar_icon_signature(candidate, false_transform))
    for scale in (.6, .8, 1.25, 1.5, 2.):
        resized = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        actual = toolbar_icon_signature(resized, HUDTransform(scale=scale))
        assert toolbar_icon_consistent(anchor, actual), scale


def test_controller_promotes_overlay_width_only_for_confirmed_nonstale_ocr():
    import logging
    from types import MethodType, SimpleNamespace

    from dofusic.app import ControllerState, DofusicController
    from dofusic.models import DecisionState, ZoneOCRResult
    from dofusic.vision.workers import OCREvent, OCREventKind

    controller = DofusicController.__new__(DofusicController)
    controller.logger = logging.getLogger('test_white_outline')
    controller.state = ControllerState()
    controller.zone_slot = SimpleNamespace(scheduler=SimpleNamespace(completed=lambda **kwargs: True))
    controller.last_zone_observed_at = 0.0
    controller._zone_overlay_width = ZoneOverlayWidthTracker()
    controller._zone_overlay_width.observe(230, window_id=19, scale=1.)

    def fake_zone_result(self, result, *, now, observed_at):
        # Simulate the actual location decision contract, independent of SQLite.
        self.last_zone_observed_at = observed_at
        self.state.ocr_text = result.text
        self.state.decision_state = ('unknown' if result.text == 'Tooltip'
                                     else DecisionState.CONFIRMED.value)

    controller.handle_zone_result = MethodType(fake_zone_result, controller)

    def feed(label, width, request_id, confidence=.99):
        controller._pending_zone_width = (request_id, width, 19, 1.)
        controller._handle_event('zone', OCREvent(
            OCREventKind.RESULT, 'zone', request_id=request_id,
            result=ZoneOCRResult(label, confidence, 12., 'test'),
            captured_at=5. + request_id, submitted_at=6. + request_id,
            finished_at=7. + request_id,
        ), now=7. + request_id)

    feed('Bonta (Cœur immaculé)', 260, 1)
    assert controller._zone_overlay_width.width == 260
    feed('Tooltip', 450, 2)
    assert controller._zone_overlay_width.width == 260
    feed('Bonta (Cœur immaculé)', 100, 3)
    assert controller._zone_overlay_width.width == 260
    feed('Bonta (Havres d\'ivoire)', 290, 4)
    assert controller._zone_overlay_width.width == 290
