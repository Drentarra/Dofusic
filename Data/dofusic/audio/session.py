"""Lecture manuelle et file d'attente pour les seuls fichiers musicaux locaux.

La musique automatique des zones/donjons reste la responsabilité de DofusicController.
Une sélection manuelle suspend temporairement cette lecture puis la restitue à
la fin de la file ou sur demande explicite.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import logging
import time

_LOG = logging.getLogger('dofusic.music.local')


@dataclass(frozen=True, slots=True)
class LocalTrack:
    local_path: Path

    @property
    def title(self) -> str:
        return self.local_path.stem

    @property
    def key(self) -> str:
        return str(self.local_path)


class MusicSession:
    """État de la bibliothèque et lecture manuelle ; aucun réseau ni thread."""

    def __init__(self, controller, config) -> None:
        self.controller = controller
        self.config = config
        self.current: LocalTrack | None = None
        self._pending: deque[LocalTrack] = deque()
        self.repeat_current = False
        self.status = 'Bibliothèque locale prête'
        self.last_error = ''
        self._playing = False
        self._busy_grace_until = 0.0
        self._search_query = ''

    @property
    def pending(self) -> tuple[LocalTrack, ...]:
        return tuple(self._pending)

    def remember_search(self, query: str) -> None:
        self._search_query = str(query or '')

    @property
    def search_query(self) -> str:
        return self._search_query

    def apply_config(self) -> None:
        """Les préférences du lecteur sont appliquées par la fenêtre principale."""

    def local_tracks(self, *, refresh: bool = False) -> tuple[Path, ...]:
        library = self.controller.music_library
        if refresh:
            library.scan()
        return tuple(library.all_tracks)

    def _known_track(self, path: Path | str) -> LocalTrack | None:
        target = Path(path)
        if target in self.local_tracks() and target.is_file():
            return LocalTrack(target)
        self.last_error = f'Musique locale introuvable : {target.name}'
        self.status = self.last_error
        return None

    def _play(self, item: LocalTrack) -> bool:
        try:
            ok = bool(self.controller.play_manual_local_track(item.local_path, item.title, loop=False))
        except Exception:
            _LOG.exception('Lecture locale impossible : %s', item.local_path)
            ok = False
        if not ok:
            self.last_error = f'Impossible de lire : {item.local_path.name}'
            self.status = self.last_error
            return False
        self.current = item
        self._playing = True
        self._busy_grace_until = time.monotonic() + 0.35
        self.last_error = ''
        self.status = f'Lecture locale : {item.title}'
        return True

    def play_local_track(self, path: Path | str) -> bool:
        item = self._known_track(path)
        return item is not None and self._play(item)

    def enqueue_local_track(self, path: Path | str) -> bool:
        item = self._known_track(path)
        if item is None:
            return False
        self._pending.append(item)
        self.status = f'Ajouté à la file : {item.title}'
        return True

    def remove_pending(self, index: int) -> LocalTrack | None:
        index = int(index)
        if index < 0 or index >= len(self._pending):
            return None
        queue = list(self._pending)
        removed = queue.pop(index)
        self._pending = deque(queue)
        self.status = f'Retiré de la file : {removed.title}'
        return removed

    def clear_queue(self) -> None:
        self._pending.clear()
        self.status = 'File vidée'

    def set_repeat(self, enabled: bool) -> None:
        self.repeat_current = bool(enabled)

    def set_volume(self, value: int | float) -> int:
        self.config.volume = max(0, min(100, int(round(value))))
        return self.config.volume

    def set_muted(self, muted: bool) -> None:
        self.config.mute = bool(muted)

    def resume_dofus_music(self) -> bool:
        self.current = None
        self._pending.clear()
        self.repeat_current = False
        self._playing = False
        result = bool(self.controller.resume_local_audio())
        self.status = 'Retour à la musique Dofus'
        return result

    def _advance(self) -> None:
        if self.current is not None and self.repeat_current:
            if self._play(self.current):
                return
            # One unreadable file must not leave playback stuck forever.
            self.repeat_current = False
        self.current = None
        while self._pending:
            if self._play(self._pending.popleft()):
                return
        self._playing = False
        self.controller.resume_local_audio()
        self.status = 'Retour à la musique Dofus'

    def tick(self, *, now: float | None = None) -> None:
        if not self._playing:
            return
        now = time.monotonic() if now is None else float(now)
        if now < self._busy_grace_until:
            return
        try:
            busy = bool(self.controller.player.is_playing())
        except Exception:
            _LOG.exception('Impossible de lire l’état du lecteur')
            return
        if not busy:
            self._advance()

    def close(self) -> None:
        """Ne coupe pas la musique en cours en fermant seulement la fenêtre."""
