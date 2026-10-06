from __future__ import annotations

from dataclasses import dataclass

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
    reference = _SHAPES[name]
    # A symmetric overlap prevents a partial icon or arbitrary solid window
    # from matching just a small fragment of a known glyph.
    padded = np.pad(mask, 1)
    best = 0.0
    for dy in range(3):
        for dx in range(3):
            shifted = padded[dy:dy + _GLYPH_SIZE, dx:dx + _GLYPH_SIZE]
            intersection = float(np.count_nonzero(shifted & reference))
            denominator = int(shifted.sum()) + int(reference.sum())
            best = max(best, 2.0 * intersection / max(1, denominator))
    return best


_SHAPES = {
    name: np.array([[(row >> x) & 1 for x in range(_GLYPH_SIZE)] for row in rows], dtype=np.uint8)
    for name, rows in GLYPH_ROWS.items()
}


def analyze_combat_toolbar(image: np.ndarray, geometry: HUDGeometry | None = None) -> CombatObservation:
    """Recognize expanded/collapsed combat and exploration toolbar shapes.

    Ambiguous or occluded regions never become a negative combat observation.
    In particular, a '+' only means combat when the *whole* inspected region
    beyond it is the empty HUD surround, not when a neighbouring icon failed
    to match. Havre-Sac and exploration have their own positive shape evidence.
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
    # The unobstructed HUD has a quiet dark gap beneath the toolbar. A panel,
    # tooltip or arbitrary scene covering this gap cannot authorize a decision.
    separator = canonical[38:40]
    if float(np.percentile(separator, 95)) > 18:
        return unknown
    observation = _classify_toolbar(canonical)
    if observation.in_combat is not None:
        return observation
    # Measuring a 37-pixel toolbar necessarily rounds its antialiased boundary.
    # Refine only that subpixel scale uncertainty, never the shape thresholds.
    # Each hypothesis still requires the same complete icon/empty-region proof.
    candidates = []
    allow_collapsed_combat = _empty_hud_surround(canonical)
    for factor in (1.01, 1.02, 1.03):
        crop_height = max(1, int(round(height / factor)))
        crop_width = max(1, int(round(width / factor)))
        refined = cv2.resize(
            image[:crop_height, :crop_width], (320, 40),
            interpolation=cv2.INTER_AREA if crop_height >= 40 else cv2.INTER_LINEAR,
        )
        if refined.ndim == 3:
            refined = refined[:, :, :3]
        candidate = _classify_toolbar(refined, allow_collapsed_combat=allow_collapsed_combat)
        if candidate.in_combat is not None:
            candidates.append(candidate)
    if not candidates or len({candidate.in_combat for candidate in candidates}) > 1:
        return unknown
    return max(candidates, key=lambda candidate: candidate.confidence)


def _empty_hud_surround(canonical: np.ndarray) -> bool:
    # Skip the collapsed button's rounded/antialiased outer edge. Count pixels,
    # not colour channels, allowing only isolated rendering specks.
    surround = canonical[1:37, 43:]
    pixels = surround.max(axis=2) if surround.ndim == 3 else surround
    return float(np.percentile(pixels, 99.8)) <= 8 and int(np.count_nonzero(pixels > 12)) <= 3


def _classify_toolbar(canonical: np.ndarray, *, allow_collapsed_combat: bool = True) -> CombatObservation:
    unknown = CombatObservation(None, 0.0, 0.0, 0.0, 0.0)
    if float(np.percentile(canonical[38:40], 95)) > 18:
        return unknown
    masks = [_glyph_mask(canonical[6:31, x:x + _GLYPH_SIZE]) for x in _GLYPH_X]
    plus = _shape_score(masks[0], 'plus')
    minus = _shape_score(masks[0], 'minus')
    if max(plus, minus) < 0.72:
        return unknown

    def result(state: bool, score: float) -> CombatObservation:
        # Historical scalar fields are retained for controller/log compatibility;
        # shape similarity replaces the old luminance ratio diagnostic.
        return CombatObservation(state, min(1.0, score), score, 0.0, 0.0)

    if plus > minus and plus >= 0.72:
        eye = _shape_score(masks[1], 'eye')
        gear = _shape_score(masks[1], 'havresac_gear')
        if max(eye, gear) >= 0.78:
            return result(False, min(plus, max(eye, gear)))
        # Full right-hand area must match the empty surround. Do not infer
        # combat merely from absent/unrecognizable neighbouring icons.
        # Scale refinement cannot crop away an obstruction in the original ROI.
        if allow_collapsed_combat and _empty_hud_surround(canonical):
            return result(True, plus)
        return unknown

    combat_names = ('combat_eye', 'diamond', 'heart', 'fighter', 'skull')
    scores = [_shape_score(mask, name) for mask, name in zip(masks[1:], combat_names)]
    # Require every icon plus the +/- control; hiding a subset is unknown.
    if min(scores) >= 0.72 and float(np.mean(scores)) >= 0.80:
        return result(True, min(minus, float(np.mean(scores))))
    sword = _shape_score(masks[1], 'sword')
    dots = _shape_score(masks[2], 'dots')
    info = _shape_score(masks[3], 'info')
    # Havre-Sac inserts the information icon one slot earlier.
    havre_dots = _shape_score(masks[1], 'dots')
    havre_info = _shape_score(masks[2], 'info')
    if min(sword, dots, info) >= 0.75:
        return result(False, min(minus, sword, dots, info))
    if min(havre_dots, havre_info) >= 0.78:
        return result(False, min(minus, havre_dots, havre_info))
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
