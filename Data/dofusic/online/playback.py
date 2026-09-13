from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
from pathlib import Path
import time

from dofusic.online.media_cache import MediaCache
from dofusic.online.models import OnlineTrack, PlaybackQueue, PlaylistItem


_LOG = logging.getLogger('dofusic.music.playback')


class PlaybackController:
    """Own queue and file-backed playback state.

    Online audio is cached first, then handed to
    Dofusic's normal player, so local and online tracks share end detection,
    volume/mute and the real FFT path.
    """

    def __init__(self, controller, config, media_cache: MediaCache, *, executor=None) -> None:
        self.controller = controller
        self.config = config
        self.media_cache = media_cache
        self.queue = PlaybackQueue()
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='DofusicPlayback')
        self._owns_executor = executor is None
        self._playing = False
        self._busy_grace_until = 0.0
        self._prepare_future: Future | None = None
        self._preparing_item: PlaylistItem | None = None
        self._prepare_generation = 0
        self._prepare_advance_on_failure = False
        self._prepare_started_at = 0.0
        self.status = 'Musique prête'
        self.last_error = ''

    @property
    def current(self) -> PlaylistItem | None:
        return self.queue.current

    @property
    def preparing(self) -> PlaylistItem | None:
        return self._preparing_item

    @property
    def pending(self) -> tuple[PlaylistItem, ...]:
        return self.queue.pending

    @property
    def repeat_current(self) -> bool:
        return self.queue.repeat_current

    @property
    def manual_local_current(self) -> Path | None:
        current = self.current
        return current.local_path if current is not None and current.source == 'local' else None

    def clear_error(self) -> None:
        self.last_error = ''

    def set_repeat(self, enabled: bool) -> None:
        self.queue.repeat_current = bool(enabled)

    def set_volume(self, value: int | float) -> int:
        volume = max(0, min(100, int(round(value))))
        self.config.volume = volume
        return volume

    def set_muted(self, muted: bool) -> None:
        self.config.mute = bool(muted)

    def local_tracks(self, *, refresh: bool = False) -> tuple[Path, ...]:
        try:
            if refresh:
                self.controller.music_library.scan()
            return tuple(self.controller.music_library.all_tracks)
        except Exception:
            return tuple()

    def _activate_online_file(self, path: Path | str, title: str) -> bool:
        activate = getattr(self.controller, 'play_online_track', None)
        if not callable(activate):
            raise RuntimeError('Le contrôleur Dofusic ne supporte pas la lecture audio online')
        return bool(activate(path, title))

    def _cancel_prepare(self) -> None:
        self._prepare_generation += 1
        future = self._prepare_future
        self._prepare_future = None
        self._preparing_item = None
        self._prepare_advance_on_failure = False
        if future is not None and not future.done():
            future.cancel()

    def _schedule_online_item(self, item: PlaylistItem, *, advance_on_failure: bool = False) -> bool:
        track = item.online_track
        if track is None:
            self.last_error = 'Piste online invalide'
            self.status = self.last_error
            return False

        validate_track = getattr(self.media_cache, 'validate_track', None)
        if callable(validate_track):
            try:
                validate_track(track)
            except Exception as exc:
                self.last_error = str(exc)
                self.status = f'Erreur online : {self.last_error}'
                return False

        preparing = self._preparing_item
        future = self._prepare_future
        if preparing is not None and future is not None and not future.done():
            if preparing.key == item.key:
                self.status = f'Préparation audio : {track.title}'
                return True
            self.status = f'Préparation déjà en cours : {preparing.title}'
            return False

        self._cancel_prepare()
        generation = self._prepare_generation
        self._preparing_item = item
        self._prepare_advance_on_failure = bool(advance_on_failure)
        self._prepare_started_at = time.monotonic()
        self.status = f'Préparation audio : {track.title}'
        _LOG.info('[MUSIC][PLAYBACK] prepare start id=%s title=%r', track.video_id, track.title)
        try:
            future = self._executor.submit(self.media_cache.ensure_cached, track)
        except Exception as exc:
            self._preparing_item = None
            self.last_error = str(exc)
            self.status = f'Erreur online : {self.last_error}'
            return False
        setattr(future, '_dofusic_generation', generation)
        self._prepare_future = future
        if future.done():
            return self._finish_prepare(now=time.monotonic())
        return True

    def _finish_prepare(self, *, now: float) -> bool:
        future = self._prepare_future
        item = self._preparing_item
        if future is None or item is None or not future.done():
            return False
        generation = getattr(future, '_dofusic_generation', None)
        if generation != self._prepare_generation:
            self._prepare_future = None
            self._preparing_item = None
            self._prepare_advance_on_failure = False
            return False

        advance_on_failure = self._prepare_advance_on_failure
        self._prepare_future = None
        self._preparing_item = None
        self._prepare_advance_on_failure = False
        track = item.online_track
        try:
            cached_path = Path(future.result())
            if track is None:
                raise RuntimeError('Piste online invalide')
            if not self._activate_online_file(cached_path, track.title):
                raise RuntimeError('Dofusic n’a pas pu démarrer le fichier audio online')
        except Exception as exc:
            self.last_error = str(exc)
            self.status = f'Erreur online : {self.last_error}'
            if advance_on_failure:
                self._advance(ignore_repeat=True)
            return False

        elapsed_ms = int(max(0.0, time.monotonic() - self._prepare_started_at) * 1000)
        _LOG.info('[MUSIC][PLAYBACK] prepare done id=%s elapsed_ms=%d', track.video_id, elapsed_ms)
        self.queue.play_now(item)
        self._playing = True
        self._busy_grace_until = float(now) + 0.75
        self.clear_error()
        self.status = f'Lecture : {track.title}'
        return True

    def _play_local_item(self, item: PlaylistItem) -> bool:
        target = item.local_path
        if target is None:
            self.status = 'Piste locale invalide'
            return False
        self._cancel_prepare()
        try:
            ok = bool(self.controller.play_manual_local_track(target, item.title, loop=False))
        except TypeError:
            ok = bool(self.controller.play_manual_local_track(target, item.title))
        if not ok:
            self.status = f'Impossible de lire : {target.name}'
            return False
        self.queue.play_now(item)
        self._playing = True
        self._busy_grace_until = time.monotonic() + 0.35
        self.clear_error()
        self.status = f'Lecture locale : {item.title}'
        return True

    def _start_item(self, item: PlaylistItem, *, advance_on_failure: bool = False) -> bool:
        if item.source == 'local':
            return self._play_local_item(item)
        return self._schedule_online_item(item, advance_on_failure=advance_on_failure)

    def play_now(self, track: OnlineTrack) -> bool:
        self.clear_error()
        return self._schedule_online_item(PlaylistItem.online(track))

    def enqueue(self, track: OnlineTrack) -> None:
        item = PlaylistItem.online(track)
        self.queue.enqueue(item)
        self.status = f'Ajouté à la file : {item.title}'

    def play_local_track(self, path: Path | str) -> bool:
        target = Path(path)
        if target not in self.local_tracks():
            self.status = f'Musique locale introuvable : {target.name}'
            return False
        return self._play_local_item(PlaylistItem.local(target))

    def enqueue_local_track(self, path: Path | str) -> bool:
        target = Path(path)
        if target not in self.local_tracks():
            self.status = f'Musique locale introuvable : {target.name}'
            return False
        item = PlaylistItem.local(target)
        self.queue.enqueue(item)
        self.status = f'Ajouté à la file : {item.title}'
        return True

    def remove_pending(self, index: int) -> PlaylistItem | None:
        removed = self.queue.remove(int(index))
        if removed is not None:
            self.status = f'Retiré de la file : {removed.title}'
        return removed

    def clear_queue(self) -> None:
        self.queue.clear_pending()
        self.status = 'File vidée'

    def resume_dofus_music(self) -> bool:
        self._cancel_prepare()
        self._playing = False
        self.queue.reset()
        result = bool(self.controller.resume_local_audio())
        self.status = 'Retour à la musique Dofus'
        return result

    stop_online = resume_dofus_music

    def _advance(self, *, ignore_repeat: bool = False) -> None:
        next_item = self.queue.next_after_finish(ignore_repeat=ignore_repeat)
        self._playing = False
        if next_item is None:
            self.controller.resume_local_audio()
            self.status = 'Retour à la musique Dofus'
            return
        if not self._start_item(next_item, advance_on_failure=True):
            self._advance(ignore_repeat=True)

    def visualizer_levels(self, count: int = 24) -> tuple[float, ...]:
        bands = max(1, int(count))
        current = self.current
        if not self._playing or current is None or current.source != 'online':
            return (0.0,) * bands
        try:
            values = tuple(self.controller.player.spectrum_levels(bands))
        except Exception:
            return (0.0,) * bands
        return values if len(values) == bands else (0.0,) * bands

    def tick(self, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        future = self._prepare_future
        if future is not None and future.done():
            self._finish_prepare(now=now)
        if not self._playing or now < self._busy_grace_until:
            return
        current = self.current
        if current is None:
            self._playing = False
            return
        try:
            busy = bool(self.controller.player.is_playing())
        except Exception:
            busy = True
        if not busy:
            self._advance()

    def close(self) -> None:
        self._cancel_prepare()
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
