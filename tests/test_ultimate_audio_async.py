"""Nonblocking audio transitions, FFmpeg races and owner-thread isolation."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from dofusic.audio.player import MusicPlayer
from dofusic.audio.session import MusicSession


class FakeMusic:
    def __init__(self, *, fail_source=None):
        self.events = []
        self.busy = False
        self.fail_source = fail_source

    def load(self, path):
        self.events.append(('load', path))
        if path == str(self.fail_source):
            raise ValueError('Unsupported codec')

    def play(self, loops, fade_ms):
        self.events.append(('play', loops, fade_ms))
        self.busy = True

    def stop(self):
        self.events.append(('stop',))
        self.busy = False

    def fadeout(self, _ms):
        raise AssertionError('fadeout() is blocking and must never be called')

    def set_volume(self, volume):
        self.events.append(('volume', volume))

    def get_busy(self):
        return self.busy


def fake_player(*, fade_ms=650, fail_source=None):
    player = MusicPlayer(fade_ms=fade_ms, spectrum_enabled=False, normalize_loudness=False)
    music = FakeMusic(fail_source=fail_source)
    player._ready = True
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
    player._start_track_analysis = lambda *_a: None
    return player, music


def finish_fake_player(player):
    player._ready = False
    player.close()


def test_transition_is_nonblocking_and_advances_only_during_tick(monkeypatch):
    from dofusic.audio import player as source
    moment = [10.0]
    monkeypatch.setattr(source.time, 'monotonic', lambda: moment[0])
    player, music = fake_player(fade_ms=650)
    first, second = Path('Astrub.opus'), Path('Combat.opus')
    assert player.play(first)
    assert player.play(second)
    assert player.current == first
    assert player._pending_transition[0] == second
    assert [x for x in music.events if x[0] == 'load'] == [('load', str(first))]
    moment[0] = 10.3
    player.tick(now=10.3)
    assert player.current == first
    assert any(e[0] == 'volume' and 0 < e[1] < 0.7 for e in music.events)
    moment[0] = 10.7
    player.tick(now=10.7)
    assert player.current == second
    assert [e[1] for e in music.events if e[0] == 'load'] == [str(first), str(second)]
    assert player._pending_transition is None
    finish_fake_player(player)


def test_rapid_ocr_change_cancels_pending_transition_without_reloading_old_track(monkeypatch):
    from dofusic.audio import player as source
    monkeypatch.setattr(source.time, 'monotonic', lambda: 15.0)
    player, music = fake_player()
    first, second = Path('donjon.opus'), Path('combat.opus')
    assert player.play(first)
    assert player.play(second)
    assert player.play(first)
    assert player._pending_transition is None
    assert player.current == first
    assert [e[0] for e in music.events].count('load') == 1
    finish_fake_player(player)


def test_stop_with_fade_uses_tick_and_never_blocks(monkeypatch):
    from dofusic.audio import player as source
    monkeypatch.setattr(source.time, 'monotonic', lambda: 20.0)
    player, music = fake_player()
    player.play(Path('zone.opus'))
    player.stop(fade_ms=700)
    assert music.busy
    player.tick(now=20.5)
    assert music.busy
    player.tick(now=20.8)
    assert not music.busy
    assert player.current is None
    finish_fake_player(player)


def test_async_decode_is_loaded_only_from_main_thread(tmp_path):
    source = tmp_path / 'unhandled.wma'
    converted = tmp_path / 'converted.ogg'
    converted.write_bytes(b'converted data')
    player, music = fake_player(fail_source=source)
    owner = threading.get_ident()
    conversion_threads = []

    def convert(_source):
        conversion_threads.append(threading.get_ident())
        return converted

    player._transcode_playback_fallback = convert
    assert player.play(source)
    assert player.current is None
    assert player.is_preparing(source)
    player._decode_thread.join(timeout=2)
    assert [e for e in music.events if e[0] == 'load'] == [('load', str(source))]
    player.tick()
    assert player.current == source
    assert ('load', str(converted)) in music.events
    assert conversion_threads and conversion_threads[0] != owner
    finish_fake_player(player)
    assert not converted.exists()


def test_obsolete_conversion_cannot_override_newer_music(tmp_path):
    source = tmp_path / 'old.wma'
    good = tmp_path / 'new.opus'
    converted = tmp_path / 'old-converted.ogg'
    converted.write_bytes(b'audio')
    started = threading.Event()
    release = threading.Event()
    player, music = fake_player(fail_source=source)

    def convert(_path):
        started.set()
        assert release.wait(3)
        return converted

    player._transcode_playback_fallback = convert
    assert player.play(source)
    assert started.wait(2)
    assert player.play(good)
    assert player.current == good
    release.set()
    player._decode_thread.join(timeout=2)
    player.tick()
    assert player.current == good
    assert not converted.exists()
    assert ('load', str(converted)) not in music.events
    finish_fake_player(player)


def test_volume_update_from_worker_waits_for_ui_tick():
    player, music = fake_player()
    player._audio_owner_thread = threading.get_ident()
    worker = threading.Thread(target=lambda: player.set_volume(45))
    worker.start()
    worker.join(timeout=2)
    assert player._volume_dirty
    assert not music.events
    player.tick()
    assert ('volume', 0.45) in music.events
    finish_fake_player(player)


def test_manual_queue_does_not_skip_while_ffmpeg_is_preparing(tmp_path):
    player = SimpleNamespace(is_preparing=lambda: True, is_playing=lambda: False)
    controller = SimpleNamespace(player=player)
    session = MusicSession(controller, SimpleNamespace())
    session._playing = True
    session._busy_grace_until = -1
    session.current = SimpleNamespace(title='a')
    session.tick(now=10.0)
    assert session.current.title == 'a'
    assert session._playing


def test_closing_during_conversion_discards_temporary_result(tmp_path):
    source = tmp_path / 'not-supported.wma'
    converted = tmp_path / 'should-be-removed.ogg'
    converted.write_bytes(b'partial output')
    started = threading.Event()
    release = threading.Event()
    player, _music = fake_player(fail_source=source)

    def conversion(_path):
        started.set()
        assert release.wait(3)
        return converted

    player._transcode_playback_fallback = conversion
    assert player.play(source)
    assert started.wait(2)
    player._ready = False  # Fake mixer does not implement quit().
    player.close()
    release.set()
    player._decode_thread.join(timeout=2)
    assert not converted.exists()
    assert not player.is_preparing()


def test_runtime_data_and_distributed_music_affect_build_fingerprint(tmp_path):
    from Data.tools.build_portable import _source_fingerprint
    root = tmp_path
    (root / 'Data').mkdir()
    db = root / 'Data' / 'dofus_data.sqlite'
    alias = root / 'Data' / 'place_aliases.json'
    db.write_bytes(b'old')
    alias.write_text('{}', encoding='utf-8')
    music = root / 'Musiques'
    music.mkdir()
    (music / 'personal.opus').write_bytes(b'private')
    initial, count = _source_fingerprint(root)
    assert count == 3
    db.write_bytes(b'new')
    assert _source_fingerprint(root)[0] != initial
    new, _ = _source_fingerprint(root)
    (music / 'personal.opus').write_bytes(b'changed')
    assert _source_fingerprint(root)[0] != new
    new, _ = _source_fingerprint(root)
    alias.write_text('{"location":"new"}', encoding='utf-8')
    assert _source_fingerprint(root)[0] != new
