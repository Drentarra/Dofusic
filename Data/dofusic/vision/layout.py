from __future__ import annotations

from dataclasses import dataclass
from unicodedata import normalize

import cv2
import numpy as np

from dofusic.vision.resolution_curve import reference_hud_scale


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

    The live HUD geometry uses an offline-fitted monotone resolution curve,
    never per-frame calibration. Reference values are from a 1920x1080 Dofus
    client; only the right edges are measured from images.
    Reference-image calibration lives only in tools/hud_reference.py.
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
        origin_y: int = 0,
    ) -> tuple[int, int, int, int]:
        """Return the top HUD capture strip in *physical client pixels*.

        Width always follows the actual client because zone names are dynamic.
        Production passes a deterministic game-client resolution scale.
        The optional bootstrap strip remains for offline compatibility tests.
        """
        client_width = max(0, int(client_width))
        client_height = max(0, int(client_height))
        if client_width <= 0 or client_height <= 0:
            return 0, 0, 0, 0
        scale = None if ui_scale is None else max(0.01, float(ui_scale))
        if scale is None:
            height = self.bootstrap_capture_height
        else:
            height = max(0, int(origin_y)) + int(round((self.reference_bottom + self.capture_margin) * scale))
        height = max(self.combat_height, height)
        return 0, 0, client_width, min(client_height, height)

    def combat_button_reference_rect(self, index: int = 0) -> tuple[int, int, int, int]:
        x, y, width, height = self.combat_button_rects[int(index)]
        return self.combat_x + x, self.combat_y + y, width, height

    def combat_button_visual_reference_rect(self) -> tuple[int, int, int, int]:
        x, y, width, height = self.combat_button_visual_rect
        return self.combat_x + x, self.combat_y + y, width, height


def hud_transform_from_client_size(
    client_width: int, client_height: int, *, geometry: HUDGeometry | None = None,
) -> HUDTransform | None:
    """Build the HUD transform from the Dofus client size, never from pixels.

    The empirical scale depends on the limiting normalized client dimension,
    with a plateau at 1x. Fullscreen is checked by the capture service.
    The curve is computed only when the game's Win32 client size changes.
    """
    geometry = geometry or HUDGeometry()
    if (client_width <= 0 or client_height <= 0
            or geometry.reference_client_height <= 0):
        return None
    return HUDTransform(
        scale=reference_hud_scale(
            client_width, client_height,
            reference_width=geometry.reference_client_width,
            reference_height=geometry.reference_client_height,
        ),
        origin_x=0, origin_y=0,
    )


def measure_toolbar_right_at_transform(
    image: np.ndarray, transform: HUDTransform, *, geometry: HUDGeometry | None = None,
) -> int | None:
    """Measure only the RIGHT edge of the toolbar at its known HUD origin.

    Unlike the legacy global rim detector, this has no search across X/Y. It
    probes one top-border row and validates the expected bottom edge around
    the known toolbar height. Pop-ups can invalidate the width observation,
    but can never change the origin, scale, or height. In that case the caller
    falls back to the known physical button widths.
    """
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim not in (2, 3):
        return None
    geometry = geometry or HUDGeometry()
    h, w = image.shape[:2]
    x, y = transform.point(geometry.combat_x, geometry.combat_y)
    scale = transform.scale
    bar_h = transform.size(1, geometry.toolbar_reference_height)[1]
    # The first toolbar control must have enough visible material for the
    # right edge and its validation stripe to be reliable.
    min_span = max(12, round(28 * scale))
    if x < 0 or y < 0 or x + min_span + 2 >= w or y + bar_h + 2 >= h:
        return None
    source = image if image.ndim == 3 else image[:, :, None]
    row = source[y, x:min(w, x + max(50, round(650 * scale)) + 2)].astype(np.int16)
    color = np.median(row[:max(8, round(12 * scale))], axis=0)
    if color.max() < 24 or color.max() - color.min() > 95:
        return None
    error = np.max(np.abs(row - color), axis=1)
    differences = np.flatnonzero(error > 15)
    width = int(differences[0]) if differences.size else 0
    if width < min_span or width > min(round(650 * scale), w - x - 1):
        return None
    probe_x = x + min(max(2, round(4 * scale)), width - 1)
    inside_y = y + max(1, round(2 * scale))
    if np.max(np.abs(source[inside_y, probe_x].astype(np.float32) - color)) > 36:
        return None
    stripe_width = min(width, max(12, round(32 * scale)))
    # Require a clear border close to the expected physical bar height;
    # tolerate interpolation and +/-2 pixels from fractional scaling.
    y_low = max(y + 1, y + bar_h - max(2, round(3 * scale)))
    y_high = min(h - 1, y + bar_h + max(2, round(3 * scale)))
    for edge_y in range(y_low, y_high + 1):
        a = source[edge_y - 1, x:x + stripe_width].astype(np.int16)
        b = source[edge_y, x:x + stripe_width].astype(np.int16)
        # Bilinear image scaling softens a few edge pixels; >=88% remains
        # sufficiently strict while supporting 1.5x reference images.
        if float(np.mean(np.max(np.abs(b - a), axis=1) > 15)) >= .88:
            return width
    return None


@dataclass(frozen=True, slots=True)
class HUDInputs:
    zone: np.ndarray
    # Fixed calibrated ROI sent to the position OCR worker. It is intentionally
    # wider than the visible coordinate token so OCR correctness never depends
    # on overlay geometry.
    position: np.ndarray
    combat: np.ndarray | None = None
    transform: HUDTransform = HUDTransform()
    # Physically measured full toolbar width, including Havre-Sac trailing controls.
    toolbar_width: int | None = None


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
    scale = max(0.01, float(scale))
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


def combat_toolbar_frame_width(button_count: int | None, scale: float) -> int:
    """Stable rectangle from recognized UI state, never from scene brightness.

    Dofus has a collapsed bar of one/two buttons and a six-button expanded bar.
    The score used to recognize these states belongs to the combat detector;
    geometry never guesses from pixels of the map.
    """
    # The final icon ends at reference x=203; five px of HUD edge follow.
    # Collapsed states retain their already-validated widths.
    sizes = {1: 38, 2: 76, 6: 208}
    return max(1, round(sizes.get(button_count, 38) * max(.01, float(scale))))


def verified_combat_toolbar_width(measured: int | None, button_count: int | None, scale: float) -> int:
    """A decorative rim cannot overrule the physical size of known buttons.

    The detector supplies the number of controls. The optional visual rim is
    useful for longer Havre-Sac bars, but it can also stop at a tooltip, a
    glyph or a hover highlight. Accept it only when it matches a physically
    possible toolbar for the observed state; otherwise use the fixed size.
    No map-specific adjustments or scene-brightness thresholds are involved.
    """
    fallback = combat_toolbar_frame_width(button_count, scale)
    if measured is None or int(measured) <= 0:
        return fallback
    factor = max(.01, float(scale))
    physical = float(measured) / factor
    # A two-control Havre-Sac can retain trailing buttons; the expanded
    # Havre-Sac is similarly longer than the regular six-control bar.
    profiles = {1: (38,), 2: (76, 206), 6: (208, 305)}
    options = profiles.get(button_count, (38, 76, 208, 305))
    for expected in options:
        if abs(physical - expected) <= max(5., expected * .055):
            return int(measured)
    return fallback


def tracking_screen_rects(
    *,
    capture_screen_rect: tuple[int, int, int, int],
    capture_image_shape: tuple[int, ...],
    hud: HUDInputs,
    geometry: HUDGeometry | None = None,
    toolbar_buttons: int | None = None,
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

    # Fixed top/left/height from HUD calibration; only the right edge varies.
    # Prefer a measured border for *all* panels (especially Havre-Sac), falling
    # back to the recognized icon state when the border is not observable.
    toolbar = map_reference_rect(
        geometry.combat_x, geometry.combat_y,
        geometry.combat_width, geometry.toolbar_reference_height,
    )
    visible_width = verified_combat_toolbar_width(hud.toolbar_width, toolbar_buttons, transform.scale)
    zone_rect = map_crop(geometry.zone_x, geometry.zone_y, hud.zone)
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
    bright = (value >= 165) & (saturation <= 95)
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


def _aligned_zone_glyph_columns(
    mask: np.ndarray, *, wh: int, ww: int, initial_span: int,
) -> np.ndarray | None:
    """Keep components aligned with the first glyphs, rejecting stray scenery."""
    n, labels, stats, centres = cv2.connectedComponentsWithStats(mask, 8)
    min_area = max(4, round(4 * (wh / 34.0) ** 2))
    components = []
    for i in range(1, n):
        left, top, width, height, area = (int(v) for v in stats[i])
        if area < min_area:
            continue
        # A dash is often only two rows high; discarding every low component
        # can make "- Salle du portail" disappear from an otherwise long HUD
        # label. Admit a small *horizontal* punctuation candidate in the text
        # band, then let the aligned-word continuity filter decide its fate.
        is_dash = (1 <= height <= 3 and width >= max(5, round(wh * .14))
                   and wh * .27 <= top <= wh * .53)
        if height < max(3, round(wh * .13)) and not is_dash:
            continue
        components.append((i, left, left + width, float(centres[i][1])))

    # A name begins near the HUD origin. Derive the shared text line from its
    # first characters, not from a predetermined list of French place names.
    anchors = [cy for _, left, _, cy in components if left < initial_span]
    if not anchors:
        return None
    baseline = float(np.median(anchors))
    aligned = np.zeros(ww, dtype=bool)
    for i, left, right, cy in components:
        if abs(cy - baseline) <= max(3.0, wh * .18):
            aligned[left:right] = True
    aligned = _remove_short_true_runs(aligned, min_run=2)

    return aligned


def _last_zone_glyph_column(
    aligned: np.ndarray, *, wh: int, h: int, factor: float,
    gap_stop: int, initial_span: int,
) -> int | None:
    """Find the last coherent glyph cluster, accounting for detached punctuation."""
    seeds = np.flatnonzero(aligned[:initial_span])
    if not seeds.size:
        return None
    # Split into plausible glyph clusters. A detached one/two-stroke flash on
    # the scenery beyond a word gap is not enough evidence for another word.
    runs: list[tuple[int, int]] = []
    for x in range(len(aligned)):
        if aligned[x] and (x == 0 or not aligned[x-1]):
            runs.append((x, x + 1))
        elif aligned[x]:
            runs[-1] = (runs[-1][0], x + 1)
    groups: list[list[tuple[int, int]]] = []
    intra_word = max(8, round(wh * .45))
    for run in runs:
        if not groups or run[0] - groups[-1][-1][1] > intra_word:
            groups.append([])
        groups[-1].append(run)

    last = int(seeds[0])
    stop = max(8, round(gap_stop * factor), round(wh * .90),
               round(37 * factor) if h < 24 else 0)
    for group_index, group in enumerate(groups):
        start, finish = group[0][0], group[-1][1]
        if finish <= last:
            continue
        gap = start - last
        # On long labels, a hyphen or punctuation mark may be detached from
        # the following word by a wider gap. Extend only when the next group
        # looks like a *word* (several aligned strokes); scenery singletons
        # must not extend the capture arbitrarily.
        tail_word = (
            group_index + 1 < len(groups)
            and len(groups[group_index + 1]) >= 4
            and groups[group_index + 1][0][0] - finish <= max(24, round(wh * .8))
        )
        plausible_tail = len(group) >= 4 or (len(group) <= 2 and tail_word)
        extended_gap = max(stop, round(65 * factor))
        if gap > stop and (last < round(180 * factor) or gap > extended_gap or not plausible_tail):
            break
        if (h >= 24 and group_index and len(groups[0]) >= 6
                and len(group) <= 2 and gap > intra_word and not tail_word):
            break
        last = finish - 1
    return last


def _trim_fixed_origin_text_group(
    image: np.ndarray,
    *,
    gap_stop: int,
    padding: int,
    empty_preview_width: int,
) -> np.ndarray:
    """Find a coherent *line* of outlined HUD glyphs, never just its first word.

    The ROI starts at the calibrated HUD origin. We measure the top text band
    and associate outlined components with the first glyphs' vertical alignment.
    The content boundary is a long absence of aligned glyphs, not a single
    blank word separator. Scenery contrast alone cannot define the boundary.
    OCR receives precisely the pixels measured for the visible rectangle.
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return np.empty((0, 0, 3), dtype=np.uint8)
    h, w = image.shape[:2]
    if w <= 1:
        return np.ascontiguousarray(image)

    factor = 34.0 / h if h < 24 else 1.0
    work = image if factor == 1.0 else cv2.resize(
        image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC,
    )
    wh, ww = work.shape[:2]
    band_h = max(1, min(wh, round(wh * .69)))
    mask = _outlined_text_mask(work)[:band_h]
    initial_span = min(ww, max(round(wh * 3.0), round(64 * factor)))
    aligned = _aligned_zone_glyph_columns(mask, wh=wh, ww=ww, initial_span=initial_span)
    if aligned is None:
        preview = max(1, min(w, round(empty_preview_width)))
        return np.ascontiguousarray(image[:, :preview])
    last = _last_zone_glyph_column(aligned, wh=wh, h=h, factor=factor,
                                   gap_stop=gap_stop, initial_span=initial_span)
    if last is None:
        return np.ascontiguousarray(image[:, :max(1, min(w, round(empty_preview_width)))])
    end = last + 1 + max(1, round(padding * factor))
    return np.ascontiguousarray(image[:, :max(1, min(w, round(end / factor)))])


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
    gap_stop: int = 42,
    padding: int = 6,
    empty_preview_width: int = 96,
) -> np.ndarray:
    """One common, live-pixel width for OCR and its visible tracking frame.

    Inter-word spaces (including before parentheses) are *not* line endings.
    The HUD's own outlined glyphs determine the final right edge; OCR text is
    never used to guess the crop or to override the rectangle.
    """
    return _trim_fixed_origin_text_group(
        image,
        gap_stop=gap_stop,
        padding=padding,
        empty_preview_width=empty_preview_width,
    )


# Visual guard only: no dependency on installed Windows fonts or the audio
# resolver. Advances approximate the game's 20 px bold zone lettering at 1x.
# This is deliberately used only to reject *grossly* oversized contours after
# an accepted OCR result. The OCR image itself is never shrunk by this guard.
_ZONE_GLYPHS = dict(zip(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ()-/.,:'%+!_",
    (13.5, 14.3, 11.9, 14.3, 13.6, 8.7, 14.3, 14.2, 6.9, 6.9,
     13.3, 6.9, 20.8, 14.2, 13.7, 14.3, 14.3, 9.9, 11.9, 9.6,
     14.2, 13.0, 18.5, 12.9, 13.0, 11.6,
     15.5, 15.2, 14.7, 16.6, 13.7, 13.7, 16.4, 16.7, 7.4, 7.4,
     15.5, 12.8, 19.9, 16.7, 17.0, 14.7, 17.0, 15.4, 14.4,
     13.6, 16.2, 15.5, 22.1, 15.4, 14.5, 14.5,
     *([13.9] * 10), 7.0, 9.1, 9.1, 8.3, 7.3, 7.6, 7.6, 8.0,
     6.1, 20.0, 16.8, 9.1, 10.0),
))


def _zone_label_typographic_width(text: str, scale: float) -> int | None:
    """Cheap approximate label length, only for rejecting excessive scenery."""
    if not text or len(text) > 180:
        return None
    converted = normalize('NFKD', text).replace('œ', 'oe').replace('Œ', 'OE')
    from unicodedata import combining
    total = sum(_ZONE_GLYPHS.get(c, 0.0) for c in converted if not combining(c))
    if not total or any(c not in _ZONE_GLYPHS for c in converted if not combining(c)):
        return None  # Unknown alphabet: trust the live HUD rather than guessing.
    return max(1, round(total * max(.01, float(scale)) + 6))


class ZoneOverlayWidthTracker:
    """Keep the WHITE contour tied to a credible OCR observation, not a hover.

    OCR still processes the live, dynamic pixel crop independently. The screen
    contour is updated only when a recognized zone text changes. Tooltips and
    mouseover animations can alter the image-based width on every frame without
    representing a real change of zone; they must not resize the contour.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.window_id: int | None = None
        self.scale: float | None = None
        self.width: int | None = None
        self.text: str | None = None

    def observe(self, proposed_width: int, *, window_id: int, scale: float) -> int:
        if (self.window_id != int(window_id)
                or (self.scale is not None and abs(float(scale) - self.scale) > .075)):
            self.reset()
        self.window_id = int(window_id)
        self.scale = float(scale)
        if self.width is None:
            self.width = max(1, int(proposed_width))
        return self.width

    def confirm(self, text: str, width: int, *, window_id: int,
                scale: float, confidence: float) -> bool:
        # A merely nonempty or low-confidence OCR result may be a tooltip,
        # not a replacement for a readable Dofus place label.
        if not text.strip() or confidence < .80 or width < 1:
            return False
        if self.window_id != int(window_id) or self.scale is None or abs(scale - self.scale) > .075:
            return False  # late result from an older window/scale
        name = ' '.join(text.casefold().split())
        if name != self.text:
            measured = int(width)
            estimated = _zone_label_typographic_width(text.strip(), scale)
            # Scene decorations sometimes resemble outlined HUD glyphs and
            # extend the dynamic crop by hundreds of pixels. Use typography
            # only as a generous maximum, not to change normal measured widths.
            if estimated is not None and measured > estimated + max(30, round(estimated * .18)):
                measured = estimated
            self.width = measured
            self.text = name
            return True
        return False


class ZoneCropStabilizer:
    """Debounce live *capture* widths, not text/overlay widths separately.

    Expansions and contractions require corroboration across frames; the OCR
    crop and its overlay always use the exact same accepted width. A genuine
    shorter or longer label is admitted after a small number of captures.
    Reset on a different window or a material HUD-scale change.
    """

    def __init__(self, confirmations: int = 2, tolerance_px: int = 3):
        self.confirmations = max(1, int(confirmations))
        self.tolerance_px = max(0, int(tolerance_px))
        self.reset()

    def reset(self) -> None:
        self.width: int | None = None
        self.pending_width: int | None = None
        self.pending_count = 0
        self.scale: float | None = None
        self.window_id: int | None = None

    def observe(self, proposed_width: int, *, scale: float, window_id: int, maximum: int) -> int:
        proposed_width = max(1, min(int(maximum), int(proposed_width)))
        scale = float(scale)
        if self.window_id != window_id or (self.scale is not None and abs(scale - self.scale) > .075):
            self.reset()
        self.window_id = window_id
        self.scale = scale
        if self.width is None:
            self.width = proposed_width
        elif abs(proposed_width - self.width) <= self.tolerance_px:
            self.pending_width = None
            self.pending_count = 0
        else:
            if self.pending_width is None or abs(proposed_width - self.pending_width) > self.tolerance_px:
                self.pending_width, self.pending_count = proposed_width, 1
            else:
                self.pending_count += 1
                if self.pending_count >= self.confirmations:
                    self.width = proposed_width
                    self.pending_width = None
                    self.pending_count = 0
        return min(int(maximum), self.width)


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

    transform = transform or HUDTransform()
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
        gap_stop=max(8, int(round(42 * scale))),
        padding=max(2, int(round(6 * scale))),
        empty_preview_width=max(32, int(round(96 * scale))),
    )
    # Recognition gets the complete calibrated 180 px slot. Overlay width is
    # computed later from the accepted PositionOCRResult, never from these pixels.
    position = np.ascontiguousarray(position_max)
    # Known origin/height: probe only the RIGHT edge (no X/Y search).
    measured_width = measure_toolbar_right_at_transform(image, transform, geometry=geometry)
    return HUDInputs(
        combat=combat,
        zone=zone,
        position=position,
        transform=transform,
        toolbar_width=measured_width,
    )
