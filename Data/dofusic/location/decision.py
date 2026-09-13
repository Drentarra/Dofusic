from __future__ import annotations

from collections import deque

from rapidfuzz import fuzz

from dofusic.models import DecisionResult, DecisionState, LocationEvidence, MatchMode
from dofusic.text import normalize_text


class DecisionEngine:
    """Confirmation temporelle. Ne connaît ni fichiers MP3 ni lecteur audio."""

    def __init__(
        self,
        *,
        min_confidence: float = 0.55,
        min_fuzzy_score: float = 86.0,
        min_margin: float = 8.0,
        exact_single_confidence: float = 0.92,
        contained_single_confidence: float = 0.94,
        required_confirmations: int = 2,
        text_stability_min: float = 84.0,
        confirmation_max_gap_sec: float = 2.0,
        history_size: int = 6,
    ) -> None:
        self.min_confidence = float(min_confidence)
        self.min_fuzzy_score = float(min_fuzzy_score)
        self.min_margin = float(min_margin)
        self.exact_single_confidence = float(exact_single_confidence)
        self.contained_single_confidence = float(contained_single_confidence)
        self.required_confirmations = max(2, int(required_confirmations))
        self.text_stability_min = float(text_stability_min)
        self.confirmation_max_gap_sec = max(0.1, float(confirmation_max_gap_sec))
        self.history: deque[LocationEvidence] = deque(maxlen=max(3, int(history_size)))
        self.confirmed_key = ''

    def _same_recent(self, evidence: LocationEvidence) -> tuple[int, int]:
        if evidence.top1 is None:
            return 0, 0
        key = evidence.top1.location.canonical_key
        current = normalize_text(evidence.raw_zone_text)
        same = 0
        close = 0
        for old in reversed(self.history):
            # Confirmation is temporal continuity, not a vote over arbitrary old
            # observations. Any different/unknown location breaks the streak.
            if old.top1 is None or old.top1.location.canonical_key != key:
                break
            if evidence.timestamp - old.timestamp > self.confirmation_max_gap_sec:
                break
            same += 1
            previous = normalize_text(old.raw_zone_text)
            if previous and current and (previous == current or fuzz.WRatio(previous, current) >= self.text_stability_min):
                close += 1
        return same, close

    def evaluate(self, evidence: LocationEvidence) -> DecisionResult:
        top1 = evidence.top1
        if top1 is None:
            self.history.append(evidence)
            return DecisionResult(DecisionState.UNKNOWN, None, evidence, 'aucun candidat de lieu')

        if evidence.ocr_confidence < self.min_confidence:
            self.history.append(evidence)
            return DecisionResult(DecisionState.UNKNOWN, None, evidence, 'confiance OCR trop basse')

        if top1.mode is MatchMode.FUZZY and top1.score < self.min_fuzzy_score:
            self.history.append(evidence)
            return DecisionResult(DecisionState.UNKNOWN, None, evidence, 'score texte trop bas')

        # La marge s'applique aussi aux exacts : deux lieux homonymes exacts doivent
        # rester ambigus tant que le parent/coordonnée ne les départage pas.
        if evidence.top2 is not None and evidence.margin < self.min_margin:
            self.history.append(evidence)
            return DecisionResult(
                DecisionState.AMBIGUOUS,
                top1.location,
                evidence,
                f'marge Top1/Top2 insuffisante ({evidence.margin:.1f})',
            )

        same_before, close_before = self._same_recent(evidence)
        self.history.append(evidence)
        same = same_before + 1
        close = close_before + 1

        if top1.mode is MatchMode.EXACT and evidence.ocr_confidence >= self.exact_single_confidence:
            self.confirmed_key = top1.location.canonical_key
            return DecisionResult(DecisionState.CONFIRMED, top1.location, evidence, 'exact haute confiance')

        if top1.mode is MatchMode.CONTAINED and evidence.ocr_confidence >= self.contained_single_confidence and top1.score >= 98.0:
            self.confirmed_key = top1.location.canonical_key
            return DecisionResult(DecisionState.CONFIRMED, top1.location, evidence, 'libellé canonique contenu dans le HUD')

        if same >= self.required_confirmations and close >= self.required_confirmations:
            self.confirmed_key = top1.location.canonical_key
            return DecisionResult(DecisionState.CONFIRMED, top1.location, evidence, 'confirmations cohérentes')

        return DecisionResult(
            DecisionState.PENDING,
            top1.location,
            evidence,
            f'confirmation {same}/{self.required_confirmations}, texte {close}/{self.required_confirmations}',
        )
