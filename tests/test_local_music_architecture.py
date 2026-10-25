"""Non-régressions : musique locale, file, configuration et distribution portable."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.session import MusicSession
from dofusic.config import AppConfig, load_config


class _Player:
    def __init__(self) -> None:
        self.busy = True

    def is_playing(self) -> bool:
        return self.busy


class _Controller:
    def __init__(self, tracks):
        self.music_library = SimpleNamespace(
            all_tracks=tuple(tracks),
            scan=lambda: None,
        )
        self.player = _Player()
        self.calls = []
        self.resume_count = 0

    def play_manual_local_track(self, path, title, *, loop=False):
        self.calls.append((Path(path), title, loop))
        self.player.busy = True
        return True

    def resume_local_audio(self):
        self.resume_count += 1
        return True


def test_manual_local_playback_and_queue_end_return_to_dofus(tmp_path):
    a, b = tmp_path / 'Incarnam.opus', tmp_path / 'Astrub.mp3'
    a.touch()
    b.touch()
    controller = _Controller((a, b))
    session = MusicSession(controller, AppConfig())

    assert session.enqueue_local_track(b)
    assert controller.calls == []
    assert session.play_local_track(a)
    assert controller.calls == [(a, 'Incarnam', False)]
    controller.player.busy = False
    session.tick(now=session._busy_grace_until + 1)
    assert controller.calls[-1] == (b, 'Astrub', False)
    assert not session.pending
    controller.player.busy = False
    session.tick(now=session._busy_grace_until + 1)
    assert controller.resume_count == 1
    assert session.current is None


def test_local_repeat_and_manual_resume(tmp_path):
    track = tmp_path / 'Combat.ogg'
    track.touch()
    controller = _Controller((track,))
    session = MusicSession(controller, AppConfig())
    session.set_repeat(True)
    assert session.play_local_track(track)
    controller.player.busy = False
    session.tick(now=session._busy_grace_until + 1)
    assert len(controller.calls) == 2
    assert session.current.title == 'Combat'
    session.resume_dofus_music()
    assert controller.resume_count == 1
    assert not session.pending
    assert not session.repeat_current
    assert session.current is None


def test_local_library_rejects_non_library_files(tmp_path):
    local, external = tmp_path / 'Incarnam.ogg', tmp_path / 'hors_bibliotheque.webm'
    local.touch()
    external.touch()
    controller = _Controller((local,))
    session = MusicSession(controller, AppConfig())
    assert not session.play_local_track(external)
    assert not session.enqueue_local_track(external)
    assert controller.calls == []


def test_queue_reorder_and_volume_are_local_only(tmp_path):
    files = [tmp_path / f'zone-{i}.mp3' for i in range(3)]
    for p in files:
        p.touch()
    config = AppConfig()
    session = MusicSession(_Controller(files), config)
    for p in files:
        assert session.enqueue_local_track(p)
    assert session.remove_pending(1).local_path == files[1]
    assert tuple(x.local_path for x in session.pending) == (files[0], files[2])
    session.clear_queue()
    assert session.pending == ()
    assert session.set_volume(150) == 100
    session.set_muted(True)
    assert config.mute is True


def test_older_online_config_migrates_without_losing_local_volume_and_theme(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(
        '{"config_version":26,"volume":61,"mute":true,'
        '"theme":"emerald","online_music_enabled":true,'
        '"online_cache_mb":999,"show_visualizer":true}', encoding='utf-8',
    )
    config = load_config(path)
    assert config.volume == 61
    assert config.mute is True
    assert config.theme == 'emerald'
    assert config.show_visualizer is True
    assert config.config_version == 27
    assert not any(name.startswith('online_') for name in config.__dataclass_fields__)


def test_online_code_and_media_engines_are_removed_from_sources():
    root = Path(__file__).resolve().parents[1]
    assert not (root / 'Data/dofusic/online').exists()
    req = (root / 'Data/requirements-lock.txt').read_text(encoding='utf-8')
    assert 'yt-dlp' not in req
    spec = (root / 'Data/Dofusic.spec').read_text(encoding='utf-8')
    assert 'QUICKJS_BINARY' not in spec
    assert 'imageio_ffmpeg' in (root / 'Data/main.py').read_text(encoding='utf-8')
    builder = (root / 'Data/tools/build_portable.py').read_text(encoding='utf-8')
    assert 'prepare_quickjs_runtime' not in builder
    assert 'qjs.exe' in builder  # Explicit validation against obsolete binaries
