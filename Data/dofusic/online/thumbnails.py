from __future__ import annotations

from contextlib import contextmanager
import io
import os
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

from dofusic.online.models import OnlineTrack


_MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024


def _fetch_url(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        },
    )
    with urlopen(request, timeout=max(1.0, float(timeout))) as response:
        return response.read(_MAX_DOWNLOAD_BYTES + 1)


class ThumbnailService:
    """Small, bounded thumbnail cache independent from Tkinter.

    Network access and Pillow decoding happen on worker threads. The UI receives
    PNG bytes and creates Tk PhotoImage objects only on the Tk thread.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        fetcher=None,
        max_items: int = 48,
        timeout: float = 4.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fetcher = fetcher or _fetch_url
        self.max_items = max(16, int(max_items))
        self.timeout = max(1.0, float(timeout))
        self._locks_guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    @property
    def active_lock_count(self) -> int:
        with self._locks_guard:
            return len(self._locks)

    @contextmanager
    def _key_lock(self, key: str):
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

    def _path(self, track: OnlineTrack, size: tuple[int, int]) -> Path:
        width, height = size
        return self.cache_dir / f'{track.video_id}_{width}x{height}.png'

    @staticmethod
    def _valid_png(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size >= 32
        except OSError:
            return False

    def _prune(self, *, keep: Path) -> None:
        try:
            files = [p for p in self.cache_dir.glob('*.png') if p.is_file()]
            if len(files) <= self.max_items:
                return
            files.sort(key=lambda p: p.stat().st_mtime)
            extra = len(files) - self.max_items
            for path in files:
                if extra <= 0:
                    break
                try:
                    if path.resolve() == keep.resolve():
                        continue
                    path.unlink()
                    extra -= 1
                except OSError:
                    continue
        except OSError:
            return

    def _decode_png(self, raw: bytes, size: tuple[int, int]) -> bytes:
        if not raw or len(raw) > _MAX_DOWNLOAD_BYTES:
            return b''
        width, height = (max(16, int(size[0])), max(9, int(size[1])))
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image = image.convert('RGB')
                image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format='PNG', optimize=True)
                return output.getvalue()
        except Exception:
            return b''

    def get_png(self, track: OnlineTrack, *, size: tuple[int, int] = (112, 63)) -> bytes:
        target = self._path(track, size)
        key = target.name
        with self._key_lock(key):
            if self._valid_png(target):
                try:
                    os.utime(target, None)
                    return target.read_bytes()
                except OSError:
                    pass

            primary = str(track.thumbnail_url or '').strip()
            fallback = f'https://i.ytimg.com/vi/{track.video_id}/hqdefault.jpg'
            urls = []
            for url in (primary, fallback):
                if url and url not in urls:
                    urls.append(url)

            encoded = b''
            for url in urls:
                try:
                    encoded = self._decode_png(self._fetcher(url, self.timeout), size)
                except Exception:
                    encoded = b''
                if encoded:
                    break
            if not encoded:
                return b''

            temporary = target.with_suffix('.tmp')
            try:
                temporary.write_bytes(encoded)
                os.replace(temporary, target)
                os.utime(target, (time.time(), time.time()))
            finally:
                try:
                    if temporary.exists():
                        temporary.unlink()
                except OSError:
                    pass
            self._prune(keep=target)
            return encoded
