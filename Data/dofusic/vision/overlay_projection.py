"""Projection déterministe des coordonnées de capture vers les overlays écran.

Aucun accès Win32/Tk, aucune mise à jour d'état : le contrôleur publie uniquement
le résultat, qui utilise exactement le HUD extrait de la capture courante.
"""
from __future__ import annotations

from dataclasses import dataclass

from dofusic.models import PositionOCRResult
from dofusic.vision.layout import HUDGeometry, HUDInputs, tracking_screen_rects
from dofusic.vision.position_overlay import coordinate_overlay_width


@dataclass(frozen=True, slots=True)
class OverlayProjection:
    hwnd: int | None = None
    combat: tuple[int, int, int, int] | None = None
    zone: tuple[int, int, int, int] | None = None
    position: tuple[int, int, int, int] | None = None


def project_position_overlay(
    full_rect: tuple[int, int, int, int],
    result: PositionOCRResult | None,
    roi_height: int,
    roi_width: int,
) -> tuple[int, int, int, int] | None:
    if result is None or result.coordinates is None or roi_height <= 0 or roi_width <= 0:
        return None
    left, top, full_width, full_height = (int(v) for v in full_rect)
    if full_width <= 0 or full_height <= 0:
        return None
    width_in_roi = coordinate_overlay_width(result, roi_height)
    if width_in_roi <= 0:
        return None
    width = int(round(float(full_width) * min(roi_width, width_in_roi) / float(roi_width)))
    return left, top, max(1, min(full_width, width)), full_height


def project_overlays(
    *, source: str, hwnd: int, screen_rect: tuple[int, int, int, int] | None,
    image_shape: tuple[int, ...], hud: HUDInputs, geometry: HUDGeometry,
    toolbar_buttons: int | None, position_result: PositionOCRResult | None,
    zone_display_width: int | None = None,
) -> OverlayProjection:
    """Only on-screen, visible captures are eligible to display overlays."""
    if source != 'visible' or screen_rect is None:
        return OverlayProjection()
    rects = tracking_screen_rects(
        capture_screen_rect=screen_rect, capture_image_shape=image_shape,
        hud=hud, geometry=geometry, toolbar_buttons=toolbar_buttons,
    )
    position = project_position_overlay(
        rects.position, position_result,
        int(hud.position.shape[0]) if hud.position.ndim >= 2 else 0,
        int(hud.position.shape[1]) if hud.position.ndim >= 2 else 0,
    )
    zone = rects.zone
    if zone_display_width is not None and hud.zone.ndim >= 2 and hud.zone.shape[1] > 0:
        # The OCR ROI stays untouched; only the visible contour's right edge
        # uses the last recognized text width (converted to screen pixels).
        screen_width = round(zone[2] * max(1, int(zone_display_width)) / hud.zone.shape[1])
        zone = (zone[0], zone[1], max(1, screen_width), zone[3])
    return OverlayProjection(int(hwnd), rects.combat, zone, position)
