from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dofusic.vision.combat_shapes import GLYPH_ROWS
from dofusic.vision.layout import HUDGeometry, toolbar_material_columns


@dataclass(frozen=True, slots=True)
class CombatObservation:
    """One visual observation of Dofus' top-left action toolbar.

    ``in_combat`` is ``None`` when the toolbar is not reliable enough to make a
    decision. Confidence describes silhouette similarity, independently of colours.
    """

    in_combat: bool | None
    confidence: float
    # 1 = '+' seul, 2 = '+' et second bouton, 6 = volet déroulé.
    toolbar_buttons: int | None = None
    # Physical number of controls can be known even if the combat mode is not.
    # Zero means there is no independent proof.
    layout_confidence: float = 0.0
    configuration: str | None = None
    # "absence" indicates a state inferred from a missing neighbouring control.
    # This is weaker than affirmative ordered-icon evidence, even when "+" matches perfectly.
    evidence_type: str = "direct"


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
    # Exactly the two order statistics used by percentile(..., 98) for
    # 25x25 glyphs (index 611.52). A partial selection avoids sorting and
    # NumPy's general-purpose percentile machinery on every captured frame.
    ordered = np.partition(contrast.reshape(-1), (611, 612))
    peak = float(ordered[611]) + 0.52 * (float(ordered[612]) - float(ordered[611]))
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
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    # The 3x3 translation hypotheses are evaluated together inside OpenCV,
    # rather than dispatching 9 separate NumPy reductions per reference icon.
    # All masks are binary uint8, so CCORR is their exact intersection count.
    intersection = cv2.matchTemplate(padded, reference, cv2.TM_CCORR)
    area = cv2.boxFilter(
        padded, cv2.CV_32S, (_GLYPH_SIZE, _GLYPH_SIZE),
        anchor=(0, 0), normalize=False, borderType=cv2.BORDER_CONSTANT,
    )[:3, :3]
    total = int(reference.sum())
    best = float(np.max(2.0 * intersection / np.maximum(1, area + total)))
    if name in _TOGGLE_OUTLINES:
        # Keep the historical thin-stroke coverage rule byte-for-byte. Only
        # the ordinary overlap stage above has a faster implementation.
        outline = _TOGGLE_OUTLINES[name]
        for dy in range(3):
            for dx in range(3):
                shifted = padded[dy:dy + _GLYPH_SIZE, dx:dx + _GLYPH_SIZE]
                expanded = cv2.dilate(shifted, _DILATION_KERNEL)
                precision = float(np.count_nonzero(shifted & outline)) / max(1, int(area[dy, dx]))
                recall = float(np.count_nonzero(reference & expanded)) / max(1, total)
                coverage = 2.0 * precision * recall / max(0.001, precision + recall)
                best = max(best, 0.94 * coverage)
    return best


_DILATION_KERNEL = np.ones((3, 3), dtype=np.uint8)

_SHAPES = {
    name: np.array([[(row >> x) & 1 for x in range(_GLYPH_SIZE)] for row in rows], dtype=np.uint8)
    for name, rows in GLYPH_ROWS.items()
}
_TOGGLE_OUTLINES = {
    name: cv2.dilate(_SHAPES[name], _DILATION_KERNEL)
    for name in ('plus', 'minus')
}


@dataclass(frozen=True, slots=True)
class _ToolbarHypothesis:
    """Four mutually exclusive visual configurations, scored on one scale."""

    name: str
    combat: bool
    buttons: int
    score: float


# Scores are evidence of an observed silhouette (Dice overlap), not a guessed
# screen-resolution profile. The common acceptance/margin rules apply to all
# configurations; only the physical glyphs present in each configuration vary.
_TOGGLE_EVIDENCE = 0.72
_ICON_MARGIN = 0.07


def _empty_hud_surround(coverage: np.ndarray) -> bool:
    """The second slot is absent, without inspecting distant map scenery."""
    return coverage.size >= 72 and float(coverage[43:69].mean()) < .30


def _physical_layout(
    coverage: np.ndarray, plus: float, minus: float, second_present: bool,
    folded_neighbour: float,
) -> tuple[int | None, float]:
    """Infer a button count without needing a reliable combat classification.

    Multiple spatially separated button-material patches, not a single icon or
    a colour elsewhere in the map, provide evidence of an unfolded bar. A plus
    followed by the neighbouring button material signals two folded controls.
    """
    if max(plus, minus) < _TOGGLE_EVIDENCE or coverage.size < 205:
        return None, 0.0
    # A positively identified '+' and exploration neighbour take priority
    # over panel material: Havre-Sac has a long *folded* decorative rim.
    if plus - minus >= .08 and folded_neighbour >= .70 and second_present:
        return 2, min(.95, max(plus, folded_neighbour))
    slots = (coverage[43:69], coverage[76:100], coverage[110:135],
             coverage[144:169], coverage[178:204])
    present = [float(slot.mean()) >= .34 for slot in slots]
    # A long toolbar must contain at least three separately identifiable slots,
    # including one after the third slot. A border/tooltip alone is insufficient.
    if present[0] and sum(present) >= 3 and any(present[2:]):
        # Material alone is only a *candidate*. Long decorative panels and
        # scenery can look identical; independent icon evidence is needed
        # before publishing six controls to the overlay.
        return 6, 0.0
    if plus >= _TOGGLE_EVIDENCE and plus >= minus - .03:
        if second_present:
            return 2, min(.90, max(.64, plus))
        if _empty_hud_surround(coverage):
            return 1, min(.90, max(.64, plus))
    return None, 0.0


def _classify_toolbar(
    canonical: np.ndarray, *, allow_collapsed_combat: bool = True,
) -> CombatObservation:
    """Rank four hypotheses using ordered icons, plus separate layout evidence.

    A missing glyph is never evidence of exploration. The collapsed combat
    result is inferential and explicitly carries weaker evidence. Expanded states must
    exhibit an affirmative ordered *signature*. Folded combat requires an
    identifiable plus and the verified absence of its immediate neighbour.
    """
    unknown = CombatObservation(None, 0.)
    first = _glyph_mask(canonical[6:31, _GLYPH_X[0]:_GLYPH_X[0] + _GLYPH_SIZE])
    plus, minus = _shape_score(first, 'plus'), _shape_score(first, 'minus')
    if max(plus, minus) < _TOGGLE_EVIDENCE:
        return unknown

    coverage = toolbar_material_columns(canonical)
    if coverage.size < 205 or float(coverage[5:32].mean()) < .45:
        return unknown

    second = _glyph_mask(canonical[6:31, _GLYPH_X[1]:_GLYPH_X[1] + _GLYPH_SIZE])
    eye = _shape_score(second, 'eye')
    gear = _shape_score(second, 'havresac_gear')
    second_present = float(coverage[43:69].mean()) >= .34
    buttons, layout_confidence = _physical_layout(coverage, plus, minus, second_present, max(eye, gear))
    hypotheses: list[_ToolbarHypothesis] = []

    # Collapsed states: one plus and a positive neighbour/empty-slot proof.
    if plus >= _TOGGLE_EVIDENCE:
        neighbour = max(eye, gear)
        if neighbour >= .70 and second_present and neighbour - _shape_score(second, 'combat_eye') >= .12:
            hypotheses.append(_ToolbarHypothesis('exploration_collapsed', False, 2,
                                                  .35 * plus + .65 * neighbour))
        if allow_collapsed_combat and _empty_hud_surround(coverage):
            # The plus sign is common to both folded modes. Its similarity
            # alone cannot constitute 100% evidence of combat: the *missing*
            # neighbour is negative/inferential evidence, easily occluded by
            # tooltips, animated controls and other overlays.
            hypotheses.append(_ToolbarHypothesis('combat_collapsed', True, 1, min(.82, plus)))

    # Material tells us whether the later glyph positions might be real icons.
    # In the common folded case none of these silhouettes needs extracting.
    if buttons == 6 or (minus >= _TOGGLE_EVIDENCE and minus > plus + .03):
        masks = [first, second]
        masks.extend(_glyph_mask(canonical[6:31, x:x + _GLYPH_SIZE]) for x in _GLYPH_X[2:])
        score_cache: dict[tuple[int, str], float] = {}

        def glyph(slot: int, name: str) -> float:
            key = (slot, name)
            if key not in score_cache:
                score_cache[key] = _shape_score(masks[slot], name)
            return score_cache[key]

        toggle = max(plus, minus)
        # Exploration: distinguish the sword from the combat eye, and require
        # the *ordered* sword/dots/info signature. A small +/- ambiguity is OK.
        sword, dots, info = glyph(1, 'sword'), glyph(2, 'dots'), glyph(3, 'info')
        if (sword >= .80 and dots >= .58 and info >= .68
                and sword - glyph(1, 'combat_eye') >= .20):
            hypotheses.append(_ToolbarHypothesis('exploration_expanded', False, 6,
                                                  .18 * toggle + .82 * (sword + dots + info) / 3))

        # Havre-Sac uses a different *ordered* exploration signature. It is
        # still the same expanded-exploration hypothesis, not a fifth state.
        havre_dots, havre_info = glyph(1, 'dots'), glyph(2, 'info')
        if (minus >= .74 and minus > plus and havre_dots >= .72 and havre_info >= .72):
            hypotheses.append(_ToolbarHypothesis('exploration_expanded', False, 6,
                                                  .18 * minus + .82 * (havre_dots + havre_info) / 2))

        combat = [glyph(slot, name) for slot, name in enumerate(
            ('combat_eye', 'diamond', 'heart', 'fighter', 'skull'), start=1)]
        # A complete signature is essential; one vanished icon cannot cause a
        # false combat confirmation. Thin glyphs legitimately score lower.
        if (minus >= .72 and all(actual >= minimum for actual, minimum in zip(
                combat, (.60, .58, .76, .51, .56))) and sum(combat) / 5 >= .64):
            hypotheses.append(_ToolbarHypothesis('combat_expanded', True, 6,
                                                  .18 * minus + .82 * sum(combat) / 5))

        # Geometry is independent of *which* of those ordered signatures won.
        # Three distinct slots with recognizable silhouettes are sufficient to
        # confirm six physical buttons when one icon is covered or unreadable.
        slot_proofs = (
            max(sword, combat[0]) >= .60,
            max(dots, combat[1]) >= .57,
            max(info, combat[2]) >= .70,
            combat[3] >= .50,
            combat[4] >= .55,
        )
        if buttons == 6 and sum(slot_proofs) >= 3:
            layout_confidence = max(layout_confidence, .75)

    if not hypotheses:
        return CombatObservation(None, 0., buttons, layout_confidence)

    # Two alternative signatures of the SAME state are not competitors.
    by_state: dict[str, _ToolbarHypothesis] = {}
    for candidate in hypotheses:
        if (previous := by_state.get(candidate.name)) is None or candidate.score > previous.score:
            by_state[candidate.name] = candidate
    ranked = sorted(by_state.values(), key=lambda hypothesis: hypothesis.score, reverse=True)
    winner = ranked[0]
    if len(ranked) > 1 and winner.score - ranked[1].score < _ICON_MARGIN:
        return CombatObservation(None, 0., buttons, layout_confidence)
    # A strong ordered signature can establish six controls even when the
    # physical-material estimate was uncertain. Conversely, weak icon evidence
    # cannot override a confidently established physical button count.
    if buttons is not None and buttons != winner.buttons and layout_confidence >= .70:
        return CombatObservation(None, 0., buttons, layout_confidence)
    return CombatObservation(winner.combat, winner.score, winner.buttons, max(layout_confidence, winner.score), winner.name,
                             'absence' if winner.name == 'combat_collapsed' else 'direct')


def analyze_combat_toolbar(image: np.ndarray, geometry: HUDGeometry | None = None) -> CombatObservation:
    """Observe the four HUD states without modifying temporal/music decisions.

    Only HUD-scale rounding is reconsidered on ambiguous observations. This
    operates on a tiny fixed crop, never a resolution-wide continuous search.
    """
    unknown = CombatObservation(None, 0.)
    if image is None or getattr(image, 'size', 0) == 0 or image.ndim not in (2, 3):
        return unknown
    height, width = image.shape[:2]
    if height < 14 or width < 90 or (image.ndim == 3 and image.shape[2] < 3):
        return unknown
    canonical = cv2.resize(image, (320, 40), interpolation=cv2.INTER_AREA if height >= 40 else cv2.INTER_LINEAR)
    if canonical.ndim == 3:
        canonical = canonical[:, :, :3]
    observed = _classify_toolbar(canonical)
    if observed.in_combat is not None:
        return observed

    # One scale's uncertain layout remains useful even if all state hypotheses
    # fail. Preserve it unless a corrected sample supplies stronger evidence.
    candidates: list[CombatObservation] = []
    allow_collapsed_combat = _empty_hud_surround(toolbar_material_columns(canonical))
    for factor in (1.01, 1.02, 1.03):
        crop_h = max(1, round(height / factor))
        crop_w = max(1, round(width / factor))
        refined = cv2.resize(image[:crop_h, :crop_w], (320, 40),
                             interpolation=cv2.INTER_AREA if crop_h >= 40 else cv2.INTER_LINEAR)
        if refined.ndim == 3:
            refined = refined[:, :, :3]
        candidate = _classify_toolbar(refined, allow_collapsed_combat=allow_collapsed_combat)
        if candidate.in_combat is not None:
            candidates.append(candidate)
        if candidate.layout_confidence > observed.layout_confidence:
            observed = CombatObservation(None, 0., candidate.toolbar_buttons, candidate.layout_confidence)
    if not candidates or len({candidate.in_combat for candidate in candidates}) > 1:
        return observed
    return max(candidates, key=lambda candidate: candidate.confidence)


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
        min_inferred_transition_seconds: float = 0.0,
    ) -> None:
        self.required_confirmations = max(1, int(required_confirmations))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_inferred_transition_seconds = max(0.0, float(min_inferred_transition_seconds))
        self.in_combat = bool(initial_state)
        # Independent physical-layout confidence can update the overlay even
        # when combat/exploration is unknown. It cannot change music state.
        self.toolbar_buttons: int | None = None
        self._candidate: bool | None = None
        self._candidate_count = 0
        self._candidate_first_at: float | None = None
        self._candidate_evidence_type: str | None = None

    def reset(self) -> None:
        """Réinitialiser un cycle de détection, sans recréer le tracker."""
        self.in_combat = False
        self.toolbar_buttons = None
        self._candidate = None
        self._candidate_count = 0
        self._candidate_first_at = None
        self._candidate_evidence_type = None

    def update(
        self, observation: CombatObservation, *, enabled: bool = True,
        disturbed: bool = False, now: float | None = None,
    ) -> bool | None:
        """Publish the physical layout independently of combat confirmation.

        While in a dungeon, the controls can still be drawn correctly, but
        combat must not override the dungeon soundtrack. Unknown-mode readings
        may update the layout *only with independent positive visual evidence*;
        they never update the combat state and interrupt pending transitions.
        """
        if disturbed:
            # Input events change HUD pixels but not combat state. Do not
            # publish either audio transitions or apparent button-count losses.
            self._candidate = None
            self._candidate_count = 0
            self._candidate_first_at = None
            self._candidate_evidence_type = None
            return None
        observed = observation.in_combat
        reliable = observed is not None and observation.confidence >= self.min_confidence
        layout_reliable = (
            reliable or observation.layout_confidence >= self.min_confidence
        )
        # When the weaker 'one button because the neighbour disappeared'
        # evidence requests an opposite state, keep the last trusted width
        # until the same transition passes its longer temporal validation.
        inferred_opposite = (observation.evidence_type == 'absence'
                             and observed is not None and observed != self.in_combat)
        if layout_reliable and observation.toolbar_buttons in (1, 2, 6) and not inferred_opposite:
            self.toolbar_buttons = observation.toolbar_buttons
        if not enabled:
            self._candidate = None
            self._candidate_count = 0
            self._candidate_first_at = None
            self._candidate_evidence_type = None
            return None
        if observed is None or observation.confidence < self.min_confidence:
            self._candidate = None
            self._candidate_count = 0
            self._candidate_first_at = None
            self._candidate_evidence_type = None
            return None

        if observed == self.in_combat:
            self._candidate = None
            self._candidate_count = 0
            self._candidate_first_at = None
            self._candidate_evidence_type = None
            return None

        if self._candidate == observed and self._candidate_evidence_type == observation.evidence_type:
            self._candidate_count += 1
        else:
            self._candidate = observed
            self._candidate_count = 1
            self._candidate_first_at = now
            self._candidate_evidence_type = observation.evidence_type

        if self._candidate_count < self.required_confirmations:
            return None
        if (observation.evidence_type == 'absence'
                and self.min_inferred_transition_seconds > 0.0
                and now is not None and self._candidate_first_at is not None
                and now - self._candidate_first_at < self.min_inferred_transition_seconds):
            return None

        self.in_combat = bool(observed)
        if observation.toolbar_buttons in (1, 2, 6):
            self.toolbar_buttons = observation.toolbar_buttons
        self._candidate = None
        self._candidate_count = 0
        self._candidate_first_at = None
        self._candidate_evidence_type = None
        return self.in_combat
