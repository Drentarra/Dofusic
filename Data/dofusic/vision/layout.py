from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class HUDTransform:
    """Runtime mapping from reference HUD coordinates to captured pixels."""

    scale: float = 1.0
    origin_x: int = 0
    origin_y: int = 0

    def point(self, ref_x: int, ref_y: int) -> tuple[int, int]:
        return (
            self.origin_x + int(round(int(ref_x) * self.scale)),
            self.origin_y + int(round(int(ref_y) * self.scale)),
        )

    def size(self, ref_width: int, ref_height: int) -> tuple[int, int]:
        return (
            max(1, int(round(int(ref_width) * self.scale))),
            max(1, int(round(int(ref_height) * self.scale))),
        )


@dataclass(frozen=True, slots=True)
class HUDGeometry:
    """Single source of truth for Dofus' top-left toolbar and OCR HUD lines.

    Dofus does not scale its HUD linearly with the desktop resolution. The
    toolbar itself is therefore used as the runtime scale anchor. Reference
    coordinates below describe the known-good 1920x1080 HUD; one transform is
    then reused by capture, OCR crops and the passive overlay.
    """

    reference_client_width: int = 1920
    reference_client_height: int = 1080

    capture_x: int = 0
    capture_y: int = 0
    capture_width: int = 1920
    capture_height: int = 100
    bootstrap_capture_height: int = 220
    capture_margin: int = 8

    combat_x: int = 0
    combat_y: int = 0
    combat_width: int = 320
    combat_height: int = 40
    # The physical toolbar background occupies 37 px at the reference UI scale.
    # Its height is theme-independent in all supplied captures and remains valid
    # even when extra Havre-Sac controls extend the toolbar horizontally.
    toolbar_reference_height: int = 37
    combat_button_rects: tuple[tuple[int, int, int, int], ...] = (
        (4, 3, 35, 34),
        (43, 3, 35, 34),
        (81, 3, 36, 34),
        (120, 3, 36, 34),
    )
    combat_button_visual_rect: tuple[int, int, int, int] = (4, 4, 29, 29)

    zone_x: int = 0
    zone_y: int = 40
    zone_height: int = 34

    position_x: int = 0
    position_y: int = 69
    position_width: int = 180
    position_height: int = 29

    @property
    def reference_bottom(self) -> int:
        return max(
            self.combat_y + self.combat_height,
            self.zone_y + self.zone_height,
            self.position_y + self.position_height,
        )

    def capture_rect(
        self,
        client_width: int,
        client_height: int,
        *,
        ui_scale: float | None = None,
    ) -> tuple[int, int, int, int]:
        """Return the top HUD capture strip in *physical client pixels*.

        Width always follows the actual client because zone names are dynamic.
        Height follows the detected HUD scale, never the desktop resolution.
        Before the first scale observation a conservative bootstrap strip is
        captured; one frame later the strip contracts to the exact requirement.
        """
        client_width = max(0, int(client_width))
        client_height = max(0, int(client_height))
        if client_width <= 0 or client_height <= 0:
            return 0, 0, 0, 0
        scale = None if ui_scale is None else max(0.35, min(3.0, float(ui_scale)))
        if scale is None:
            height = self.bootstrap_capture_height
        else:
            height = int(round((self.reference_bottom + self.capture_margin) * scale))
        height = max(self.combat_height, height)
        return 0, 0, client_width, min(client_height, height)

    def combat_button_reference_rect(self, index: int = 0) -> tuple[int, int, int, int]:
        x, y, width, height = self.combat_button_rects[int(index)]
        return self.combat_x + x, self.combat_y + y, width, height

    def combat_button_visual_reference_rect(self) -> tuple[int, int, int, int]:
        x, y, width, height = self.combat_button_visual_rect
        return self.combat_x + x, self.combat_y + y, width, height


def estimate_hud_transform(
    image: np.ndarray,
    geometry: HUDGeometry | None = None,
) -> HUDTransform | None:
    """Infer HUD scale from the theme-independent toolbar background height.

    The first toolbar row is anchored at the client top-left. Its background is
    contiguous while the pixels directly below it are black before the zone
    label starts. Measuring that vertical run is more reliable than scaling from
    desktop/client resolution and works with the green, purple and Havre-Sac UI.
    """
    geometry = geometry or HUDGeometry()
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim < 2:
        return None
    h, w = image.shape[:2]
    if h < 24 or w < 48:
        return None

    probe_h = min(h, max(96, geometry.bootstrap_capture_height))
    # A collapsed combat toolbar is only one +/- button wide. Probing the
    # former 180-pixel row misses it at smaller UI scales and breaks both OCR
    # and combat crops. Measure inside that first button instead.
    probe_w = min(w, 32)
    probe = image[:probe_h, :probe_w]
    if probe.ndim == 2:
        gray = probe.astype(np.uint8, copy=False)
    else:
        gray = cv2.cvtColor(probe, cv2.COLOR_BGR2GRAY)

    active = gray > 8
    row_fraction = active.mean(axis=1)
    start_candidates = np.flatnonzero(row_fraction[: min(12, len(row_fraction))] > 0.20)
    if start_candidates.size == 0:
        return None
    origin_y = int(start_candidates[0])

    low_run = 0
    toolbar_end: int | None = None
    for y in range(origin_y, len(row_fraction)):
        if row_fraction[y] < 0.08:
            low_run += 1
            if low_run >= 3 and y - origin_y >= 20:
                toolbar_end = y - low_run + 1
                break
        else:
            low_run = 0
    if toolbar_end is None:
        # When outdoor scenery fills the rows immediately under the HUD,
        # brightness cannot delimit its lower edge. Compare the material
        # inside the first button with the rows beneath that same button.
        # This keeps scale calibration independent of map brightness.
        first_band = active[origin_y:min(probe_h, origin_y + 18)]
        first_columns = np.flatnonzero(first_band.mean(axis=0) > 0.20) if first_band.size else np.empty(0)
        first_x = int(first_columns[0]) if first_columns.size else 0
        y0 = origin_y + 7
        y1 = min(probe_h, origin_y + 13)
        x0 = first_x + 5
        x1 = min(probe_w, first_x + 9)
        if y1 <= y0 or x1 <= x0:
            return None
        sample = probe[y0:y1, x0:x1]
        if sample.size == 0:
            return None
        if sample.ndim == 2:
            sample = sample[:, :, None]
            source = probe[:, :, None] if probe.ndim == 2 else probe
        else:
            source = probe
        material_color = np.median(sample, axis=(0, 1))
        diff = np.abs(source[:, x0:x1].astype(np.float32) - material_color).max(axis=2)
        agreement = (diff <= 34).mean(axis=1)
        if float(np.mean(agreement[y0:y1])) < 0.65:
            return None
        low_run = 0
        for y in range(origin_y + 20, len(agreement)):
            if agreement[y] < 0.20:
                low_run += 1
                if low_run >= 3:
                    toolbar_end = y - low_run + 1
                    break
            else:
                low_run = 0
        if toolbar_end is None:
            return None

    toolbar_height = toolbar_end - origin_y
    if toolbar_height < 18 or toolbar_height > 120:
        return None

    # Find only the left edge; horizontal toolbar extent is intentionally ignored
    # because Havre-Sac appends extra controls to the same row.
    top_band = active[origin_y:toolbar_end, : min(w, 80)]
    col_fraction = top_band.mean(axis=0) if top_band.size else np.empty((0,))
    x_candidates = np.flatnonzero(col_fraction > 0.20)
    origin_x = int(x_candidates[0]) if x_candidates.size else 0

    scale = float(toolbar_height) / float(geometry.toolbar_reference_height)
    if not 0.35 <= scale <= 3.0:
        return None
    return HUDTransform(scale=scale, origin_x=origin_x, origin_y=origin_y)


@dataclass(frozen=True, slots=True)
class HUDInputs:
    zone: np.ndarray
    # Fixed calibrated ROI sent to the position OCR worker. It is intentionally
    # wider than the visible coordinate token so OCR correctness never depends
    # on overlay geometry.
    position: np.ndarray
    combat: np.ndarray | None = None
    transform: HUDTransform = HUDTransform()
    # Visible text width only; OCR continues to use its independent full crop.
    zone_display_width: int | None = None


@dataclass(frozen=True, slots=True)
class HUDTrackingRects:
    combat: tuple[int, int, int, int]
    zone: tuple[int, int, int, int]
    position: tuple[int, int, int, int]


def toolbar_material_columns(image: np.ndarray, *, scale: float = 1.0) -> np.ndarray:
    """Fraction of each column resembling the first toolbar button material.

    Absolute brightness is deliberately not used: a Dofus scene may be black,
    sunlit, or textured. The HUD has a distinctive, relatively uniform material
    shared by its icon slots; the scenery outside need not be dark.
    """
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim not in (2, 3):
        return np.empty((0,), dtype=np.float32)
    h, w = image.shape[:2]
    scale = max(0.35, min(3.0, float(scale)))
    y0, y1 = min(h, round(8 * scale)), min(h, round(29 * scale))
    x0, x1 = min(w, round(5 * scale)), min(w, round(9 * scale))
    if y1 <= y0 or x1 <= x0:
        return np.zeros(w, dtype=np.float32)
    patch = image[y0:y1]
    if patch.ndim == 2:
        patch = patch[:, :, None]
    background = np.median(patch[:, x0:x1], axis=(0, 1))
    difference = np.abs(patch.astype(np.float32) - background).max(axis=2)
    # On a very dark HUD, black scenery would otherwise count as the same
    # material. Relative tolerance preserves both dark themes and bright maps.
    tolerance = min(34.0, max(9.0, float(background.max()) * 0.48))
    return (difference <= tolerance).mean(axis=0).astype(np.float32)


def _visible_combat_toolbar_width(combat: np.ndarray | None, scale: float) -> int:
    """Follow contiguous HUD button material, not pixels of the game scene."""
    scale = max(0.35, min(3.0, float(scale)))
    minimum = max(1, round(38 * scale))
    if combat is None or getattr(combat, 'size', 0) == 0:
        return minimum
    height, width = combat.shape[:2]
    if height < 10 or width <= minimum:
        return min(width, minimum)

    coverage = toolbar_material_columns(combat, scale=scale)
    active = coverage >= 0.32
    # A uniformly coloured/empty crop has no detectable final button edge.
    # Do not draw a full-width outline merely because all pixels match.
    if not np.any(active[minimum:]) or not np.any(coverage[minimum:] < 0.16):
        return minimum

    # Gaps between actual buttons are short. Stop on the first longer gap:
    # on Astrub, scenery to the right is bright but is not toolbar material.
    max_gap = max(6, round(10 * scale))
    gap = 0
    last = minimum
    for x in range(minimum, width):
        if active[x]:
            gap = 0
            last = x
        else:
            gap += 1
            if gap >= max_gap:
                break
    return min(width, max(minimum, last + 1 + max(1, round(3 * scale))))

def tracking_screen_rects(
    *,
    capture_screen_rect: tuple[int, int, int, int],
    capture_image_shape: tuple[int, ...],
    hud: HUDInputs,
    geometry: HUDGeometry | None = None,
) -> HUDTrackingRects:
    """Map the exact OCR inputs back using the same runtime HUD transform."""
    geometry = geometry or HUDGeometry()
    if len(capture_image_shape) < 2:
        raise ValueError('capture_image_shape invalide')
    image_h, image_w = int(capture_image_shape[0]), int(capture_image_shape[1])
    if image_w <= 0 or image_h <= 0:
        raise ValueError('capture vide')

    left, top, screen_w, screen_h = (int(v) for v in capture_screen_rect)
    sx_screen = float(screen_w) / float(image_w)
    sy_screen = float(screen_h) / float(image_h)
    transform = hud.transform

    def map_reference_rect(ref_x: int, ref_y: int, ref_width: int, ref_height: int) -> tuple[int, int, int, int]:
        px, py = transform.point(ref_x, ref_y)
        pw, ph = transform.size(ref_width, ref_height)
        return (
            left + int(round(px * sx_screen)),
            top + int(round(py * sy_screen)),
            max(1, int(round(pw * sx_screen))),
            max(1, int(round(ph * sy_screen))),
        )

    def map_crop(ref_x: int, ref_y: int, crop: np.ndarray) -> tuple[int, int, int, int]:
        crop_h, crop_w = crop.shape[:2]
        px, py = transform.point(ref_x, ref_y)
        return (
            left + int(round(px * sx_screen)),
            top + int(round(py * sy_screen)),
            max(1, int(round(crop_w * sx_screen))),
            max(1, int(round(crop_h * sy_screen))),
        )

    # Le détecteur continue d'analyser les 320 px du HUD. Seul le contour
    # affiché utilise la longueur du bandeau réellement présent à l'écran.
    toolbar = map_reference_rect(
        geometry.combat_x, geometry.combat_y,
        geometry.combat_width, geometry.toolbar_reference_height,
    )
    visible_width = _visible_combat_toolbar_width(hud.combat, transform.scale)
    zone_rect = map_crop(geometry.zone_x, geometry.zone_y, hud.zone)
    if hud.zone_display_width is not None:
        screen_zone_width = max(1, int(round(hud.zone_display_width * sx_screen)))
        zone_rect = (zone_rect[0], zone_rect[1], screen_zone_width, zone_rect[3])
    return HUDTrackingRects(
        combat=(toolbar[0], toolbar[1], max(1, int(round(visible_width * sx_screen))), toolbar[3]),
        zone=zone_rect,
        position=map_crop(geometry.position_x, geometry.position_y, hud.position),
    )


def _transformed_rect(
    image: np.ndarray,
    *,
    ref_x: int,
    ref_y: int,
    ref_width: int,
    ref_height: int,
    transform: HUDTransform,
) -> tuple[int, int, int, int]:
    h, w = image.shape[:2]
    x, y = transform.point(ref_x, ref_y)
    width, height = transform.size(ref_width, ref_height)
    x1 = max(0, min(w - 1, x))
    y1 = max(0, min(h - 1, y))
    x2 = max(x1 + 1, min(w, x + width))
    y2 = max(y1 + 1, min(h, y + height))
    return x1, y1, x2, y2


def _crop(image: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = rect
    return np.ascontiguousarray(image[y1:y2, x1:x2])


def _outlined_text_mask(image: np.ndarray) -> np.ndarray:
    """Cheap mask for Dofus' bright glyphs with a dark outline."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    bright = (value >= 135) & (saturation <= 180)
    dark = (value <= 110).astype(np.uint8)
    near_dark = cv2.dilate(dark, np.ones((5, 5), dtype=np.uint8)) > 0
    return (bright & near_dark).astype(np.uint8)


def _remove_short_true_runs(active: np.ndarray, *, min_run: int = 2) -> np.ndarray:
    """Drop isolated active columns so scenery noise cannot bridge a text gap."""
    cleaned = np.asarray(active, dtype=bool).copy()
    start: int | None = None
    for index in range(len(cleaned) + 1):
        value = bool(cleaned[index]) if index < len(cleaned) else False
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start < min_run:
                cleaned[start:index] = False
            start = None
    return cleaned


def _trim_fixed_origin_text_group(
    image: np.ndarray,
    *,
    gap_stop: int,
    padding: int,
    empty_preview_width: int,
) -> np.ndarray:
    """Trim only the *right* edge of a calibrated left-anchored HUD line.

    The caller already knows the exact X/Y start of the Dofus slot. We never
    move or rediscover that origin; this helper only follows outlined glyph
    columns until the first real whitespace gap after text has been seen.
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return np.empty((0, 0, 3), dtype=np.uint8)

    _h, w = image.shape[:2]
    if w <= 1:
        return np.ascontiguousarray(image)

    mask = _outlined_text_mask(image)
    active = _remove_short_true_runs(mask.sum(axis=0) >= 2, min_run=2)
    last_active: int | None = None
    gap = 0
    required_gap = max(4, int(gap_stop))

    for x in range(w):
        if active[x]:
            last_active = x
            gap = 0
            continue
        if last_active is None:
            # The calibrated origin is preserved even if the font has a few
            # blank columns before the first visible stroke.
            continue
        gap += 1
        if gap >= required_gap:
            end = min(w, last_active + 1 + max(2, int(padding)))
            return np.ascontiguousarray(image[:, :end])

    if last_active is None:
        end = min(w, max(1, int(empty_preview_width)))
    else:
        end = min(w, last_active + 1 + max(2, int(padding)))
    return np.ascontiguousarray(image[:, :end])


def zone_crop_has_text(
    image: np.ndarray,
    *,
    min_active_columns: int = 4,
    min_pixels: int = 20,
) -> bool:
    """Return whether the current dynamic zone crop still contains HUD text.

    Map transitions briefly remove the zone label.  During that gap the dynamic
    crop falls back to its preview width, which used to make the passive white
    outline jump wider/narrower.  The controller uses this cheap pixel check only
    to freeze the *displayed* width until glyphs return; OCR remains free to scan
    the calibrated source image and discover the next zone normally.
    """
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim < 2:
        return False
    try:
        mask = _outlined_text_mask(image)
    except Exception:
        return False
    if mask.size == 0:
        return False
    active_columns = int(np.count_nonzero(mask.sum(axis=0) >= 2))
    active_pixels = int(mask.sum())
    return active_columns >= max(1, int(min_active_columns)) and active_pixels >= max(1, int(min_pixels))


def trim_zone_line(
    image: np.ndarray,
    *,
    gap_stop: int = 16,
    padding: int = 12,
    empty_preview_width: int = 96,
) -> np.ndarray:
    """Return the exact left-anchored zone OCR crop.

    Zone X/Y/height are calibrated. Only the right edge is dynamic so long
    labels remain fully visible while unrelated HUD content farther right is
    excluded. No previous width or OCR result participates in this geometry.
    """
    return _trim_fixed_origin_text_group(
        image,
        gap_stop=gap_stop,
        padding=padding,
        empty_preview_width=empty_preview_width,
    )


def visible_zone_text_width(image: np.ndarray, *, scale: float = 1.0) -> int | None:
    """Measure the *display* text from its bright glyphs and dark outlines.

    The OCR's permissive image crop stays unchanged. The visual outline instead
    uses the first zone-name line only, excluding coordinates and scenery
    farther right. Return None for absent/unreliable text.
    """
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim != 3:
        return None
    h, w = image.shape[:2]
    y0 = min(h, max(0, round(2 * scale)))
    y1 = min(h, max(y0 + 1, round(26 * scale)))
    if h < 8 or w < 15 or y1 <= y0:
        return None

    # Most zone labels are short. Analyze only the left part of the HUD;
    # extend progressively for unusually long names, never truncate them.
    # This avoids processing the full 1920-pixel strip on every capture.
    # Word spacing in bold zone names may exceed 13 pixels at full scale.
    gap_limit = max(12, round(18 * scale))
    scan_width = min(w, max(256, round(512 * scale)))
    while True:
        region = image[y0:y1, :scan_width, :3]
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        bright = (hsv[:, :, 2] >= 215) & (hsv[:, :, 1] <= 75)
        near_dark = cv2.dilate(
            (hsv[:, :, 2] <= 108).astype(np.uint8),
            np.ones((5, 5), dtype=np.uint8),
        ) > 0
        columns = _remove_short_true_runs(
            (bright & near_dark).sum(axis=0) >= 2, min_run=2,
        )
        first = np.flatnonzero(columns)
        if first.size == 0 or int(first[0]) > max(24, round(38 * scale)):
            return None

        last = -1
        gap = 0
        ended = False
        for x in range(scan_width):
            if columns[x]:
                last = x
                gap = 0
            elif last >= 0:
                gap += 1
                if gap >= gap_limit:
                    ended = True
                    break
        if last < max(6, round(8 * scale)):
            return None
        if ended or scan_width >= w:
            return min(w, last + 1 + max(3, round(6 * scale)))
        scan_width = min(w, scan_width * 2)

def extract_hud_inputs(
    image: np.ndarray,
    geometry: HUDGeometry | None = None,
    *,
    transform: HUDTransform | None = None,
) -> HUDInputs:
    geometry = geometry or HUDGeometry()
    if image is None or getattr(image, 'size', 0) == 0:
        empty = np.empty((0, 0, 3), dtype=np.uint8)
        return HUDInputs(empty, empty.copy(), empty.copy(), HUDTransform())

    transform = transform or estimate_hud_transform(image, geometry) or HUDTransform()
    combat_rect = _transformed_rect(
        image,
        ref_x=geometry.combat_x,
        ref_y=geometry.combat_y,
        ref_width=geometry.combat_width,
        ref_height=geometry.combat_height,
        transform=transform,
    )
    zone_y = transform.point(geometry.zone_x, geometry.zone_y)[1]
    zone_h = transform.size(1, geometry.zone_height)[1]
    h, w = image.shape[:2]
    zone_rect = (
        max(0, transform.origin_x),
        max(0, min(h - 1, zone_y)),
        w,
        max(1, min(h, zone_y + zone_h)),
    )
    position_rect = _transformed_rect(
        image,
        ref_x=geometry.position_x,
        ref_y=geometry.position_y,
        ref_width=geometry.position_width,
        ref_height=geometry.position_height,
        transform=transform,
    )
    combat = _crop(image, combat_rect)
    zone_max = _crop(image, zone_rect)
    position_max = _crop(image, position_rect)
    scale = transform.scale
    zone = trim_zone_line(
        zone_max,
        gap_stop=max(10, int(round(16 * scale))),
        padding=max(4, int(round(12 * scale))),
        empty_preview_width=max(32, int(round(96 * scale))),
    )
    # Recognition gets the complete calibrated 180 px slot. Overlay width is
    # computed later from the accepted PositionOCRResult, never from these pixels.
    position = np.ascontiguousarray(position_max)
    return HUDInputs(
        combat=combat,
        zone=zone,
        position=position,
        transform=transform,
        zone_display_width=visible_zone_text_width(zone_max, scale=scale),
    )

