"""Audio worker, cleanup and streaming regressions from the 2.0.0 audit."""
from __future__ import annotations

import queue
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.audio.player import MusicPlayer
from dofusic.audio.spectrum import compute_spectrum_chunks


class _Music:
    def __init__(self):
        self.loaded = None
        self.busy = False

    def load(self, path):
        self.loaded = Path(path)

    def play(self, loops, fade_ms):
        self.busy = True

    def stop(self):
        self.busy = False

    def set_volume(self, value):
        pass

    def get_busy(self):
        return self.busy


def _ready_player():
    music = _Music()
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=False)
    player._ready = True
    player._pygame = SimpleNamespace(
        mixer=SimpleNamespace(music=music, quit=lambda: None),
    )
    return player, music


def _lock_file(monkeypatch, path):
    unlink = Path.unlink

    def guarded_unlink(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError('temporary file is held by another process')
        return unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', guarded_unlink)


def test_locked_obsolete_conversion_does_not_kill_worker_or_lose_next_result(tmp_path, monkeypatch):
    player = MusicPlayer(spectrum_enabled=False)
    source, latest = tmp_path / 'old.wma', tmp_path / 'new.wma'
    obsolete, ready = tmp_path / 'locked.ogg', tmp_path / 'ready.ogg'
    obsolete.write_bytes(b'old')
    ready.write_bytes(b'new')
    _lock_file(monkeypatch, obsolete)

    def convert(path):
        if path == source:
            player._decode_generation += 1
            player._decode_jobs.put_nowait((latest, False, player._decode_generation))
            return obsolete
        player._decode_jobs.put_nowait(None)
        return ready

    monkeypatch.setattr(player, '_transcode_playback_fallback', convert)
    player._decode_jobs.put_nowait((source, True, player._decode_generation))
    player._decode_loop()
    assert player._decode_results.get_nowait() == (latest, False, 1, ready, None)
    assert player._decode_results.empty()


def test_locked_obsolete_result_does_not_block_loading_current_music(tmp_path, monkeypatch):
    player, music = _ready_player()
    source = tmp_path / 'new.wma'
    obsolete, ready = tmp_path / 'locked.ogg', tmp_path / 'ready.ogg'
    obsolete.write_bytes(b'old')
    ready.write_bytes(b'new')
    _lock_file(monkeypatch, obsolete)
    player._decode_generation = 2
    player._preparing = source
    player._decode_results.put((tmp_path / 'old.wma', True, 1, obsolete, None))
    player._decode_results.put((source, False, 2, ready, None))
    try:
        player.tick()
        assert player.current == source
        assert music.loaded == ready
        assert music.busy
        assert not player.is_preparing()
    finally:
        player.close()


def test_locked_decode_result_does_not_abort_player_shutdown(tmp_path, monkeypatch):
    player, music = _ready_player()
    obsolete, active = tmp_path / 'locked.ogg', tmp_path / 'active.ogg'
    obsolete.write_bytes(b'old')
    active.write_bytes(b'playing')
    _lock_file(monkeypatch, obsolete)
    player.current = tmp_path / 'playing.wma'
    player._playback_fallback_path = active
    music.busy = True
    player._decode_results.put((Path('old.wma'), True, 0, obsolete, None))

    player.close()

    assert player._analysis_stop.is_set()
    assert player._loudness_stop.is_set()
    assert not player._ready
    assert not music.busy
    assert player.current is None
    assert not active.exists()


def test_locked_partial_conversion_preserves_timeout_error(tmp_path, monkeypatch):
    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path))
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg'))
    unlink = Path.unlink

    def guarded_unlink(path, *args, **kwargs):
        if path.is_file() and path.read_bytes() == b'partial':
            raise PermissionError('locked output')
        return unlink(path, *args, **kwargs)

    class StalledProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            self.target = Path(command[-1])

        def communicate(self, timeout):
            if self.returncode is None:
                self.target.write_bytes(b'partial')
                raise subprocess.TimeoutExpired('ffmpeg', timeout)
            return None, ''

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(Path, 'unlink', guarded_unlink)
    monkeypatch.setattr(subprocess, 'Popen', StalledProcess)
    player = MusicPlayer(spectrum_enabled=False)
    with pytest.raises(RuntimeError, match='délai de 15 secondes dépassé'):
        player._transcode_playback_fallback(tmp_path / 'source.wma')
    assert player._decode_process is None


def test_cancelled_queued_conversion_does_not_start_ffmpeg(monkeypatch):
    player = MusicPlayer(spectrum_enabled=False)
    player._decode_generation = 2
    player._decode_jobs = queue.Queue()
    player._decode_jobs.put((Path('cancelled.wma'), True, 1))
    player._decode_jobs.put(None)
    converted = []
    monkeypatch.setattr(player, '_transcode_playback_fallback', lambda path: converted.append(path))

    player._decode_loop()

    assert converted == []
    assert player._decode_results.empty()


def test_cancelled_queued_spectrum_does_not_start_ffmpeg(monkeypatch):
    player, _music = _ready_player()
    player.current = Path('same-track.opus')
    player._spectrum_generation = 2
    player._analysis_jobs = queue.Queue()
    player._analysis_jobs.put((player.current, 1))
    player._analysis_jobs.put(None)
    started = []
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(get_ffmpeg_exe=lambda: 'ffmpeg'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: started.append(args))

    player._spectrum_worker_loop()

    assert started == []
    assert player._spectrum_frames.size == 0
    player.close()


@pytest.mark.parametrize('sample_rate,fps', [(16000, 15), (44100, 15), (32000, 5), (8000, 15)])
@pytest.mark.parametrize('chunk_size', [13, 1024, 977])
def test_streaming_spectrum_keeps_exact_timing_across_chunk_boundaries(sample_rate, fps, chunk_size):
    samples = np.random.default_rng(401).uniform(-0.9, 0.9, 12000).astype(np.float32)
    whole, whole_count = compute_spectrum_chunks((samples,), sample_rate, fps=fps)
    chunked, chunked_count = compute_spectrum_chunks(
        (samples[start:start + chunk_size] for start in range(0, len(samples), chunk_size)),
        sample_rate, fps=fps,
    )

    assert whole_count == chunked_count == 12000
    np.testing.assert_allclose(chunked, whole, atol=2e-6, rtol=1e-6)
