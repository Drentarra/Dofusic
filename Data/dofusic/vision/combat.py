from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from math import ceil

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry
from dofusic.vision.combat_shapes import GLYPH_ROWS


@dataclass(frozen=True, slots=True)
class CombatObservation:
    """One visual observation of Dofus' top-left action toolbar.

    ``in_combat`` is ``None`` when the toolbar is not reliable enough to make a
    decision. ``luminance_ratio`` is retained as a compatibility diagnostic and now
    carries the silhouette similarity. Classification uses shapes, not colours.
    """

    in_combat: bool | None
    confidence: float
    luminance_ratio: float
    first_peak: float
    reference_peak: float
    # Bounds of the glyphs used for this observation, in input ROI pixels.
    # Unknown observations do not claim a recognized outline.
    icon_rect: tuple[int, int, int, int] | None = None


# The new toolbar has a +/- control followed by 34-pixel icon slots.
# Keep a one-pixel tolerance for the independently estimated HUD scale.
_GLYPH_X = (6, 42, 76, 110, 144, 178)
_GLYPH_SIZE = 25


def _glyph_mask(patch: np.ndarray) -> np.ndarray | None:
    """Extract a silhouette using contrast against its own corner background.

    Absolute RGB, hue, and whether the glyph is lighter or darker than its
    background are immaterial. The strongest channel difference also preserves
    coloured glyphs with similar grayscale luminance to their theme background.
    """
    if patch.shape[:2] != (_GLYPH_SIZE, _GLYPH_SIZE):
        return None
    if patch.ndim == 2:
        patch = patch[:, :, None]
    corners = np.concatenate((
        patch[:2, :2].reshape(-1, patch.shape[2]),
        patch[:2, -2:].reshape(-1, patch.shape[2]),
        patch[-2:, :2].reshape(-1, patch.shape[2]),
        patch[-2:, -2:].reshape(-1, patch.shape[2]),
    ))
    background = np.median(corners, axis=0)
    contrast = np.abs(patch.astype(np.float32) - background).max(axis=2)
    peak = float(np.percentile(contrast, 98))
    if peak < 5:
        return None
    mask = (contrast > max(4.0, peak * 0.34)).astype(np.uint8)
    if not 8 <= int(mask.sum()) <= 480:
        return None
    return mask


def _shape_score(mask: np.ndarray | None, name: str) -> float:
    """Compare binary shapes, tolerating small resampling/position differences."""
    if mask is None:
        return 0.0
    return _cached_shape_score(mask.tobytes(), name)


@lru_cache(maxsize=256)
def _cached_shape_score(pixels: bytes, name: str) -> float:
    # HUD silhouettes usually stay identical between frames even when the map
    # animates. Cache only the binary patch/name, bounded to about 160 KiB of
    # pixel keys; every frame still extracts the currently visible glyphs.
    mask = np.frombuffer(pixels, dtype=np.uint8).reshape(_GLYPH_SIZE, _GLYPH_SIZE)
    reference = _SHAPES[name]
    # A symmetric overlap prevents a partial icon or arbitrary solid window
    # from matching just a small fragment of a known glyph.
    shifted = np.lib.stride_tricks.sliding_window_view(np.pad(mask, 1), (_GLYPH_SIZE, _GLYPH_SIZE))
    areas = np.count_nonzero(shifted, axis=(-2, -1))
    intersection = np.count_nonzero(shifted & reference, axis=(-2, -1))
    best = float(np.max(2.0 * intersection / np.maximum(1, areas + _SHAPE_AREAS[name])))
    if name in _TOGGLE_OUTLINES:
        # The +/- strokes are only 1-2 pixels thick. Their reference pixels are
        # well inside the patch, so dilating once before shifting gives the same
        # coverage as nine per-shift dilations, including at fractional scales.
        expanded = cv2.dilate(mask, _DILATION_KERNEL)
        expanded_shifts = np.lib.stride_tricks.sliding_window_view(np.pad(expanded, 1), (_GLYPH_SIZE, _GLYPH_SIZE))
        precision = np.count_nonzero(shifted & _TOGGLE_OUTLINES[name], axis=(-2, -1)) / np.maximum(1, areas)
        recall = np.count_nonzero(reference & expanded_shifts, axis=(-2, -1)) / _SHAPE_AREAS[name]
        coverage = 2.0 * precision * recall / np.maximum(0.001, precision + recall)
        best = max(best, float(np.max(0.94 * coverage)))
    return best


_SHAPES = {
    name: np.array([[(row >> x) & 1 for x in range(_GLYPH_SIZE)] for row in rows], dtype=np.uint8)
    for name, rows in GLYPH_ROWS.items()
}
_SHAPE_AREAS = {name: int(np.count_nonzero(shape)) for name, shape in _SHAPES.items()}
_DILATION_KERNEL = np.ones((3, 3), dtype=np.uint8)
_TOGGLE_OUTLINES = {
    name: cv2.dilate(_SHAPES[name], _DILATION_KERNEL)
    for name in ('plus', 'minus')
}


def analyze_combat_toolbar(image: np.ndarray, geometry: HUDGeometry | None = None) -> CombatObservation:
    """Recognize expanded/collapsed combat and exploration toolbar shapes.

    A collapsed combat toolbar exposes its '+' control. Exploration and
    Havre-Sac retain an eye or gear beside it; those shapes take priority.
    Unrecognized controls remain unknown. Scenery never gates classification.
    """
    geometry = geometry or HUDGeometry()
    unknown = CombatObservation(None, 0.0, 0.0, 0.0, 0.0)
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim not in (2, 3):
        return unknown
    height, width = image.shape[:2]
    if height < 20 or width < 160 or (image.ndim == 3 and image.shape[2] < 3):
        return unknown
    canonical = cv2.resize(image, (320, 40), interpolation=cv2.INTER_AREA if height >= 40 else cv2.INTER_LINEAR)
    if canonical.ndim == 3:
        canonical = canonical[:, :, :3]
    # The map can be visible beneath this translucent toolbar. Only the icon
    # silhouettes authorize a decision; the scenery is not occlusion evidence.
    observation = _classify_toolbar(canonical)
    if observation.in_combat is not None:
        return _capture_pixel_bounds(observation, width, height)
    # Measuring a 37-pixel toolbar necessarily rounds its antialiased boundary.
    # Refine only that subpixel scale uncertainty, never the shape thresholds.
    # Each hypothesis uses the same icon thresholds.
    candidates = []
    for factor in (1.01, 1.02, 1.03):
        crop_height = max(1, int(round(height / factor)))
        crop_width = max(1, int(round(width / factor)))
        refined = cv2.resize(
            image[:crop_height, :crop_width], (320, 40),
            interpolation=cv2.INTER_AREA if crop_height >= 40 else cv2.INTER_LINEAR,
        )
        if refined.ndim == 3:
            refined = refined[:, :, :3]
        candidate = _classify_toolbar(refined)
        if candidate.in_combat is not None:
            candidates.append(_capture_pixel_bounds(candidate, crop_width, crop_height))
    if not candidates or len({candidate.in_combat for candidate in candidates}) > 1:
        return unknown
    return max(candidates, key=lambda candidate: candidate.confidence)


def _capture_pixel_bounds(observation: CombatObservation, width: int, height: int) -> CombatObservation:
    """Undo only the classifier's normalization, including scale refinement."""
    if observation.icon_rect is None:
        return observation
    x, y, w, h = observation.icon_rect
    left, top = int(x * width / 320), int(y * height / 40)
    right, bottom = min(width, ceil((x + w) * width / 320)), min(height, ceil((y + h) * height / 40))
    return replace(observation, icon_rect=(left, top, right - left, bottom - top))


def _classify_toolbar(canonical: np.ndarray) -> CombatObservation:
    unknown = CombatObservation(None, 0.0, 0.0, 0.0, 0.0)
    masks = [_glyph_mask(canonical[6:31, x:x + _GLYPH_SIZE]) for x in _GLYPH_X]
    plus = _shape_score(masks[0], 'plus')
    minus = _shape_score(masks[0], 'minus')
    if max(plus, minus) < 0.72:
        return unknown

    def result(state: bool, score: float, indices: tuple[int, ...]) -> CombatObservation:
        # The diagnostic outline follows the same glyph masks that authorized
        # the decision. Include a small margin without extending over scenery.
        boxes = []
        for index in indices:
            x, y, w, h = cv2.boundingRect(masks[index])
            boxes.append((_GLYPH_X[index] + x, 6 + y, w, h))
        left = max(0, min(x for x, y, w, h in boxes) - 2)
        top = max(0, min(y for x, y, w, h in boxes) - 2)
        right = min(320, max(x + w for x, y, w, h in boxes) + 2)
        bottom = min(40, max(y + h for x, y, w, h in boxes) + 2)
        # Historical scalar fields are retained for controller/log compatibility;
        # shape similarity replaces the old luminance ratio diagnostic.
        return CombatObservation(state, min(1.0, score), score, 0.0, 0.0,
                                 (left, top, right - left, bottom - top))

    if plus > minus and plus >= 0.72:
        eye = _shape_score(masks[1], 'eye')
        gear = _shape_score(masks[1], 'havresac_gear')
        exploration = max(eye, gear)
        if exploration >= 0.78:
            return result(False, min(plus, exploration), (0, 1))
        # A partly visible exploration glyph is ambiguous, not a lone plus.
        # This guard relies on shape evidence and never inspects scene colour.
        if exploration >= 0.55:
            return unknown
        # In the collapsed combat HUD, the plus is the visible combat control.
        # Requiring empty/black pixels beside it rejects valid outdoor maps.
        return result(True, plus, (0,))

    combat_names = ('combat_eye', 'diamond', 'heart', 'fighter', 'skull')
    scores = [_shape_score(mask, name) for mask, name in zip(masks[1:], combat_names)]
    # Require every icon plus the +/- control; hiding a subset is unknown.
    if min(scores) >= 0.72 and float(np.mean(scores)) >= 0.80:
        return result(True, min(minus, float(np.mean(scores))), (0, 1, 2, 3, 4, 5))
    sword = _shape_score(masks[1], 'sword')
    dots = _shape_score(masks[2], 'dots')
    info = _shape_score(masks[3], 'info')
    # Havre-Sac inserts the information icon one slot earlier.
    havre_dots = _shape_score(masks[1], 'dots')
    havre_info = _shape_score(masks[2], 'info')
    if min(sword, dots, info) >= 0.75:
        return result(False, min(minus, sword, dots, info), (0, 1, 2, 3))
    if min(havre_dots, havre_info) >= 0.78:
        return result(False, min(minus, havre_dots, havre_info), (0, 1, 2))
    return unknown


class CombatStateTracker:
    """Temporal confirmation for combat transitions.

    A transition is published only after several consecutive reliable frames.
    Unknown/low-confidence frames cannot flip the current stable state.
    """

    def __init__(
        self,
        *,
        required_confirmations: int = 3,
        initial_state: bool = False,
        min_confidence: float = 0.60,
    ) -> None:
        self.required_confirmations = max(1, int(required_confirmations))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.in_combat = bool(initial_state)
        self._candidate: bool | None = None
        self._candidate_count = 0

    def update(self, observation: CombatObservation) -> bool | None:
        observed = observation.in_combat
        if observed is None or observation.confidence < self.min_confidence:
            self._candidate = None
            self._candidate_count = 0
            return None

        if observed == self.in_combat:
            self._candidate = None
            self._candidate_count = 0
            return None

        if self._candidate == observed:
            self._candidate_count += 1
        else:
            self._candidate = observed
            self._candidate_count = 1

        if self._candidate_count < self.required_confirmations:
            return None

        self.in_combat = bool(observed)
        self._candidate = None
        self._candidate_count = 0
        return self.in_combat
