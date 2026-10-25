"""Asynchronous FFmpeg codec fallback with generation-based cancellation."""
from __future__ import annotations

import os
import queue
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class AudioDecodeMixin:
    """Preparation of unsupported local audio formats off the UI thread."""

    @staticmethod
    def _discard_fallback(path: Path) -> None:
        """Locked temporary files must not interrupt playback or shutdown."""
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # The stale-file sweep can retry once external file locks clear.
            pass

    def _cleanup_playback_fallback(self, *, except_path: Path | None = None) -> None:
        old = self._playback_fallback_path
        if old is None or (except_path is not None and old == except_path):
            return
        self._playback_fallback_path = None
        self._discard_fallback(old)

    def _terminate_decode_process(self) -> None:
        with self._decode_process_lock:
            process = self._decode_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _cleanup_stale_fallbacks(folder: Path, *, now: float | None = None) -> int:
        """Reclaim orphaned audio conversions from earlier interrupted runs.

        Conversions still in use are deliberately protected for seven days,
        allowing overlapping instances to play without losing their files.
        Only the app's own regular `.ogg` files in its private temp folder
        are eligible; symlinks and unrelated user files are never followed.
        """
        cutoff = (time.time() if now is None else float(now)) - 7 * 24 * 3600
        removed = 0
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if not (entry.name.startswith('dofusic_') and entry.name.endswith('.ogg')):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                            os.unlink(entry.path)
                            removed += 1
                    except OSError:
                        # A file may belong to a concurrent process, or have
                        # disappeared while scanning the directory.
                        continue
        except OSError:
            pass
        return removed

    @staticmethod
    def _conversion_timeout_seconds(source: Path) -> float:
        """Scale FFmpeg's watchdog to file size, keeping a strict upper bound.

        Small music files retain the original 15s limit, while large files on
        slow drives can finish without prematurely aborting valid playback.
        """
        try:
            size = source.stat().st_size
        except OSError:
            return 15.0
        return min(120.0, max(15.0, size / (512 * 1024)))

    def _transcode_playback_fallback(self, source: Path) -> Path:
        """Bounded conversion. Called ONLY by the preparation thread, never Tk."""
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise RuntimeError('FFmpeg Dofusic indisponible') from exc

        folder = Path(tempfile.gettempdir()) / 'DofusicAudioFallback'
        folder.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_fallbacks(folder)
        fd, name = tempfile.mkstemp(prefix='dofusic_', suffix='.ogg', dir=folder)
        os.close(fd)
        target = Path(name)
        target.unlink(missing_ok=True)
        command = [
            str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(source), '-vn', '-map_metadata', '-1',
            '-c:a', 'libvorbis', '-q:a', '4', str(target),
        ]
        kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                      stdin=subprocess.DEVNULL, text=True)
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        timeout_seconds = self._conversion_timeout_seconds(source)
        process = None
        completed = False
        try:
            if self._decode_stop.is_set():
                raise RuntimeError('Conversion annulée : fermeture du lecteur')
            process = subprocess.Popen(command, **kwargs)
            with self._decode_process_lock:
                self._decode_process = process
            if self._decode_stop.is_set():
                self._terminate_decode_process()
            _, stderr = process.communicate(timeout=timeout_seconds)
            if self._decode_stop.is_set():
                raise RuntimeError('Conversion annulée : fermeture du lecteur')
            if process.returncode != 0 or not target.is_file() or target.stat().st_size <= 0:
                lines = (stderr or '').strip().splitlines()
                detail = lines[-1] if lines else f'code {process.returncode}'
                raise RuntimeError(f'Conversion FFmpeg impossible : {detail}')
            completed = True
            return target
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'Conversion FFmpeg interrompue : délai de {timeout_seconds:g} secondes dépassé') from exc
        except OSError as exc:
            raise RuntimeError(f'Conversion FFmpeg interrompue : {exc}') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
                try:
                    process.communicate(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                with self._decode_process_lock:
                    if self._decode_process is process:
                        self._decode_process = None
            if not completed or self._decode_stop.is_set():
                self._discard_fallback(target)

    def _queue_decode_job(self, job: tuple[Path, bool, int] | None) -> None:
        try:
            self._decode_jobs.put_nowait(job)
        except queue.Full:
            try:
                self._decode_jobs.get_nowait()
            except queue.Empty:
                pass
            self._decode_jobs.put_nowait(job)

    def _decode_loop(self) -> None:
        while not self._decode_stop.is_set():
            try:
                job = self._decode_jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                break
            source, loop, generation = job
            if generation != self._decode_generation:
                continue
            converted = None
            error = None
            try:
                converted = self._transcode_playback_fallback(source)
            except Exception as exc:
                error = str(exc)
            # Obsolete results may not leak temporary files or replace new music.
            if self._decode_stop.is_set() or generation != self._decode_generation:
                if converted is not None:
                    self._discard_fallback(converted)
            else:
                self._decode_results.put((source, loop, generation, converted, error))

    def _prepare_fallback(self, target: Path, loop: bool) -> None:
        self._preparing = target
        self._preparing_loop = loop
        self._decode_generation += 1
        self._terminate_decode_process()
        self._queue_decode_job((target, loop, self._decode_generation))
        if self._decode_thread is None or not self._decode_thread.is_alive():
            self._decode_stop.clear()
            self._decode_thread = threading.Thread(
                target=self._decode_loop, name='DofusicAudioPrepare', daemon=True
            )
            self._decode_thread.start()

    def _cancel_decode(self) -> None:
        self._decode_generation += 1
        self._preparing = None
        self._preparing_loop = None
        self._terminate_decode_process()
