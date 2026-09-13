from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, replace

from dofusic.location.repository import DofusRepository, PlaceAliasRecord
from dofusic.models import Coordinates, LocationEvidence, LocationKind, LocationMatch, LocationRecord, MatchMode
from dofusic.text import (
    TextMatchMode,
    clean_text,
    content_words,
    important_words,
    match_key,
    norm_key,
    normalize_text,
    text_similarity,
    unique_texts,
)


@dataclass(frozen=True, slots=True)
class _CoordinateContext:
    exact_subarea_ids: frozenset[int] = frozenset()
    exact_area_ids: frozenset[int] = frozenset()
    nearby_subarea_ids: frozenset[int] = frozenset()
    nearby_area_ids: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class PreparedLocation:
    location: LocationRecord
    normalized_name: str
    match_key: str
    compact_key: str
    content_words: tuple[str, ...]
    important_words: tuple[str, ...]
    candidate_words: tuple[str, ...]
    parent_normalized_name: str
    parent_compact_key: str
    parent_content_words: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedAlias:
    record: PlaceAliasRecord
    normalized_alias: str
    match_key: str
    content_words: tuple[str, ...]
    important_words: tuple[str, ...]


class LocationResolver:
    """Resolve OCR text to canonical Dofus geography with immutable prepared data."""

    def __init__(self, repository: DofusRepository) -> None:
        self.repository = repository
        self.locations = repository.all_locations()
        self.aliases = repository.place_aliases()
        self.prepared_locations = tuple(self._prepare_location(item) for item in self.locations)
        self.prepared_aliases = tuple(self._prepare_alias(item) for item in self.aliases)

        exact: dict[tuple[str, ...], list[PreparedLocation]] = defaultdict(list)
        tokens: dict[str, list[PreparedLocation]] = defaultdict(list)
        structured: dict[tuple[tuple[str, ...], tuple[str, ...]], list[PreparedLocation]] = defaultdict(list)
        structured_compact: dict[tuple[str, str], list[PreparedLocation]] = defaultdict(list)
        by_key: dict[str, PreparedLocation] = {}
        for prepared in self.prepared_locations:
            exact[prepared.content_words].append(prepared)
            by_key[prepared.location.canonical_key] = prepared
            for word in prepared.candidate_words:
                tokens[word].append(prepared)
            if prepared.location.kind is LocationKind.SUBAREA and prepared.parent_content_words:
                structured[(prepared.parent_content_words, prepared.content_words)].append(prepared)
                if prepared.parent_compact_key and prepared.compact_key:
                    structured_compact[(prepared.parent_compact_key, prepared.compact_key)].append(prepared)

        self.exact_index = {key: tuple(value) for key, value in exact.items() if key}
        self.token_index = {key: tuple(value) for key, value in tokens.items()}
        self.structured_index = {key: tuple(value) for key, value in structured.items()}
        self.structured_compact_index = {
            key: value[0]
            for key, value in structured_compact.items()
            if len(value) == 1
        }
        self._prepared_by_key = by_key

    @staticmethod
    def _prepare_location(location: LocationRecord) -> PreparedLocation:
        parent = location.parent_area_name or ''
        return PreparedLocation(
            location=location,
            normalized_name=normalize_text(location.name),
            match_key=match_key(location.name),
            compact_key=norm_key(location.name),
            content_words=content_words(location.name),
            important_words=important_words(location.name),
            candidate_words=tuple(word for word in content_words(location.name) if word not in {'tour', 'donjon', 'taverne', 'salle'}),
            parent_normalized_name=normalize_text(parent),
            parent_compact_key=norm_key(parent),
            parent_content_words=content_words(parent),
        )

    @staticmethod
    def _prepare_alias(record: PlaceAliasRecord) -> PreparedAlias:
        return PreparedAlias(
            record=record,
            normalized_alias=normalize_text(record.alias),
            match_key=match_key(record.alias),
            content_words=content_words(record.alias),
            important_words=important_words(record.alias),
        )

    @staticmethod
    def _spans(raw_text: object) -> tuple[str, ...]:
        text = clean_text(raw_text)
        if not text:
            return tuple()
        values: list[str] = [text]
        values.extend(re.findall(r'\(([^()]*)\)', text))
        if '(' in text:
            values.append(text.split('(', 1)[0].strip())

        dash_parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
        if len(dash_parts) > 1:
            values.append(dash_parts[0])
            for end in range(2, len(dash_parts)):
                values.append(' - '.join(dash_parts[:end]))
            values.extend(dash_parts[1:])
        return unique_texts(values)

    @staticmethod
    def _specificity(location: LocationRecord) -> int:
        return 2 if location.kind is LocationKind.SUBAREA else 1

    @staticmethod
    def _match_mode(mode: TextMatchMode) -> MatchMode:
        if mode is TextMatchMode.EXACT:
            return MatchMode.EXACT
        if mode is TextMatchMode.CONTAINED:
            return MatchMode.CONTAINED
        return MatchMode.FUZZY

    def _candidate_locations(self, spans: tuple[str, ...]) -> tuple[PreparedLocation, ...]:
        if not spans:
            return tuple()

        selected: dict[str, PreparedLocation] = {}
        exact_found = False
        for span in spans:
            words = content_words(span)
            exact_candidates = self.exact_index.get(words, ())
            if exact_candidates:
                exact_found = True
                for item in exact_candidates:
                    selected[item.location.canonical_key] = item
            for word in (word for word in content_words(span) if word not in {'tour', 'donjon', 'taverne', 'salle'}):
                for item in self.token_index.get(word, ()):
                    selected[item.location.canonical_key] = item

        if exact_found or selected:
            return tuple(selected.values())
        # OCR typos may change every important token (Solar -> Solor). Falling back
        # to the prepared corpus preserves robust fuzzy fallback behavior without repeated
        # normalization/tokenization of static labels.
        return self.prepared_locations

    def _parent_context_bonus(self, prepared: PreparedLocation, raw_text: str) -> float:
        if prepared.location.kind is not LocationKind.SUBAREA or not prepared.location.parent_area_name:
            return 0.0
        parent_match = text_similarity(prepared.location.parent_area_name, raw_text)
        if parent_match.mode is TextMatchMode.EXACT:
            return 5.0
        if parent_match.mode is TextMatchMode.CONTAINED:
            return 4.0
        if parent_match.score >= 92.0:
            return 2.5
        return 0.0

    def _coordinate_context(self, coordinates: Coordinates | None) -> _CoordinateContext:
        if coordinates is None:
            return _CoordinateContext()
        exact = self.repository.coordinate_candidates(coordinates, tolerance=0)
        nearby = self.repository.coordinate_candidates(coordinates, tolerance=1)
        return _CoordinateContext(
            exact_subarea_ids=frozenset(item.id for item in exact),
            exact_area_ids=frozenset(item.parent_area_id for item in exact if item.parent_area_id is not None),
            nearby_subarea_ids=frozenset(item.id for item in nearby),
            nearby_area_ids=frozenset(item.parent_area_id for item in nearby if item.parent_area_id is not None),
        )

    @staticmethod
    def _coordinate_bonus(location: LocationRecord, context: _CoordinateContext) -> tuple[float, float]:
        if location.kind is LocationKind.SUBAREA and location.id in context.exact_subarea_ids:
            return 5.0, 1.0
        if location.kind is LocationKind.AREA and location.id in context.exact_area_ids:
            return 3.0, 0.65
        if location.kind is LocationKind.SUBAREA and location.id in context.nearby_subarea_ids:
            return 3.0, 0.70
        if location.kind is LocationKind.AREA and location.id in context.nearby_area_ids:
            return 1.5, 0.45
        return 0.0, 0.0

    def _best_text_match(self, prepared: PreparedLocation, spans: tuple[str, ...], raw_text: str) -> LocationMatch | None:
        location = prepared.location
        best: LocationMatch | None = None
        for span in spans:
            similarity = text_similarity(location.name, span)
            if similarity.score <= 0.0:
                continue
            if (
                similarity.mode is TextMatchMode.CONTAINED
                and not prepared.important_words
                and important_words(span)
            ):
                continue
            candidate = LocationMatch(
                location=location,
                score=similarity.score,
                origin=f'{similarity.mode.value}:{span}',
                matched_alias=span,
                exact=similarity.mode is TextMatchMode.EXACT,
                mode=self._match_mode(similarity.mode),
                context_bonus=self._parent_context_bonus(prepared, raw_text),
                coordinate_bonus=0.0,
            )
            if best is None or (
                candidate.score,
                candidate.context_bonus,
                self._specificity(location),
                len(match_key(span)),
            ) > (
                best.score,
                best.context_bonus,
                self._specificity(best.location),
                len(match_key(best.matched_alias)),
            ):
                best = candidate
        return best

    @staticmethod
    def _is_supporting_parent(top: LocationMatch, candidate: LocationMatch) -> bool:
        return (
            top.location.kind is LocationKind.SUBAREA
            and candidate.location.kind is LocationKind.AREA
            and top.location.parent_area_id == candidate.location.id
        )

    def _is_semantically_equivalent(self, top: LocationMatch, candidate: LocationMatch) -> bool:
        if candidate.location.canonical_key == top.location.canonical_key:
            return True
        top_prepared = self._prepared_by_key.get(top.location.canonical_key)
        candidate_prepared = self._prepared_by_key.get(candidate.location.canonical_key)
        if top_prepared is None or candidate_prepared is None or candidate_prepared.match_key != top_prepared.match_key:
            return False

        top_loc = top.location
        cand_loc = candidate.location
        return (
            top_loc.kind is LocationKind.AREA
            and cand_loc.kind is LocationKind.SUBAREA
            and cand_loc.parent_area_id == top_loc.id
        ) or (
            top_loc.kind is LocationKind.SUBAREA
            and cand_loc.kind is LocationKind.AREA
            and top_loc.parent_area_id == cand_loc.id
        )

    def _structured_pair_match(self, raw_text: str, coordinate_context: _CoordinateContext) -> LocationMatch | None:
        match = re.fullmatch(r'\s*(.+?)\s*\(([^()]*)\)\s*', clean_text(raw_text))
        if not match:
            return None
        area_text = clean_text(match.group(1))
        subarea_text = clean_text(match.group(2))
        if not area_text or not subarea_text:
            return None
        candidates = self.structured_index.get((content_words(area_text), content_words(subarea_text)), ())
        if len(candidates) == 1:
            prepared = candidates[0]
            origin = 'structured'
        else:
            prepared = self.structured_compact_index.get((norm_key(area_text), norm_key(subarea_text)))
            if prepared is None:
                return None
            origin = 'structured-compact'

        location = prepared.location
        coord_bonus, _support = self._coordinate_bonus(location, coordinate_context)
        return LocationMatch(
            location=location,
            score=100.0,
            origin=f'{origin}:{area_text}({subarea_text})',
            matched_alias=subarea_text,
            exact=True,
            mode=MatchMode.EXACT,
            context_bonus=12.0,
            coordinate_bonus=coord_bonus,
        )

    def _alias_matches(self, raw_text: str, coordinate_context: _CoordinateContext) -> list[LocationMatch]:
        result: list[LocationMatch] = []
        for prepared in self.prepared_aliases:
            alias_record = prepared.record
            similarity = text_similarity(alias_record.alias, raw_text)
            if similarity.score < 45.0:
                continue
            coord_bonus, _support = self._coordinate_bonus(alias_record.location, coordinate_context)
            priority_bonus = max(0.0, min(4.0, (alias_record.priority - 80) / 5.0))
            result.append(LocationMatch(
                location=alias_record.location,
                score=similarity.score,
                origin=f'alias:{similarity.mode.value}:{alias_record.alias}',
                matched_alias=alias_record.alias,
                exact=similarity.mode is TextMatchMode.EXACT,
                mode=self._match_mode(similarity.mode),
                context_bonus=priority_bonus,
                coordinate_bonus=coord_bonus,
            ))
        return result

    def resolve(
        self,
        raw_text: object,
        ocr_confidence: float,
        coordinates: Coordinates | None = None,
        *,
        timestamp: float | None = None,
    ) -> LocationEvidence:
        raw = clean_text(raw_text)
        spans = self._spans(raw)
        coordinate_context = self._coordinate_context(coordinates)
        matches: list[LocationMatch] = self._alias_matches(raw, coordinate_context)
        structured_match = self._structured_pair_match(raw, coordinate_context)
        if structured_match is not None:
            matches.append(structured_match)

        for prepared in self._candidate_locations(spans):
            match = self._best_text_match(prepared, spans, raw)
            if match is None or match.score < 35.0:
                continue
            coord_bonus, _support = self._coordinate_bonus(prepared.location, coordinate_context)
            if coord_bonus:
                match = replace(match, coordinate_bonus=coord_bonus)
            matches.append(match)

        def sort_key(item: LocationMatch) -> tuple[float, float, int, int]:
            prepared = self._prepared_by_key.get(item.location.canonical_key)
            key_length = len(prepared.match_key) if prepared is not None else len(match_key(item.location.name))
            return (item.rank_score, item.score, self._specificity(item.location), key_length)

        matches.sort(key=sort_key, reverse=True)

        top1 = matches[0] if matches else None
        top2: LocationMatch | None = None
        if top1 is not None:
            for candidate in matches[1:]:
                if self._is_semantically_equivalent(top1, candidate):
                    continue
                if self._is_supporting_parent(top1, candidate):
                    continue
                top2 = candidate
                break

        margin = 0.0 if top1 is None else top1.rank_score - (top2.rank_score if top2 else 0.0)
        coordinate_support = 0.0
        if top1 is not None:
            _bonus, coordinate_support = self._coordinate_bonus(top1.location, coordinate_context)

        return LocationEvidence(
            raw_zone_text=raw,
            ocr_confidence=max(0.0, min(1.0, float(ocr_confidence))),
            top1=top1,
            top2=top2,
            margin=float(margin),
            coordinates=coordinates,
            coordinate_consistency=float(coordinate_support),
            timestamp=float(time.time() if timestamp is None else timestamp),
        )
