from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import re

from dofusic.audio.dungeons import DungeonCatalog
from dofusic.location.repository import DofusRepository
from dofusic.models import LocationKind, LocationRecord
from dofusic.text import TextMatchMode, match_key, norm_key, text_similarity

AUDIO_EXTS = {
    '.mp3', '.mp2', '.ogg', '.oga', '.opus', '.wav', '.flac', '.m4a', '.aac', '.wma',
    '.webm', '.mka', '.mp4', '.aiff', '.aif', '.ac3', '.ape', '.wv', '.tta', '.amr', '.caf',
}
_COMBAT_STEM = 'Musique Combat'
_COMBAT_PREFIX_KEY = norm_key(_COMBAT_STEM)
_GENERIC_NORMAL_RE = re.compile(r'^\s*musique\s*\d*\s*$', re.IGNORECASE)
_GENERIC_COMBAT_RE = re.compile(r'^\s*musique\s+combat\s*\d*\s*$', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _TrackScore:
    path: Path
    score: float
    exactness: int


class MusicLibrary:
    """Bibliothèque audio à sens unique : Location confirmée -> morceau.

    Elle peut tolérer un nom de fichier légèrement différent, mais ce résultat ne
    remonte jamais vers LocationResolver/DecisionEngine.
    """

    def __init__(
        self,
        repository: DofusRepository,
        folder: Path | str,
        fallback_stem: str = 'Musique',
        *,
        fuzzy_min: float = 90.0,
        fuzzy_margin: float = 7.0,
        dungeon_catalog: DungeonCatalog | None = None,
    ) -> None:
        self.repository = repository
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.fallback_stem = fallback_stem
        self.fuzzy_min = float(fuzzy_min)
        self.fuzzy_margin = float(fuzzy_margin)
        self.dungeon_catalog = dungeon_catalog or DungeonCatalog()
        self.fallback: Path | None = None
        self.tracks: tuple[Path, ...] = tuple()
        self.all_tracks: tuple[Path, ...] = tuple()
        self.generic_tracks: tuple[Path, ...] = tuple()
        self.generic_combat_tracks: tuple[Path, ...] = tuple()
        self.ambiguous_track_keys: set[str] = set()
        self._named_tracks: dict[str, tuple[Path, ...]] = {}
        self._ambiguity_cache: dict[str, bool] = {}
        # Generic music is a playback-session choice, not a geography cache.
        # A choice stays stable while the current exploration/combat cycle is
        # active and rotates only when the controller explicitly starts a new
        # cycle. This prevents repeated OCR from shuffling tracks while also
        # avoiding the old "one permanent random song per zone" behaviour.
        self._generic_current_by_mode: dict[bool, Path] = {}
        self._random = random.SystemRandom()
        self._locations = repository.all_locations()
        self.scan()

    def scan(self) -> None:
        found: list[Path] = []
        all_found: list[Path] = []
        generic_normal: list[Path] = []
        generic_combat: list[Path] = []
        named: dict[str, list[Path]] = {}
        fallback_key = match_key(self.fallback_stem)
        self.fallback = None
        for path in sorted(self.folder.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            all_found.append(path)
            named.setdefault(match_key(path.stem), []).append(path)

            if _GENERIC_COMBAT_RE.fullmatch(path.stem):
                generic_combat.append(path)
                continue
            if _GENERIC_NORMAL_RE.fullmatch(path.stem):
                generic_normal.append(path)
                if match_key(path.stem) == fallback_key:
                    self.fallback = path
                continue

            # Combat tracks are a separate deterministic namespace. They must
            # never win ordinary fuzzy location matching while out of combat.
            stem_key = norm_key(path.stem)
            if stem_key == _COMBAT_PREFIX_KEY or stem_key.startswith(_COMBAT_PREFIX_KEY + ' '):
                continue
            found.append(path)

        self.tracks = tuple(found)
        self.all_tracks = tuple(all_found)
        self.generic_tracks = tuple(generic_normal)
        self.generic_combat_tracks = tuple(generic_combat)
        self._named_tracks = {key: tuple(paths) for key, paths in named.items()}
        # A library refresh is inventory maintenance only. Opening the local
        # music window must not silently reroll the automatic soundtrack.
        for mode, pool in ((False, self.generic_tracks), (True, self.generic_combat_tracks)):
            current = self._generic_current_by_mode.get(mode)
            if current is not None and current not in pool:
                self._generic_current_by_mode.pop(mode, None)
        # Ambiguity analysis used to compare every audio filename against every
        # Dofus location during startup (O(tracks × locations)). It is now lazy and
        # cached: only the one or two tracks that can actually win a resolution
        # need the expensive geography check.
        self.ambiguous_track_keys.clear()
        self._ambiguity_cache.clear()

    @staticmethod
    def _score_track(path: Path, anchors: tuple[str, ...]) -> _TrackScore:
        best_score = 0.0
        best_exactness = 0
        for anchor in anchors:
            if not anchor:
                continue
            similarity = text_similarity(path.stem, anchor)
            exactness = 2 if similarity.mode is TextMatchMode.EXACT else 1 if similarity.mode is TextMatchMode.CONTAINED else 0
            if (similarity.score, exactness) > (best_score, best_exactness):
                best_score = similarity.score
                best_exactness = exactness
        return _TrackScore(path, best_score, best_exactness)


    def _is_ambiguous_track(self, path: Path) -> bool:
        track_key = match_key(path.stem)
        cached = self._ambiguity_cache.get(track_key)
        if cached is not None:
            return cached
        compatible_names: set[str] = set()
        for location in self._locations:
            similarity = text_similarity(path.stem, location.name)
            if similarity.score >= 96.0:
                compatible_names.add(match_key(location.name))
                if len(compatible_names) > 1:
                    self._ambiguity_cache[track_key] = True
                    self.ambiguous_track_keys.add(track_key)
                    return True
        self._ambiguity_cache[track_key] = False
        return False

    @staticmethod
    def _strict_anchor_match(path: Path, anchors: tuple[str, ...]) -> bool:
        strict_track = norm_key(path.stem)
        return any(strict_track == norm_key(anchor) for anchor in anchors if anchor)

    def _best_track(self, anchors: tuple[str, ...]) -> Path | None:
        if not self.tracks:
            return None

        # Filename-to-anchor scoring is cheap. Sort it first, then perform the
        # expensive ambiguity-against-all-locations check only until two usable
        # candidates remain; only Top1/Top2 can affect the decision below.
        candidates = [self._score_track(path, anchors) for path in self.tracks]
        candidates.sort(
            key=lambda item: (item.exactness, item.score, len(match_key(item.path.stem))),
            reverse=True,
        )
        scored: list[_TrackScore] = []
        for candidate in candidates:
            if self._is_ambiguous_track(candidate.path) and not self._strict_anchor_match(candidate.path, anchors):
                continue
            scored.append(candidate)
            if len(scored) >= 2:
                break
        if not scored:
            return None
        best = scored[0]
        second = scored[1] if len(scored) > 1 else None

        if best.exactness >= 2 and best.score >= 99.0:
            return best.path
        if best.score < self.fuzzy_min:
            return None
        if second is not None and best.score - second.score < self.fuzzy_margin and best.exactness == second.exactness:
            return None
        return best.path

    def _exact_named_track(self, stem: str) -> Path | None:
        matches = self._named_tracks.get(match_key(stem), ())
        return matches[0] if len(matches) == 1 else None

    def _combat_scope_names(self, location: LocationRecord) -> tuple[str, ...]:
        if location.kind is LocationKind.AREA:
            return (location.name,)

        names: list[str] = []
        parent_name = location.parent_area_name or ''
        if not parent_name and location.parent_area_id is not None:
            parent = self.repository.parent_area(location)
            if parent is not None:
                parent_name = parent.name
        if parent_name:
            names.append(parent_name)
        if location.name and match_key(location.name) != match_key(parent_name):
            names.append(location.name)
        return tuple(names)


    def _choose_generic(self, *, combat: bool, new_cycle: bool = False) -> Path | None:
        pool = self.generic_combat_tracks if combat else self.generic_tracks
        if not pool:
            return None

        mode = bool(combat)
        current = self._generic_current_by_mode.get(mode)
        if not new_cycle and current in pool:
            return current

        candidates = list(pool)
        if current in candidates and len(candidates) > 1:
            candidates.remove(current)
        chosen = self._random.choice(candidates)
        self._generic_current_by_mode[mode] = chosen
        return chosen

    def _dungeon_aliases(self, location: LocationRecord) -> tuple[str, ...]:
        return self.dungeon_catalog.audio_aliases(location)

    def _is_dungeon(self, location: LocationRecord) -> bool:
        return self.dungeon_catalog.is_dungeon(location)

    def _dungeon_combat_track(self, location: LocationRecord) -> Path | None:
        # Boss name first (catalog order), then canonical dungeon/location name.
        aliases = (*self._dungeon_aliases(location), location.name)
        seen: set[str] = set()
        for alias in aliases:
            key = match_key(alias)
            if not key or key in seen:
                continue
            seen.add(key)
            track = self._exact_named_track(f'{_COMBAT_STEM} {alias}')
            if track is not None:
                return track
        return None

    def _resolve_normal(
        self,
        location: LocationRecord,
        raw_text: str,
        *,
        new_generic_cycle: bool = False,
    ) -> Path | None:
        # Texte HUD confirmé puis nom canonique. Cela permet plusieurs ambiances
        # d'une même famille sans laisser la musique influencer la reconnaissance.
        aliases = self._dungeon_aliases(location)
        specific = self._best_track((raw_text, location.name, *aliases))
        if specific is not None:
            return specific

        # Zone parente uniquement si aucun morceau spécifique sûr n'existe.
        if location.kind is LocationKind.SUBAREA and location.parent_area_id is not None:
            parent = self.repository.parent_area(location)
            if parent is not None:
                parent_track = self._best_track((parent.name,))
                if parent_track is not None:
                    return parent_track
        return self._choose_generic(combat=False, new_cycle=new_generic_cycle)

    def resolve(
        self,
        location: LocationRecord | None,
        raw_text: str = '',
        *,
        combat: bool = False,
        new_generic_cycle: bool = False,
    ) -> Path | None:
        if not combat:
            return (
                self._choose_generic(combat=False, new_cycle=new_generic_cycle)
                if location is None
                else self._resolve_normal(location, raw_text, new_generic_cycle=new_generic_cycle)
            )

        # A dungeon is an explicit catalogue class, not a name heuristic. Generic
        # combat music must never replace its current exploration soundtrack. If
        # the user supplied a boss/dungeon-specific combat track, that exact file
        # is allowed to take over for the fight.
        if location is not None and self._is_dungeon(location):
            specific_combat = self._dungeon_combat_track(location)
            if specific_combat is not None:
                return specific_combat
            return self._resolve_normal(location, raw_text, new_generic_cycle=new_generic_cycle)

        # Combat names are deterministic rather than fuzzy: a file called
        # "Musique Combat Astrub" can never collide with ordinary "Astrub".
        if location is not None:
            for scope_name in self._combat_scope_names(location):
                scoped_track = self._exact_named_track(f'{_COMBAT_STEM} {scope_name}')
                if scoped_track is not None:
                    return scoped_track

        generic_combat = self._choose_generic(combat=True, new_cycle=new_generic_cycle)
        if generic_combat is not None:
            return generic_combat
        return self._choose_generic(combat=False, new_cycle=new_generic_cycle)

    def report(self) -> dict[str, object]:
        return {
            'tracks': len(self.tracks),
            'fallback': str(self.fallback) if self.fallback else None,
            'generic_tracks': len(self.generic_tracks),
            'generic_combat_tracks': len(self.generic_combat_tracks),
            'ambiguous_tracks': tuple(sorted(self.ambiguous_track_keys)),
        }
