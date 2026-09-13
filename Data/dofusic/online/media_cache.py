from __future__ import annotations

from contextlib import contextmanager
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from dofusic.audio.library import AUDIO_EXTS
from dofusic.online.errors import MediaCacheError, MediaErrorCode
from dofusic.online.models import OnlineTrack
from dofusic.online.retrieval import YouTubeAudioFetcher


_LOG = logging.getLogger('dofusic.music.media')
_INVALID_WINDOWS_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_STEMS = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL'}
    | {f'COM{index}' for index in range(1, 10)}
    | {f'LPT{index}' for index in range(1, 10)}
)
_MAX_FILENAME_STEM_LENGTH = 180
_CACHE_MEDIA_EXTS = frozenset(AUDIO_EXTS | {'.webm', '.opus', '.aac', '.mp4', '.mka'})
_MAX_ONLINE_DURATION_SECONDS = 2 * 60 * 60


def _clean_filename_text(value: str) -> str:
    text = _INVALID_WINDOWS_FILENAME.sub('', (value or '').strip())
    return re.sub(r'\s+', ' ', text).strip(' .')[:_MAX_FILENAME_STEM_LENGTH].rstrip(' .')


def sanitize_filename(value: str, *, fallback: str = 'Musique') -> str:
    text = _clean_filename_text(value) or _clean_filename_text(fallback) or 'Musique'
    if text.split('.', 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        text = f'_{text}'
    return text[:_MAX_FILENAME_STEM_LENGTH].rstrip(' .') or 'Musique'


class MediaCache:
    """Single authoritative yt-dlp/FFmpeg/cache boundary.

    A per-video lock guarantees one producer at a time. Lock entries are reference
    counted and removed when the last waiter leaves, so a long Dofusic session does
    not retain one lock object for every video ever touched.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        music_dir: Path | str,
        *,
        cache_mb: int = 128,
        downloader=None,
        transcoder=None,
        fetcher=None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.music_dir = Path(music_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.music_dir.mkdir(parents=True, exist_ok=True)
        self.cache_bytes = max(64, int(cache_mb)) * 1024 * 1024
        self._downloader = downloader
        self._transcoder = transcoder
        self._fetcher = fetcher or YouTubeAudioFetcher(self.cache_dir)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}
        self._prune_lock = threading.Lock()


    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        total = max(0, int(total_seconds))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f'{hours:d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:d}:{seconds:02d}'

    def validate_track(self, track: OnlineTrack) -> None:
        duration = max(0, int(getattr(track, 'duration_seconds', 0) or 0))
        if duration > _MAX_ONLINE_DURATION_SECONDS:
            raise MediaCacheError(
                f'Vidéo trop longue pour la lecture directe Dofusic : {self._format_duration(duration)} '
                f'(limite {self._format_duration(_MAX_ONLINE_DURATION_SECONDS)}). Choisissez une version plus courte.',
                code=MediaErrorCode.TOO_LONG,
            )

    @property
    def active_lock_count(self) -> int:
        with self._locks_guard:
            return len(self._locks)

    @contextmanager
    def _video_lock(self, video_id: str):
        key = str(video_id or '')
        with self._locks_guard:
            entry = self._locks.get(key)
            if entry is None:
                lock, users = threading.Lock(), 0
            else:
                lock, users = entry
            self._locks[key] = (lock, users + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._locks_guard:
                current = self._locks.get(key)
                if current is not None and current[0] is lock:
                    users = current[1] - 1
                    if users <= 0:
                        self._locks.pop(key, None)
                    else:
                        self._locks[key] = (lock, users)

    @staticmethod
    def _is_valid_file(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _transcode_to_opus(self, source: Path, target: Path) -> Path:
        if self._transcoder is not None:
            produced = Path(self._transcoder(source, target))
            if not self._is_valid_file(produced):
                raise MediaCacheError('La conversion audio n’a produit aucun fichier', code=MediaErrorCode.TOOLING)
            return produced
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:  # pragma: no cover - packaging/runtime dependency
            raise MediaCacheError('Le convertisseur FFmpeg Dofusic est indisponible', code=MediaErrorCode.TOOLING) from exc

        temporary = target.with_name(target.stem + '.part' + target.suffix)
        try:
            temporary.unlink(missing_ok=True)
            command = [
                str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-i', str(source), '-vn', '-map_metadata', '-1',
                '-c:a', 'libopus', '-b:a', '64k', '-vbr', 'on', '-compression_level', '10',
                str(temporary),
            ]
            kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.PIPE, 'text': True, 'check': False}
            if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(command, **kwargs)
            if result.returncode != 0 or not self._is_valid_file(temporary):
                lines = (result.stderr or '').strip().splitlines()
                detail = lines[-1] if lines else f'code {result.returncode}'
                raise MediaCacheError(f'Conversion audio impossible : {detail}', code=MediaErrorCode.TOOLING)
            os.replace(temporary, target)
            return target
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_playable_cache(self, source: Path, video_id: str) -> Path:
        source = Path(source)
        target = self.cache_dir / f'{video_id}.opus'
        try:
            same_file = source.resolve() == target.resolve()
        except OSError:
            same_file = source == target
        if same_file and self._is_valid_file(target):
            return target
        if self._is_valid_file(target):
            return target
        converted = self._transcode_to_opus(source, target)
        if converted.resolve() != source.resolve():
            try:
                source.unlink()
            except OSError:
                pass
        return converted

    def _cached_path(self, video_id: str) -> Path | None:
        key = str(video_id or '')
        final = self.cache_dir / f'{key}.opus'
        if self._is_valid_file(final):
            try:
                os.utime(final, None)
            except OSError:
                pass
            return final
        for path in sorted(self.cache_dir.glob(f'{key}.*')):
            # Accept only final cache artifacts named exactly <video_id>.<ext>.
            # Interrupted transcodes such as <video_id>.part.opus must never be
            # mistaken for a valid cache hit after a crash or forced shutdown.
            if path.stem != key:
                continue
            if self._is_valid_file(path) and path.suffix.lower() in _CACHE_MEDIA_EXTS:
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                return path
        return None

    def ensure_cached(self, track: OnlineTrack) -> Path:
        _LOG.info('[MUSIC][MEDIA] cache start id=%s title=%r', track.video_id, track.title)
        try:
            self.validate_track(track)
            with self._video_lock(track.video_id):
                cached = self._cached_path(track.video_id)
                if cached is None:
                    if self._downloader is not None:
                        cached = Path(self._downloader(track, self.cache_dir))
                        if not self._is_valid_file(cached):
                            raise MediaCacheError('Le téléchargement n’a produit aucun fichier')
                    else:
                        cached = Path(self._fetcher.fetch(track))
                playable = self._ensure_playable_cache(cached, track.video_id)
                self.prune_cache(keep=playable)
        except MediaCacheError as exc:
            if exc.code in {MediaErrorCode.BOT_CHALLENGE, MediaErrorCode.AGE_RESTRICTED, MediaErrorCode.AUTH_FAILED, MediaErrorCode.TOO_LONG}:
                _LOG.warning('[MUSIC][MEDIA] cache blocked id=%s code=%s message=%s', track.video_id, exc.code.value, exc)
            else:
                _LOG.exception('[MUSIC][MEDIA] cache failed id=%s', track.video_id)
            raise
        except Exception:
            _LOG.exception('[MUSIC][MEDIA] cache failed id=%s', track.video_id)
            raise
        _LOG.info('[MUSIC][MEDIA] cache done id=%s path=%s', track.video_id, playable)
        return playable

    def prune_cache(self, *, keep: Path | None = None) -> None:
        # Cache production can run concurrently for different videos. Protect
        # every video that currently owns/waits on its producer lock so pruning
        # cannot delete another operation's finished source between download and
        # playback/transcode.
        with self._locks_guard:
            active_video_ids = {key for key, (_lock, users) in self._locks.items() if users > 0}
        with self._prune_lock:
            try:
                files = [path for path in self.cache_dir.iterdir() if path.is_file()]
            except OSError:
                return
            sized: list[tuple[Path, int, float]] = []
            total = 0
            for path in files:
                try:
                    stat = path.stat()
                except OSError:
                    continue
                sized.append((path, stat.st_size, stat.st_mtime))
                total += stat.st_size
            if total <= self.cache_bytes:
                return
            keep_resolved = None
            try:
                keep_resolved = keep.resolve() if keep is not None and keep.exists() else None
            except OSError:
                pass
            for path, size, _mtime in sorted(sized, key=lambda item: item[2]):
                if total <= self.cache_bytes:
                    break
                try:
                    if keep_resolved is not None and path.resolve() == keep_resolved:
                        continue
                    if any(path.name.startswith(video_id + '.') for video_id in active_video_ids):
                        continue
                    path.unlink()
                    total -= size
                except OSError:
                    continue

    def save_to_library(self, track: OnlineTrack, desired_name: str) -> Path:
        _LOG.info('[MUSIC][MEDIA] save start id=%s title=%r', track.video_id, track.title)
        source = self.ensure_cached(track)
        stem = sanitize_filename(desired_name, fallback=track.title or 'Musique')
        target = self.music_dir / f'{stem}{source.suffix.lower()}'
        counter = 2
        while target.exists():
            target = self.music_dir / f'{stem} ({counter}){source.suffix.lower()}'
            counter += 1
        shutil.copy2(source, target)
        try:
            os.utime(source, None)
        except OSError:
            pass
        _LOG.info('[MUSIC][MEDIA] save done id=%s path=%s', track.video_id, target)
        return target
