from __future__ import annotations

import json
import re
from pathlib import Path

from dofusic.models import LocationKind, LocationRecord
from dofusic.text import match_key

_DUNGEON_PREFIX_RE = re.compile(r"^\s*donjon\s+(?:de\s+la|des|du|de|d[\'’])\s+(.+?)\s*$", re.IGNORECASE)
_EXPEDITION_PREFIX_RE = re.compile(r"^\s*expédition(?:\s+de\s+l[\'’](?:audace|bravoure))?\s*-\s*(.+?)\s*$", re.IGNORECASE)


class DungeonCatalog:
    """Local, deterministic dungeon classifier for confirmed Dofus locations.

    The catalog is intentionally data-driven: Dofusic never scrapes a website at
    runtime.  Exact location names are matched after accent/case normalization, and
    expedition variants reuse the base dungeon entry.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, tuple[str, tuple[str, ...]]] = {}
        if self.path is not None and self.path.is_file():
            self._load(self.path)

    def _load(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return
        rows = payload.get('dungeons', ()) if isinstance(payload, dict) else ()
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get('name') or '').strip()
            if not name:
                continue
            bosses = row.get('bosses', ())
            if isinstance(bosses, str):
                bosses = (bosses,)
            bosses_clean = tuple(str(value).strip() for value in bosses if str(value).strip()) if isinstance(bosses, (list, tuple)) else tuple()
            self._entries[match_key(name)] = (name, bosses_clean)

    @staticmethod
    def base_location_name(name: str) -> str:
        text = str(name or '').strip()
        match = _EXPEDITION_PREFIX_RE.match(text)
        return match.group(1).strip() if match else text

    def _entry(self, location: LocationRecord | None):
        if location is None or location.kind is not LocationKind.SUBAREA:
            return None
        base = self.base_location_name(location.name)
        entry = self._entries.get(match_key(base))
        if entry is not None:
            return entry
        # Mapping entries literally named "Donjon de/du/des ..." remain valid
        # even if absent from the external reference data.
        match = _DUNGEON_PREFIX_RE.match(base)
        if match:
            alias = match.group(1).strip(" -\t")
            return (base, (alias,) if alias else tuple())
        return None

    def is_dungeon(self, location: LocationRecord | None) -> bool:
        return self._entry(location) is not None

    def audio_aliases(self, location: LocationRecord | None) -> tuple[str, ...]:
        entry = self._entry(location)
        if entry is None:
            return tuple()
        canonical, bosses = entry
        base = self.base_location_name(location.name if location is not None else canonical)
        values = [base, canonical, *bosses]
        out: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = match_key(value)
            if value and key and key not in seen:
                out.append(value)
                seen.add(key)
        return tuple(out)
