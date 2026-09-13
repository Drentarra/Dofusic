from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry


@dataclass(frozen=True, slots=True)
class CombatObservation:
    """One visual observation of Dofus' top-left action toolbar.

    ``in_combat`` is ``None`` when the toolbar is not reliable enough to make a
    decision. ``luminance_ratio`` is the first-button icon peak divided by the
    brightest stable neighbouring icon peak; this makes the classifier largely
    independent from the active Dofus colour theme.
    """

    in_combat: bool | None
    confidence: float
    luminance_ratio: float
    first_peak: float
    reference_peak: float


def _button_luminance(gray: np.ndarray, rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, width, height = rect
    patch = gray[y:y + height, x:x + width]
    if patch.size == 0:
        return 0.0, 0.0
    background = float(np.percentile(patch, 50.0))
    peak = float(np.percentile(patch, 99.0))
    return peak, max(0.0, peak - background)


def analyze_combat_toolbar(image: np.ndarray, geometry: HUDGeometry | None = None) -> CombatObservation:
    """Classify combat from the top-left Dofus toolbar without theme colours.

    Outside combat the first toolbar icon (sword, or the replacement first icon
    in screens such as Havre-Sac) has a luminance peak comparable to the stable
    information/utility icons beside it. During combat Dofus disables the sword
    and its glyph becomes markedly dimmer while the neighbouring icons keep their
    normal luminance. The ratio survives the supplied green and purple themes and
    ordinary client scaling, unlike fixed RGB/template matching.
    """

    geometry = geometry or HUDGeometry()
    if image is None or getattr(image, 'size', 0) == 0:
        return CombatObservation(None, 0.0, 0.0, 0.0, 0.0)

    h, w = image.shape[:2]
    if h < 8 or w < 32:
        return CombatObservation(None, 0.0, 0.0, 0.0, 0.0)

    canonical = cv2.resize(
        image,
        (geometry.combat_width, geometry.combat_height),
        interpolation=(
            cv2.INTER_AREA
            if w >= geometry.combat_width and h >= geometry.combat_height
            else cv2.INTER_LINEAR
        ),
    )
    if canonical.ndim == 2:
        gray = canonical.astype(np.uint8, copy=False)
    else:
        gray = cv2.cvtColor(canonical, cv2.COLOR_BGR2GRAY)

    luminance = tuple(_button_luminance(gray, rect) for rect in geometry.combat_button_rects)
    peaks = tuple(item[0] for item in luminance)
    contrasts = tuple(item[1] for item in luminance)
    first_peak = peaks[0]
    reference_peak = max(peaks[1:], default=0.0)
    first_contrast = contrasts[0]
    reference_contrast = max(contrasts[1:], default=0.0)

    # A contrast gate avoids interpreting an absent/covered toolbar as combat.
    # It is intentionally far below the supplied themes' ~130-point active-icon
    # contrast, so theme brightness itself is not the classifier.
    if reference_contrast < 24.0:
        return CombatObservation(None, 0.0, 0.0, first_peak, reference_peak)

    ratio = max(0.0, min(2.0, first_contrast / reference_contrast))
    if ratio <= 0.68:
        confidence = max(0.0, min(1.0, (0.76 - ratio) / 0.28))
        return CombatObservation(True, confidence, ratio, first_peak, reference_peak)
    if ratio >= 0.82:
        confidence = max(0.0, min(1.0, (ratio - 0.72) / 0.28))
        return CombatObservation(False, confidence, ratio, first_peak, reference_peak)
    return CombatObservation(None, 0.0, ratio, first_peak, reference_peak)


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
