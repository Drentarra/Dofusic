from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
import sys
import time
from typing import Callable

from dofusic.online.errors import MediaCacheError, MediaErrorCode
from dofusic.online.models import OnlineTrack


_LOG = logging.getLogger('dofusic.music.retrieval')


def resolve_quickjs_executable() -> Path:
    """Resolve the tiny QuickJS runtime used by yt-dlp/EJS.

    Frozen releases always use the bundled qjs.exe. Source/development runs can
    use the builder-provided environment override or a qjs.exe already on PATH.
    """
    frozen_root = getattr(sys, '_MEIPASS', None)
    if frozen_root:
        bundled = Path(frozen_root) / 'js' / 'qjs.exe'
        if bundled.is_file():
            return bundled

    configured = os.environ.get('DOFUSIC_QJS_BINARY', '').strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path

    discovered = shutil.which('qjs') or shutil.which('qjs.exe')
    if discovered:
        path = Path(discovered)
        if path.is_file():
            return path

    raise MediaCacheError('Runtime JavaScript QuickJS introuvable', code=MediaErrorCode.TOOLING)


def classify_retrieval_error(exc: Exception) -> MediaErrorCode:
    text = str(exc).casefold()
    if 'confirm your age' in text or 'age-restricted' in text or 'age restricted' in text:
        return MediaErrorCode.AGE_RESTRICTED
    if any(value in text for value in ('sign in to confirm you', 'not a bot', 'login_required', 'login required')):
        return MediaErrorCode.BOT_CHALLENGE
    if any(value in text for value in ('timed out', 'timeout', 'network', 'connection', 'socket', 'http error 5')):
        return MediaErrorCode.NETWORK
    if any(value in text for value in ('video unavailable', 'not available', 'private video', 'removed by the uploader')):
        return MediaErrorCode.UNAVAILABLE
    return MediaErrorCode.UNKNOWN


def _message_for_error(code: MediaErrorCode, exc: Exception) -> str:
    if code is MediaErrorCode.AGE_RESTRICTED:
        return 'Vidéo restreinte par YouTube (vérification d’âge requise)'
    if code is MediaErrorCode.BOT_CHALLENGE:
        return 'YouTube bloque temporairement cette vidéo (vérification anti-bot)'
    if code is MediaErrorCode.NETWORK:
        return f'Connexion YouTube impossible : {exc}'
    if code is MediaErrorCode.UNAVAILABLE:
        return 'Cette vidéo YouTube est indisponible'
    return f'Téléchargement audio impossible : {exc}'


class YouTubeAudioFetcher:
    """Anonymous yt-dlp retrieval policy for one online Play action."""

    retry_backoff_sec = 0.8

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        runner: Callable[[OnlineTrack, dict[str, object]], Path | str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._runner = runner or self._run_ytdlp
        self._sleep = sleep_fn

    def _options(self, video_id: str) -> dict[str, object]:
        return {
            'format': 'bestaudio/best',
            'outtmpl': str(self.cache_dir / f'{video_id}.source.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'overwrites': False,
            'continuedl': True,
            'socket_timeout': 12,
            'retries': 2,
        }

    def _run_ytdlp(self, track: OnlineTrack, options: dict[str, object]) -> Path:
        options = dict(options)
        options['js_runtimes'] = {'quickjs': {'path': str(resolve_quickjs_executable())}}
        try:
            import yt_dlp
            import yt_dlp_ejs  # noqa: F401
        except Exception as exc:  # pragma: no cover - packaging/runtime dependency
            raise MediaCacheError('Moteur YouTube Dofusic incomplet (yt-dlp / EJS)', code=MediaErrorCode.TOOLING) from exc

        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(track.youtube_url, download=True)
            if not result:
                raise MediaCacheError('Aucun flux audio trouvé', code=MediaErrorCode.UNAVAILABLE)
            try:
                candidate = Path(ydl.prepare_filename(result))
            except Exception:
                candidate = Path()

        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
        for path in sorted(self.cache_dir.glob(f'{track.video_id}.*')):
            try:
                if path.name.startswith(track.video_id + '.source.') and path.is_file() and path.stat().st_size > 0:
                    return path
            except OSError:
                continue
        raise MediaCacheError('Le flux audio téléchargé est introuvable', code=MediaErrorCode.UNAVAILABLE)

    def _attempt(self, track: OnlineTrack) -> Path:
        _LOG.info('[MUSIC][RETRIEVAL] attempt id=%s mode=anonymous', track.video_id)
        try:
            path = Path(self._runner(track, self._options(track.video_id)))
        except MediaCacheError:
            raise
        except Exception as exc:
            code = classify_retrieval_error(exc)
            raise MediaCacheError(_message_for_error(code, exc), code=code) from exc
        try:
            valid = path.is_file() and path.stat().st_size > 0
        except OSError:
            valid = False
        if not valid:
            raise MediaCacheError('Le téléchargement n’a produit aucun fichier', code=MediaErrorCode.UNAVAILABLE)
        return path

    def fetch(self, track: OnlineTrack) -> Path:
        last_error: MediaCacheError | None = None
        for attempt in range(2):
            try:
                return self._attempt(track)
            except MediaCacheError as exc:
                last_error = exc
                if exc.code not in {MediaErrorCode.NETWORK, MediaErrorCode.UNKNOWN} or attempt:
                    raise
                self._sleep(self.retry_backoff_sec)
        assert last_error is not None
        raise last_error
