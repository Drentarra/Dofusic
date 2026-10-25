"""Offline calibration of archived HUD fixtures; never imported by the application.

Live capture uses the game-client resolution curve. These image-based utilities
exist only to keep historical screenshot regression coverage reproducible.
"""
from __future__ import annotations

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry, HUDInputs, HUDTransform
from dofusic.vision.layout import extract_hud_inputs as _extract_hud_inputs


class HUDTransformSmoother:
    """Hysteresis for noisy toolbar-scale measurements on consecutive captures.

    The first reliable calibration (and the first after a resize) is immediate.
    Subsequent deviations need corroboration; temporary detection failures do
    not jump straight to the default 1x scale. No scene luminance is involved.
    """

    def __init__(self, confirmations: int = 2, miss_limit: int = 2):
        self.confirmations = max(2, int(confirmations))
        self.miss_limit = max(1, int(miss_limit))
        self.reset()

    def reset(self) -> None:
        self.stable: HUDTransform | None = None
        self.pending: HUDTransform | None = None
        self.pending_count = 0
        self.misses = 0

    def update(self, measured: HUDTransform | None) -> HUDTransform | None:
        if measured is None:
            # Losing sight of the toolbar (tooltip, occlusion, animation) is
            # not evidence that the game's DPI/HUD scale has changed. Keep the
            # last confirmed transform until an explicit window/size reset or
            # a newly corroborated measurement replaces it.
            self.misses += 1
            self.pending, self.pending_count = None, 0
            return self.stable
        self.misses = 0
        old = self.stable
        if old is None:
            self.stable = measured
            return measured
        if (abs(old.scale - measured.scale) < .015
                and abs(old.origin_x - measured.origin_x) < 2
                and abs(old.origin_y - measured.origin_y) < 2):
            self.pending, self.pending_count = None, 0
            return old
        previous = self.pending
        if (previous is None or abs(previous.scale - measured.scale) > .015
                or abs(previous.origin_x - measured.origin_x) >= 2
                or abs(previous.origin_y - measured.origin_y) >= 2):
            self.pending, self.pending_count = measured, 1
        else:
            self.pending_count += 1
            if self.pending_count >= self.confirmations:
                self.stable = measured
                self.pending, self.pending_count = None, 0
        return self.stable


def measure_toolbar_rim(
    image: np.ndarray, *, hint: HUDTransform | None = None,
) -> tuple[int, int, int, int] | None:
    """Measure the top-left Dofus control strip, including extra Havre-Sac controls.

    The toolbar has a continuous, nearly uniform *horizontal top border* and
    a coherent lower horizontal edge. Both are UI geometry, not map luminance.
    Scan only the top-left screen vicinity, allowing for a window/client offset.
    Return (origin_x, origin_y, right_exclusive, bottom_exclusive) in image pixels.
    No button count or predefined bar width participates in the measurement.

    Deliberately refuse ambiguous candidates: callers retain the confirmed HUD
    transform / icon-state fallback rather than adopting scenery as geometry.
    """
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim not in (2, 3):
        return None
    h, w = image.shape[:2]
    if h < 42 or w < 60:
        return None
    top_limit = min(h - 25, 100)
    left_limit = min(w - 32, 120)
    # The top rim is a flat strip, irrespective of the icon colours and artwork.
    # A broad saturated stroke (e.g. an annotation) is not toolbar material.
    source = image if image.ndim == 3 else image[:, :, None]
    # The normal path probes the previously calibrated origin first, avoiding
    # a full scan through window padding every capture. Falling back to the
    # whole nearby area also detects a genuinely relocated HUD.
    ordered_y = list(range(top_limit))
    if hint is not None:
        nearby_y = [int(hint.origin_y) + d for d in (0, -1, 1, -2, 2)]
        ordered_y = [y for y in nearby_y if 0 <= y < top_limit] + ordered_y
    visited_y: set[int] = set()
    # A candidate border has at least 28 nearly uniform pixels. Reject rows
    # and X offsets that cannot possibly satisfy the original 15-unit per-pixel
    # rim test *before* running the expensive per-offset median/span search.
    # This matters when a Dofus popup hides the toolbar: previously a failed
    # measurement could scan ~100 rows x 120 offsets x the full 1920px width,
    # freezing the capture worker for >1 second despite healthy MSS grabs.
    flat_kernel = np.ones((1, 28), dtype=np.uint8)
    # The original recognizer also requires a near-horizontal *lower* edge of
    # the first toolbar button: >=90% of a 28..32 pixel stripe must change
    # sharply on one row at least 18px below the proposed top. Checking just
    # the first 28px at a looser 22/28 threshold is a NECESSARY condition for
    # any original success (even if the complete 32px stripe is used).
    # Precompute it for all candidate offsets before the expensive X scan. A
    # two-colour inventory panel otherwise defeats the flat-row shortcut and
    # can still stall the capture for 2 seconds.
    edge_source = source[:, :left_limit + 28].astype(np.int16)
    edge_changes = (np.max(np.abs(np.diff(edge_source, axis=0)), axis=2) > 15)
    edge_prefix = np.pad(np.cumsum(edge_changes, axis=1, dtype=np.int16), ((0, 0), (1, 0)))
    edge_counts = edge_prefix[:, 28:] - edge_prefix[:, :-28]
    for top in ordered_y:
        if top in visited_y:
            continue
        visited_y.add(top)
        candidate_edge = np.any(
            edge_counts[top + 17:min(h - 1, top + 120), :left_limit] >= 22,
            axis=0,
        )
        if not np.any(candidate_edge):
            continue
        row = source[top].astype(np.int16)
        # Skip the unoccupied upper-left padding without scanning every X.
        if int(np.max(row[:left_limit + 12])) < 24:
            continue
        if (w > left_limit + 651
                and np.all(np.max(row, axis=0) - np.min(row, axis=0) <= 15)):
            # A globally flat row cannot end within the allowed 650px rim.
            # This is common for solid-color/fullscreen game menus.
            continue
        # For a valid candidate, the first 28 pixels necessarily lie within
        # +/-15 of their median for every channel. The min/max range is thus
        # <=30 for those pixels. Sliding morphology computes this necessary
        # condition in native OpenCV instead of evaluating thousands of Python
        # candidates on each frame. It never rejects an otherwise valid rim.
        scan = source[top:top + 1, :left_limit + 28]
        if scan.dtype == np.uint8:
            lo = cv2.erode(scan, flat_kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
            hi = cv2.dilate(scan, flat_kernel, anchor=(0, 0), borderType=cv2.BORDER_REPLICATE)
            # OpenCV removes the final singleton channel on grayscale inputs.
            if lo.ndim == 2:
                lo, hi = lo[:, :, None], hi[:, :, None]
            flat_offsets = np.all(
                hi[0, :left_limit].astype(np.int16) - lo[0, :left_limit].astype(np.int16) <= 30,
                axis=1,
            )
        else:
            # Non-uint8 test inputs retain the original matching semantics.
            flat_offsets = None
        ordered_x = list(range(left_limit))
        if hint is not None:
            near_x = [int(hint.origin_x) + d for d in (0, -1, 1, -2, 2)]
            ordered_x = [x for x in near_x if 0 <= x < left_limit] + ordered_x
        visited_x: set[int] = set()
        for left in ordered_x:
            if left in visited_x:
                continue
            visited_x.add(left)
            if not candidate_edge[left]:
                continue
            if flat_offsets is not None and not flat_offsets[left]:
                continue
            color = np.median(row[left:left + 12], axis=0)
            if color.max() < 24 or (color.max() - color.min()) > 95:
                continue
            error = np.max(np.abs(row[left:] - color), axis=1)
            interruptions = np.flatnonzero(error > 15)
            span = int(interruptions[0]) if interruptions.size else len(error)
            if not 28 <= span <= min(650, w - left - 1):
                continue
            # The next interior rows must belong to the *same* material.
            # This excludes drawn annotations, single flat map streaks and
            # thin non-HUD lines that can also look like a horizontal rim.
            next_interior = source[top + 2, left + min(4, span // 4)]
            if np.max(np.abs(next_interior.astype(np.float32) - color)) > 36:
                continue
            # The outer border ends at the same Y across the *first button*.
            # Using that vertical seam prevents a sunlit/textured scene below
            # the HUD from being mistaken for a taller toolbar.
            probe_w = min(32, span)
            stripe = source[top:min(h, top + 121), left:left + probe_w].astype(np.int16)
            if stripe.shape[0] < 23:
                continue
            delta = np.abs(stripe[1:] - stripe[:-1]).max(axis=2)
            sharp = (delta > 15).mean(axis=1)
            edges = np.flatnonzero(sharp[17:] >= .90)
            if not edges.size:
                continue
            bottom = top + 18 + int(edges[0])
            height = bottom - top
            if 19 <= height <= 111:
                if left <= 3 and np.max(source[top, 0]) >= 24:
                    left = 0
                return left, top, left + span, bottom
    return None


def estimate_hud_transform(
    image: np.ndarray,
    geometry: HUDGeometry | None = None,
    *,
    previous: HUDTransform | None = None,
    require_rim: bool = False,
) -> HUDTransform | None:
    """Infer HUD scale from the theme-independent toolbar background height.

    The first toolbar row is anchored at the client top-left. Its background is
    contiguous while the pixels directly below it are black before the zone
    label starts. Measuring that vertical run is more reliable than scaling from
    desktop/client resolution and works with the green, purple and Havre-Sac UI.
    """
    geometry = geometry or HUDGeometry()
    rim = measure_toolbar_rim(image, hint=previous)
    if rim is not None:
        left, top, right, bottom = rim
        height = bottom - top
        scale = height / float(geometry.toolbar_reference_height)
        if .35 <= scale <= 3.0:
            # A real toolbar has a small set of physical aspect ratios in its
            # collapsed, expanded, and Havre-Sac layouts. Random tooltip/map
            # strips with a convincing bottom edge do not qualify as anchors.
            # Use this additional check only after we already have a confirmed
            # transform: bootstrap retains its original discovery tolerance.
            if require_rim and previous is not None:
                if (abs(scale - previous.scale) < .015
                        and abs(left - previous.origin_x) < 2
                        and abs(top - previous.origin_y) < 2):
                    # Already aligned; no revalidation of a 24px image patch
                    # on every capture. Expensive checks apply to *changes*.
                    return HUDTransform(scale=scale, origin_x=left, origin_y=top)
                normalized_width = (right - left) / scale
                expected_widths = (38, 76, 208, 308)
                plausible = any(abs(normalized_width - value) <= max(6, value * .055)
                                for value in expected_widths)
                # Position is not assumed to stay at (0, 0): an actual HUD
                # relocation remains possible and the temporal filter below
                # confirms it before changing the visible geometry.
                # Flat notification boxes can have precisely the same aspect
                # ratio as a toolbar. Verify that the first button actually
                # contains the textured/icon graphics expected of a real HUD.
                # Only a tiny patch is sampled; no full-frame extra processing.
                x0, x1 = left + round(height * .15), left + round(height * .86)
                y0, y1 = top + round(height * .15), top + round(height * .86)
                button = image[y0:y1, x0:x1]
                detailed = (button.size > 0 and
                            float(cv2.meanStdDev(button)[1].max()) >= 8.0)
                if not (plausible and detailed):
                    return None
            return HUDTransform(scale=scale, origin_x=left, origin_y=top)
    if require_rim:
        # Once a HUD has been calibrated, a background/tooltip-induced fallback
        # is not independent evidence that the toolbar changed size. Reuse the
        # confirmed scale until an actual toolbar edge is measured or the game
        # window changes dimensions (which resets the calibration).
        return None
    return _bootstrap_hud_transform(image, geometry)


def _bootstrap_hud_transform(image: np.ndarray, geometry: HUDGeometry) -> HUDTransform | None:
    """Initial scale inference when no validated toolbar rim is available.

    Called only before calibration: once calibrated, a missing rim never
    invents a new scale from arbitrary map brightness.
    """
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
    # Fallback is accepted only with a coherent, physical toolbar edge.
    # A dark/light map transition is not a calibration anchor by itself.
    if toolbar_end >= probe_h:
        return None
    seam_before = probe[toolbar_end - 1].astype(np.int16)
    seam_after = probe[toolbar_end].astype(np.int16)
    seam_delta = np.abs(seam_after - seam_before)
    if seam_delta.ndim == 2:
        seam_delta = seam_delta.max(axis=-1)
    if float(np.mean(seam_delta > 15)) < .75:
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


def toolbar_icon_signature(image: np.ndarray, transform: HUDTransform) -> np.ndarray | None:
    """Tiny scale-normalized picture of the first physical toolbar button.

    A candidate bar can have the right rectangular dimensions and still be a
    tooltip. Comparing actual icon pixels against the last confirmed HUD is
    a second, independent check during an attempted scale/anchor change.
    """
    if image is None or getattr(image, 'size', 0) == 0:
        return None
    h = round(37 * transform.scale)
    x0 = transform.origin_x + round(h * .15)
    y0 = transform.origin_y + round(h * .15)
    x1 = transform.origin_x + round(h * .86)
    y1 = transform.origin_y + round(h * .86)
    if (x0 < 0 or y0 < 0 or x1 > image.shape[1] or
            y1 > image.shape[0] or x1 <= x0 or y1 <= y0):
        return None
    button = image[y0:y1, x0:x1]
    return cv2.resize(button, (24, 24), interpolation=cv2.INTER_AREA)


def toolbar_icon_consistent(reference: np.ndarray | None,
                            candidate: np.ndarray | None) -> bool:
    """Reject a geometrically plausible but visually unrelated toolbar."""
    if reference is None or candidate is None or reference.shape != candidate.shape:
        return False
    a = reference.astype(np.float32)
    b = candidate.astype(np.float32)
    if float(np.abs(a - b).mean()) > 20.0:
        return False
    a -= float(a.mean())
    b -= float(b.mean())
    energy = float(np.linalg.norm(a) * np.linalg.norm(b))
    return energy > 1.0 and float(np.sum(a * b) / energy) >= .32


def _recognized_toolbar_at(image: np.ndarray, geometry: HUDGeometry, candidate: HUDTransform) -> bool:
    """Independent positive evidence for a one-pixel HUD-scale change.

    An icon can legitimately change from + to - (or between Dofus' toolbar
    modes). Requiring pixel similarity to the *old* icon then freezes a wrong
    scale indefinitely. Instead verify the complete glyph arrangement at the
    proposed scale, using the existing high-specificity combat recognizer.
    """
    from dofusic.vision.combat import analyze_combat_toolbar

    x, y = candidate.point(geometry.combat_x, geometry.combat_y)
    w, h = candidate.size(geometry.combat_width, geometry.combat_height)
    if x < 0 or y < 0 or x + w > image.shape[1] or y + h > image.shape[0]:
        return False
    observation = analyze_combat_toolbar(image[y:y + h, x:x + w], geometry)
    return observation.in_combat is not None and observation.confidence >= 0.80


def validated_hud_measurement(
    image: np.ndarray, geometry: HUDGeometry,
    stable: HUDTransform | None, icon_signature: np.ndarray | None,
) -> HUDTransform | None:
    """Validate recalibration without coupling toolbar geometry to old icon art.

    Scale changes of one physical toolbar pixel occur when the simulator (or
    Dofus) switches the collapsed/expanded HUD. A stale 29px scale cannot read
    the 30px expanded glyphs; the old strict width/pixel-equality guard
    rejected their *correct* candidate indefinitely. For such tiny changes,
    validate a freshly classified complete toolbar, not the old icon pixels.
    Larger reallocations and relocation keep their strict historical checks.
    """
    measured = estimate_hud_transform(
        image, geometry, previous=stable, require_rim=stable is not None,
    )
    if stable is None:
        return measured

    def small_scale_change(candidate: HUDTransform) -> bool:
        return (abs(candidate.origin_x - stable.origin_x) <= 1
                and abs(candidate.origin_y - stable.origin_y) <= 1
                and 0 < abs(candidate.scale - stable.scale) <= 1.1 / geometry.toolbar_reference_height)

    # The strict rim check can reject the valid expanded bar when a top-edge
    # colour seam cuts off its measured width. Re-evaluate *only nearby scale*
    # candidates, and only on independent shape evidence.
    if measured is None:
        candidate = estimate_hud_transform(image, geometry, previous=stable)
        if (candidate is not None and small_scale_change(candidate)
                and _recognized_toolbar_at(image, geometry, candidate)):
            return candidate
        return None

    changed = (abs(measured.scale - stable.scale) >= .015
               or abs(measured.origin_x - stable.origin_x) >= 2
               or abs(measured.origin_y - stable.origin_y) >= 2)
    if changed and icon_signature is not None:
        candidate_icon = toolbar_icon_signature(image, measured)
        if not toolbar_icon_consistent(icon_signature, candidate_icon):
            if small_scale_change(measured) and _recognized_toolbar_at(image, geometry, measured):
                return measured
            return None
    return measured


def extract_reference_hud_inputs(
    image: np.ndarray, geometry: HUDGeometry | None = None, *,
    transform: HUDTransform | None = None,
) -> HUDInputs:
    """Calibrate an archived crop whose original client dimensions are unknown."""
    geometry = geometry or HUDGeometry()
    if transform is None:
        transform = estimate_hud_transform(image, geometry) or HUDTransform()
    return _extract_hud_inputs(image, geometry, transform=transform)
