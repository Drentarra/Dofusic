from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class OnlineTrack:
    video_id: str
    title: str
    author: str = ''
    duration_seconds: int = 0
    thumbnail_url: str = ''
    source_url: str = ''

    @property
    def duration_text(self) -> str:
        total = max(0, int(self.duration_seconds))
        if total <= 0:
            return '—'
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:d}:{seconds:02d}'

    @property
    def youtube_url(self) -> str:
        return self.source_url or f'https://www.youtube.com/watch?v={self.video_id}'


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    """One manual playback item, regardless of where its audio comes from."""

    source: Literal['online', 'local']
    title: str
    online_track: OnlineTrack | None = None
    local_path: Path | None = None

    @classmethod
    def online(cls, track: OnlineTrack) -> 'PlaylistItem':
        return cls(source='online', title=track.title, online_track=track)

    @classmethod
    def local(cls, path: Path | str) -> 'PlaylistItem':
        target = Path(path)
        return cls(source='local', title=target.stem, local_path=target)

    @property
    def key(self) -> tuple[str, str]:
        if self.source == 'online' and self.online_track is not None:
            return 'online', self.online_track.video_id
        if self.local_path is not None:
            return 'local', str(self.local_path)
        return self.source, self.title


class PlaybackQueue:
    """Deterministic manual playlist shared by ONLINE and LOCAL tracks."""

    def __init__(self) -> None:
        self.current: PlaylistItem | None = None
        self._pending: deque[PlaylistItem] = deque()
        self.repeat_current = False

    @property
    def pending(self) -> tuple[PlaylistItem, ...]:
        return tuple(self._pending)

    def play_now(self, item: PlaylistItem) -> PlaylistItem:
        self.current = item
        return item

    def enqueue(self, item: PlaylistItem) -> None:
        self._pending.append(item)

    def remove(self, index: int) -> PlaylistItem | None:
        if index < 0 or index >= len(self._pending):
            return None
        values = list(self._pending)
        removed = values.pop(index)
        self._pending = deque(values)
        return removed

    def clear_pending(self) -> None:
        self._pending.clear()

    def reset(self) -> None:
        self.current = None
        self._pending.clear()
        self.repeat_current = False

    def next_after_finish(self, *, ignore_repeat: bool = False) -> PlaylistItem | None:
        if self.current is not None and self.repeat_current and not ignore_repeat:
            return self.current
        self.current = None
        if self._pending:
            return self._pending.popleft()
        return None
