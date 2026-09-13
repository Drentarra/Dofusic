from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class LocationKind(str, Enum):
    AREA = 'area'
    SUBAREA = 'subarea'


class MatchMode(str, Enum):
    EXACT = 'exact'
    CONTAINED = 'contained'
    FUZZY = 'fuzzy'


@dataclass(frozen=True, slots=True)
class Coordinates:
    x: int
    y: int




@dataclass(frozen=True, slots=True)
class ZoneOCRResult:
    text: str
    confidence: float
    elapsed_ms: float
    engine: str
    error: str = ''


@dataclass(frozen=True, slots=True)
class PositionOCRResult:
    raw_text: str
    x: Optional[int]
    y: Optional[int]
    confidence: float
    elapsed_ms: float
    engine: str
    variant: str = ''
    error: str = ''

    @property
    def coordinates(self) -> Optional[Coordinates]:
        if self.x is None or self.y is None:
            return None
        return Coordinates(int(self.x), int(self.y))


@dataclass(frozen=True, slots=True)
class LocationRecord:
    id: int
    name: str
    kind: LocationKind
    parent_area_id: Optional[int] = None
    parent_area_name: Optional[str] = None

    @property
    def canonical_key(self) -> str:
        return f'{self.kind.value}:{self.id}'


@dataclass(frozen=True, slots=True)
class LocationMatch:
    location: LocationRecord
    score: float
    origin: str
    matched_alias: str
    exact: bool = False
    mode: MatchMode = MatchMode.FUZZY
    context_bonus: float = 0.0
    coordinate_bonus: float = 0.0

    @property
    def rank_score(self) -> float:
        return float(self.score + self.context_bonus + self.coordinate_bonus)


@dataclass(frozen=True, slots=True)
class LocationEvidence:
    raw_zone_text: str
    ocr_confidence: float
    top1: Optional[LocationMatch]
    top2: Optional[LocationMatch]
    margin: float
    coordinates: Optional[Coordinates]
    coordinate_consistency: float
    timestamp: float


class DecisionState(str, Enum):
    CONFIRMED = 'confirmed'
    PENDING = 'pending'
    AMBIGUOUS = 'ambiguous'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class DecisionResult:
    state: DecisionState
    location: Optional[LocationRecord]
    evidence: Optional[LocationEvidence]
    reason: str
