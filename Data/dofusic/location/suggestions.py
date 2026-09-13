from __future__ import annotations

from dofusic.text import match_key


def location_name_suggestions(repository, query: str, *, limit: int = 8) -> tuple[str, ...]:
    needle = match_key(query or '')
    if not needle:
        return tuple()
    names: list[str] = []
    seen: set[str] = set()
    for location in repository.all_locations():
        name = str(getattr(location, 'name', '') or '').strip()
        key = match_key(name)
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    starts = [name for name in names if match_key(name).startswith(needle)]
    contains = [name for name in names if needle in match_key(name) and name not in starts]
    starts.sort(key=lambda name: (len(match_key(name)), match_key(name)))
    contains.sort(key=lambda name: (match_key(name).find(needle), len(match_key(name)), match_key(name)))
    return tuple((starts + contains)[:max(1, int(limit))])
