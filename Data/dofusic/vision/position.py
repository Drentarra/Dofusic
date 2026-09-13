from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from dofusic.models import Coordinates


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
    items = tuple(candidates)
    if not items:
        return None

    groups: dict[Coordinates, list[PositionCandidate]] = defaultdict(list)
    for item in items:
        groups[item.coordinates].append(item)

    ranked = sorted(
        groups.items(),
        key=lambda pair: (
            len(pair[1]),
            sum(max(0.0, min(1.0, c.confidence)) for c in pair[1]) / len(pair[1]),
            max(c.confidence for c in pair[1]),
        ),
        reverse=True,
    )
    _coords, group = ranked[0]
    return max(group, key=lambda c: c.confidence)
