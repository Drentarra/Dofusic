from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import threading

from dofusic.config import AppConfig, default_music_dir, user_data_dir
from dofusic.online.discovery import YouTubeDiscoveryClient
from dofusic.online.media_cache import MediaCache
from dofusic.online.models import OnlineTrack, PlaylistItem
from dofusic.online.playback import PlaybackController
from dofusic.online.search import SearchController, SearchPhase, SearchSnapshot, SuggestionSnapshot
from dofusic.online.thumbnails import ThumbnailService


class MediaActionPhase(str, Enum):
    IDLE = 'idle'
    CACHING = 'caching'
    READY = 'ready'
    SAVING = 'saving'
    SAVED = 'saved'
    ERROR = 'error'


@dataclass(frozen=True, slots=True)
class MediaActionEvent:
    revision: int
    phase: MediaActionPhase
    track: OnlineTrack | None = None
    path: Path | None = None
    error: str = ''
    error_code: str = ''


class MusicSession:
    """Single UI-facing facade for Dofusic music state.

    Tkinter sends intentions to this object and renders snapshots/events. Futures,
    worker ownership and stale-result handling stay inside the session/controllers.
    """

    def __init__(
        self,
        controller,
        config: AppConfig,
        *,
        discovery: YouTubeDiscoveryClient | None = None,
        media_cache: MediaCache | None = None,
        search_controller: SearchController | None = None,
        playback: PlaybackController | None = None,
        thumbnail_service: ThumbnailService | None = None,
        media_executor=None,
        thumbnail_executor=None,
        search_executor=None,
        suggestion_executor=None,
        playback_executor=None,
    ) -> None:
        self.controller = controller
        self.config = config
        self.discovery = discovery or YouTubeDiscoveryClient()
        music_dir = Path(config.music_dir) if getattr(config, 'music_dir', '') else default_music_dir()
        self.media_cache = media_cache or MediaCache(
            user_data_dir() / 'cache' / 'online',
            music_dir,
            cache_mb=int(getattr(config, 'online_cache_mb', 128)),
        )
        self.search = search_controller or SearchController(
            self.discovery,
            result_limit=int(getattr(config, 'online_search_results', 8)),
            executor=search_executor,
            suggestion_executor=suggestion_executor,
        )
        self.playback = playback or PlaybackController(
            controller, config, self.media_cache, executor=playback_executor,
        )
        self.thumbnail_service = thumbnail_service or ThumbnailService(user_data_dir() / 'cache' / 'thumbnails')
        self._media_executor = media_executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='DofusicMediaAction')
        self._thumbnail_executor = thumbnail_executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='DofusicThumb')
        self._owns_media_executor = media_executor is None
        self._owns_thumbnail_executor = thumbnail_executor is None
        self._lock = threading.RLock()

        self._thumbnail_jobs: dict[tuple[str, tuple[int, int]], Future] = {}
        self._thumbnail_ready: dict[str, bytes] = {}
        self._media_future: Future | None = None
        self._media_track: OnlineTrack | None = None
        self._media_action = ''
        self._media_revision = 0
        self._media_event = MediaActionEvent(0, MediaActionPhase.IDLE)
        self._search_memory: dict[str, tuple[str, tuple[OnlineTrack, ...]]] = {
            'online': ('', tuple()),
            'local': ('', tuple()),
        }
        self.music_source_mode = 'online'
        self._last_search_revision = 0
        self.search_health_ok = True

    @property
    def current(self) -> PlaylistItem | None:
        return self.playback.current

    @property
    def preparing(self) -> PlaylistItem | None:
        return self.playback.preparing

    @property
    def pending(self) -> tuple[PlaylistItem, ...]:
        return self.playback.pending

    @property
    def repeat_current(self) -> bool:
        return self.playback.repeat_current

    @property
    def status(self) -> str:
        event = self._media_event
        if event.phase is MediaActionPhase.CACHING and event.track is not None:
            return f'Téléchargement : {event.track.title}'
        if event.phase is MediaActionPhase.SAVING:
            return 'Ajout dans le dossier Musiques…'
        if event.phase is MediaActionPhase.ERROR:
            return event.error
        return self.playback.status

    @property
    def last_error(self) -> str:
        return self.playback.last_error

    def clear_error(self) -> None:
        self.playback.clear_error()

    def remember_search(self, mode: str, query: str, results=()) -> None:
        key = 'local' if str(mode).lower() == 'local' else 'online'
        values = tuple(results or ()) if key == 'online' else tuple()
        self._search_memory[key] = (str(query or ''), values)

    def search_memory(self, mode: str) -> tuple[str, tuple[OnlineTrack, ...]]:
        key = 'local' if str(mode).lower() == 'local' else 'online'
        return self._search_memory.get(key, ('', tuple()))

    def apply_config(self) -> None:
        try:
            self.media_cache.cache_bytes = max(64, int(getattr(self.config, 'online_cache_mb', 128))) * 1024 * 1024
        except Exception:
            pass

    def prewarm(self) -> None:
        self.search.prewarm()

    def submit_search(self, query: str) -> int:
        return self.search.submit_search(query)

    def submit_suggestions(self, query: str) -> int:
        return self.search.submit_suggestions(query, limit=8)

    def search_snapshot(self) -> SearchSnapshot:
        return self.search.snapshot()

    def suggestions_snapshot(self) -> SuggestionSnapshot:
        return self.search.suggestions_snapshot()

    def request_thumbnail(self, track: OnlineTrack, *, size: tuple[int, int] = (112, 63)) -> None:
        normalized = (max(16, int(size[0])), max(9, int(size[1])))
        key = (track.video_id, normalized)
        with self._lock:
            if track.video_id in self._thumbnail_ready:
                return
            existing = self._thumbnail_jobs.get(key)
            if existing is not None and not existing.done():
                return
            try:
                self._thumbnail_jobs[key] = self._thumbnail_executor.submit(
                    self.thumbnail_service.get_png, track, size=normalized,
                )
            except RuntimeError:
                return

    def take_thumbnail(self, video_id: str) -> bytes | None:
        with self._lock:
            return self._thumbnail_ready.pop(str(video_id), None)

    def begin_download(self, track: OnlineTrack) -> bool:
        with self._lock:
            if self._media_future is not None and not self._media_future.done():
                return False
            self._media_revision += 1
            revision = self._media_revision
            self._media_track = track
            self._media_action = 'cache'
            self._media_event = MediaActionEvent(revision, MediaActionPhase.CACHING, track=track)
            try:
                future = self._media_executor.submit(self.media_cache.ensure_cached, track)
            except RuntimeError as exc:
                self._media_event = MediaActionEvent(revision, MediaActionPhase.ERROR, track=track, error=str(exc))
                return False
            setattr(future, '_dofusic_revision', revision)
            self._media_future = future
            return True

    def begin_save(self, track: OnlineTrack, desired_name: str) -> bool:
        with self._lock:
            if self._media_future is not None and not self._media_future.done():
                return False
            self._media_revision += 1
            revision = self._media_revision
            self._media_track = track
            self._media_action = 'save'
            self._media_event = MediaActionEvent(revision, MediaActionPhase.SAVING, track=track)
            try:
                future = self._media_executor.submit(self.media_cache.save_to_library, track, desired_name)
            except RuntimeError as exc:
                self._media_event = MediaActionEvent(revision, MediaActionPhase.ERROR, track=track, error=str(exc))
                return False
            setattr(future, '_dofusic_revision', revision)
            self._media_future = future
            return True

    def media_event(self) -> MediaActionEvent:
        return self._media_event

    def acknowledge_media_event(self, revision: int) -> None:
        with self._lock:
            if self._media_event.revision == int(revision) and self._media_event.phase in {
                MediaActionPhase.READY, MediaActionPhase.SAVED, MediaActionPhase.ERROR,
            }:
                self._media_event = MediaActionEvent(revision, MediaActionPhase.IDLE)

    def media_busy_video_id(self) -> str:
        event = self._media_event
        if event.phase in {MediaActionPhase.CACHING, MediaActionPhase.SAVING} and event.track is not None:
            return event.track.video_id
        return ''

    def play_now(self, track: OnlineTrack) -> bool:
        return self.playback.play_now(track)

    def enqueue(self, track: OnlineTrack) -> None:
        self.playback.enqueue(track)

    def play_local_track(self, path: Path | str) -> bool:
        return self.playback.play_local_track(path)

    def enqueue_local_track(self, path: Path | str) -> bool:
        return self.playback.enqueue_local_track(path)

    def local_tracks(self, *, refresh: bool = False) -> tuple[Path, ...]:
        return self.playback.local_tracks(refresh=refresh)

    def remove_pending(self, index: int):
        return self.playback.remove_pending(index)

    def clear_queue(self) -> None:
        self.playback.clear_queue()

    def set_repeat(self, enabled: bool) -> None:
        self.playback.set_repeat(enabled)

    def set_volume(self, value: int | float) -> int:
        volume = max(0, min(100, int(round(value))))
        self.config.volume = volume
        self.playback.set_volume(volume)
        return volume

    def set_muted(self, muted: bool) -> None:
        value = bool(muted)
        self.config.mute = value
        self.playback.set_muted(value)

    def resume_dofus_music(self) -> bool:
        return self.playback.resume_dofus_music()

    def stop_online(self) -> bool:
        return self.playback.resume_dofus_music()

    def online_visualizer_levels(self, count: int = 24) -> tuple[float, ...]:
        return self.playback.visualizer_levels(count)

    def register_library_change(self) -> None:
        try:
            self.controller.music_library.scan()
        except Exception:
            pass

    def tick(self) -> None:
        self.search.tick()
        self.playback.tick()

        snapshot = self.search.snapshot()
        if snapshot.revision != self._last_search_revision and snapshot.phase is not SearchPhase.LOADING:
            self._last_search_revision = snapshot.revision
            self.search_health_ok = snapshot.phase is not SearchPhase.ERROR

        with self._lock:
            for key, future in tuple(self._thumbnail_jobs.items()):
                if not future.done():
                    continue
                self._thumbnail_jobs.pop(key, None)
                video_id = key[0]
                try:
                    payload = bytes(future.result() or b'')
                except Exception:
                    payload = b''
                if payload:
                    self._thumbnail_ready[video_id] = payload

            future = self._media_future
            if future is not None and future.done():
                self._media_future = None
                track = self._media_track
                action = self._media_action
                revision = int(getattr(future, '_dofusic_revision', self._media_revision))
                self._media_track = None
                self._media_action = ''
                try:
                    path = Path(future.result())
                except Exception as exc:
                    code = getattr(getattr(exc, 'code', None), 'value', '')
                    self._media_event = MediaActionEvent(
                        revision, MediaActionPhase.ERROR, track=track, error=str(exc), error_code=str(code),
                    )
                else:
                    phase = MediaActionPhase.READY if action == 'cache' else MediaActionPhase.SAVED
                    self._media_event = MediaActionEvent(revision, phase, track=track, path=path)

    def close(self) -> None:
        self.search.close()
        self.playback.close()
        try:
            self.discovery.close()
        except Exception:
            pass
        with self._lock:
            for future in list(self._thumbnail_jobs.values()) + ([self._media_future] if self._media_future is not None else []):
                if future is not None and not future.done():
                    future.cancel()
            self._thumbnail_jobs.clear()
            self._thumbnail_ready.clear()
            self._media_future = None
        if self._owns_media_executor:
            self._media_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_thumbnail_executor:
            self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)
