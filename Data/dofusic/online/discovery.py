from __future__ import annotations

import json
import logging
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from dofusic.online.models import OnlineTrack


_LOG = logging.getLogger('dofusic.music.discovery')
_SUGGEST_ENDPOINT = 'https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=fr&gl=fr&q={query}'
_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36'


def _compact_text(value: object) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _duration_seconds(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _thumbnail_url(row: Mapping[str, object]) -> str:
    direct = _compact_text(row.get('thumbnail'))
    if direct:
        return direct
    thumbnails = row.get('thumbnails')
    if isinstance(thumbnails, (list, tuple)):
        for item in reversed(thumbnails):
            if isinstance(item, Mapping):
                url = _compact_text(item.get('url'))
                if url:
                    return url
    return ''




def _efficient_thumbnail_url(row: Mapping[str, object], video_id: str) -> str:
    current = _thumbnail_url(row)
    lowered = current.casefold()
    if 'ytimg.com/' in lowered or 'ggpht.com/' in lowered or not current:
        return f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'
    return current

def _track_from_row(row: Mapping[str, object]) -> OnlineTrack | None:
    video_id = _compact_text(row.get('id') or row.get('video_id'))
    if not video_id:
        url = _compact_text(row.get('webpage_url') or row.get('url'))
        match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})', url)
        video_id = match.group(1) if match else ''
    if not video_id:
        return None
    title = _compact_text(row.get('title')) or 'Vidéo YouTube'
    author = _compact_text(row.get('uploader') or row.get('channel') or row.get('channel_name'))
    source_url = _compact_text(row.get('webpage_url') or row.get('original_url'))
    if not source_url or source_url.startswith('ytsearch'):
        source_url = f'https://www.youtube.com/watch?v={video_id}'
    return OnlineTrack(
        video_id=video_id,
        title=title,
        author=author,
        duration_seconds=_duration_seconds(row.get('duration')),
        thumbnail_url=_efficient_thumbnail_url(row, video_id),
        source_url=source_url,
    )


def _default_search_runner(query: str, limit: int) -> Iterable[Mapping[str, object]]:
    try:
        import yt_dlp
    except Exception as exc:  # pragma: no cover - packaging dependency
        raise RuntimeError('Moteur de recherche YouTube indisponible (yt-dlp)') from exc

    wanted = max(1, min(20, int(limit)))
    options = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'noplaylist': False,
        'socket_timeout': 8,
        'retries': 1,
        'playlistend': wanted,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        payload = ydl.extract_info(f'ytsearch{wanted}:{query}', download=False)
    entries = payload.get('entries') if isinstance(payload, Mapping) else None
    if not isinstance(entries, (list, tuple)):
        return tuple()
    return tuple(item for item in entries if isinstance(item, Mapping))


def _default_suggestion_runner(query: str, limit: int) -> Iterable[str]:
    endpoint = _SUGGEST_ENDPOINT.format(query=quote_plus(query))
    request = Request(endpoint, headers={'User-Agent': _USER_AGENT, 'Accept': 'application/json,text/plain,*/*'})
    try:
        with urlopen(request, timeout=3.0) as response:
            raw = response.read(256 * 1024)
        payload = json.loads(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        _LOG.warning('[MUSIC][DISCOVERY] suggestions failed query=%r error=%s', query, exc)
        return tuple()
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return tuple()
    return tuple(str(value) for value in payload[1][: max(1, int(limit))])


class YouTubeDiscoveryClient:
    """Lightweight YouTube discovery with no embedded browser or persistent session."""

    def __init__(
        self,
        *,
        search_runner: Callable[[str, int], Iterable[Mapping[str, object]]] | None = None,
        suggestion_runner: Callable[[str, int], Iterable[str]] | None = None,
    ) -> None:
        self._search_runner = search_runner or _default_search_runner
        self._suggestion_runner = suggestion_runner or _default_suggestion_runner

    def prewarm(self) -> bool:
        try:
            import yt_dlp  # noqa: F401
        except Exception:
            return False
        return True

    def search(self, query: str, *, limit: int = 8) -> tuple[OnlineTrack, ...]:
        text = _compact_text(query)
        if not text:
            return tuple()
        wanted = max(1, min(20, int(limit)))
        try:
            rows = self._search_runner(text, wanted)
        except Exception as exc:
            _LOG.warning('[MUSIC][DISCOVERY] search failed query=%r error=%s', text, exc)
            raise RuntimeError(f'Recherche YouTube indisponible : {exc}') from exc

        tracks: list[OnlineTrack] = []
        seen: set[str] = set()
        for row in rows or ():
            if not isinstance(row, Mapping):
                continue
            track = _track_from_row(row)
            if track is None or track.video_id in seen:
                continue
            seen.add(track.video_id)
            tracks.append(track)
            if len(tracks) >= wanted:
                break
        return tuple(tracks)

    def suggestions(self, query: str, *, limit: int = 8) -> tuple[str, ...]:
        text = _compact_text(query)
        if len(text) < 2:
            return tuple()
        wanted = max(1, min(12, int(limit)))
        try:
            values = self._suggestion_runner(text, wanted)
        except Exception:
            return tuple()
        result: list[str] = []
        seen: set[str] = set()
        for value in values or ():
            item = _compact_text(value)
            key = item.casefold()
            if not item or key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= wanted:
                break
        return tuple(result)

    def close(self) -> None:
        return None
