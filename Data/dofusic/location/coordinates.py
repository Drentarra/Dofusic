from __future__ import annotations

import re
from collections.abc import Callable

from dofusic.models import Coordinates
from dofusic.text import clean_text

# Dofusic mapping currently contains no axis outside -99..99. Keep the parser
# aligned with the actual HUD contract: optional sign + one/two digits per axis.
_COORD_RE = re.compile(r"(?<!\d)([+-]?\d{1,2})\s*[,;:.]\s*([+-]?\d{1,2})(?!\d)")
_COORD_FALLBACK_RE = re.compile(r"^\s*([+-]?\d{1,2})\s+([+-]?\d{1,2})(?=\D|$)")
_COORD_GROUP_RE = re.compile(r"^\s*([+-]?\d{1,2})\s*(?:[,;:.]|\s+)\s*([+-]?\d{1,2})")

_NUMERIC_TRANSLATION = str.maketrans({
    '−': '-', '–': '-', '—': '-',
    'O': '0', 'o': '0',
    'I': '1', 'l': '1', '|': '1',
})


def _numeric_text(value: object) -> str:
    return clean_text(value).translate(_NUMERIC_TRANSLATION)


def _bounded(value: int) -> bool:
    return -99 <= int(value) <= 99


def parse_coordinates(value: object) -> Coordinates | None:
    """Parse the first Dofus ``X,Y`` coordinate pair, strictly in -99..99.

    OCR punctuation confusions ``; : .`` are tolerated, as is a missing comma
    when two signed numeric tokens are clearly separated by whitespace. Text
    after the second number (for example ``- Niveau 40%``) is ignored.
    """
    text = _numeric_text(value)
    match = _COORD_RE.search(text) or _COORD_FALLBACK_RE.search(text)
    if not match:
        return None
    try:
        coordinates = Coordinates(int(match.group(1)), int(match.group(2)))
    except (TypeError, ValueError):
        return None
    return coordinates if _bounded(coordinates.x) and _bounded(coordinates.y) else None


def manhattan_distance(a: Coordinates, b: Coordinates) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def chebyshev_distance(a: Coordinates, b: Coordinates) -> int:
    return max(abs(a.x - b.x), abs(a.y - b.y))


def positions_compatible(a: Coordinates | None, b: Coordinates | None, *, tolerance: int = 2) -> bool:
    if a is None or b is None:
        return False
    return chebyshev_distance(a, b) <= max(0, int(tolerance))


def _sign_variants_from_explicit_text(text: str, parsed: Coordinates) -> set[Coordinates]:
    """Generate only the useful lost-sign hypotheses for an otherwise valid pair.

    An explicitly visible sign is authoritative. If OCR omitted a sign, temporal
    context or the map database may select the opposite sign; no digit splitting
    or glued-level heuristics are generated anymore.
    """
    match = _COORD_GROUP_RE.search(text)
    if not match:
        return {parsed}
    x_token, y_token = match.group(1), match.group(2)
    x_values = {parsed.x} if x_token.startswith(('+', '-')) else {abs(parsed.x), -abs(parsed.x)}
    y_values = {parsed.y} if y_token.startswith(('+', '-')) else {abs(parsed.y), -abs(parsed.y)}
    return {
        Coordinates(x, y)
        for x in x_values
        for y in y_values
        if _bounded(x) and _bounded(y)
    }


def coordinate_hypotheses(raw_text: object, parsed: Coordinates | None = None) -> set[Coordinates]:
    text = _numeric_text(raw_text)
    parsed = parsed or parse_coordinates(text)
    if parsed is None:
        return set()
    return _sign_variants_from_explicit_text(text, parsed)


def contextual_coordinates(
    raw_text: object,
    parsed: Coordinates | None,
    reference: Coordinates | None,
    *,
    tolerance: int = 2,
    known_coordinate: Callable[[Coordinates], bool] | None = None,
) -> Coordinates | None:
    """Resolve only a possible lost sign; never invent or split coordinate digits.

    A nearby previous position may repair a missing minus. A real teleport remains
    valid because an explicit parsed X,Y is returned when no close sign hypothesis
    exists. The map database is an additional validator, not a replacement OCR.
    """
    text = _numeric_text(raw_text)
    parsed = parsed or parse_coordinates(text)
    if parsed is None:
        return None

    candidates = coordinate_hypotheses(text, parsed)
    if reference is not None and candidates:
        ranked = sorted(
            candidates,
            key=lambda item: (
                chebyshev_distance(item, reference),
                manhattan_distance(item, reference),
                0 if item == parsed else 1,
            ),
        )
        best = ranked[0]
        if positions_compatible(best, reference, tolerance=tolerance):
            return best

    if known_coordinate is not None:
        try:
            if known_coordinate(parsed):
                return parsed
        except Exception:
            pass
        known: list[Coordinates] = []
        for candidate in candidates:
            if candidate == parsed:
                continue
            try:
                if known_coordinate(candidate):
                    known.append(candidate)
            except Exception:
                continue
        unique = tuple(dict.fromkeys(known))
        if len(unique) == 1:
            return unique[0]

    return parsed
