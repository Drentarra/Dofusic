from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from dofusic.models import Coordinates
from dofusic.numeric import clamp_confidence


@dataclass(frozen=True, slots=True)
class PositionCandidate:
    raw_text: str
    coordinates: Coordinates
    confidence: float
    variant: str


def select_position_candidate(candidates: tuple[PositionCandidate, ...] | list[PositionCandidate]) -> PositionCandidate | None:
    """Choose coordinates by cross-variant consensus, then confidence.

    A single high-confidence hallucination must not beat two independent OCR
    variants that agree on the same coordinate pair. With no consensus, keep a
    strong single reading rather than inventing a coordinate.
    """
    valid = []
    for candidate in candidates:
        try:
            finite = math.isfinite(float(candidate.confidence))
        except (TypeError, ValueError, OverflowError):
            finite = False
        if finite:
            valid.append(candidate)
    items = tuple(valid)
    if not items:
        return None

    groups: dict[Coordinates, dict[str, PositionCandidate]] = defaultdict(dict)
    for item in items:
        variants = groups[item.coordinates]
        previous = variants.get(item.variant)
        if previous is None or clamp_confidence(item.confidence) > clamp_confidence(previous.confidence):
            variants[item.variant] = item

    ranked = sorted(
        groups.items(),
        key=lambda pair: (
            len(pair[1]),
            sum(clamp_confidence(c.confidence) for c in pair[1].values()) / len(pair[1]),
            max(clamp_confidence(c.confidence) for c in pair[1].values()),
        ),
        reverse=True,
    )
    _coords, group = ranked[0]
    return max(group.values(), key=lambda c: clamp_confidence(c.confidence))
