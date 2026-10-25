"""Regression tests for OCR quiet mode, soundtrack transitions, and reproducible Windows ZIPs."""
from __future__ import annotations

import logging
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from Data.tools import build_portable as builder
from dofusic.app import DofusicController, _OCRRuntimeState
from dofusic.vision.change_gate import OCRChangeGate


class Worker:
    alive = True

    def __init__(self):
        self.calls = []

    def submit(self, image, *, captured_at):
        self.calls.append(captured_at)
        return len(self.calls)


def new_controller():
    control = DofusicController.__new__(DofusicController)
    control.logger = logging.getLogger('test.final_optimization')
    control.zone_worker = Worker()
    control.position_worker = Worker()
    control.zone_slot = _OCRRuntimeState()
    control.position_slot = _OCRRuntimeState()
    control.zone_change_gate = OCRChangeGate(idle_refresh_sec=2.0)
    control.position_change_gate = OCRChangeGate(idle_refresh_sec=1.0)
    control._automatic_detection_unlocked = True
    control.state = SimpleNamespace(in_combat=False, status='', decision_state='', location='',
                                    location_key='', place='', ocr_text='')
    control.current_location = SimpleNamespace(name='Astrub')
    control.last_reliable_position = SimpleNamespace(x=1, y=2)
    control.next_confirmation_at = 0.0
    control.unknown_since = None
    control.config = SimpleNamespace(zone_ocr_min_interval_sec=0.6,
                                     position_ocr_min_interval_sec=0.4,
                                     pending_confirmation_interval_sec=0.35,
                                     fallback_unknown_delay_sec=2.5)
    return control


def feed(control, hud, now):
    control._schedule_ocr_for_hud(hud, now=now, captured_at=now, source='printwindow')
    for slot in (control.zone_slot, control.position_slot):
        request_id = slot.scheduler.active_request_id
        if request_id is not None:
            slot.scheduler.completed(request_id=request_id, now=now + 0.01)


def test_confirmed_combat_skips_both_ocr_channels_even_when_confirmation_overdue():
    control = new_controller()
    hud = SimpleNamespace(zone=np.zeros((20, 160, 3), dtype=np.uint8),
                          position=np.zeros((20, 80, 3), dtype=np.uint8))
    control.state.in_combat = True
    control.next_confirmation_at = 1.0  # formerly disabled cadence checks
    for t in (10.0, 10.1, 10.2, 12.0, 20.0):
        feed(control, hud, t)
    assert control.zone_worker.calls == []
    assert control.position_worker.calls == []
    # A fresh post-combat HUD must receive OCR even if its visual pixels did not change.
    control.state.in_combat = False
    control.zone_change_gate.reset()
    control.position_change_gate.reset()
    feed(control, hud, 20.1)
    assert control.zone_worker.calls == [20.1]
    assert control.position_worker.calls == [20.1]


def test_stable_unknown_hud_is_scanned_only_on_idle_or_visual_change():
    control = new_controller()
    control.current_location = None
    hud = SimpleNamespace(zone=np.zeros((20, 180, 3), dtype=np.uint8),
                          position=np.zeros((20, 80, 3), dtype=np.uint8))
    feed(control, hud, 1.0)
    control._mark_unknown(1.1, 'zone absente du catalogue')
    assert control.next_confirmation_at == 0.0
    for t in (1.7, 2.0, 2.9):
        feed(control, hud, t)
    assert control.zone_worker.calls == [1.0]
    feed(control, hud, 3.0)  # idle refresh sec
    assert control.zone_worker.calls == [1.0, 3.0]
    changed = SimpleNamespace(zone=np.full((20, 180, 3), 255, dtype=np.uint8),
                              position=hud.position)
    feed(control, changed, 3.7)
    assert control.zone_worker.calls == [1.0, 3.0, 3.7]


def test_pending_place_remains_fast_confirmation_without_busy_loop():
    control = new_controller()
    hud = SimpleNamespace(zone=np.zeros((20, 180, 3), dtype=np.uint8),
                          position=np.zeros((20, 80, 3), dtype=np.uint8))
    feed(control, hud, 1.0)
    control.next_confirmation_at = 1.35
    feed(control, hud, 1.2)
    assert control.zone_worker.calls == [1.0]
    feed(control, hud, 1.5)  # due but limited by 0.6s min interval
    assert control.zone_worker.calls == [1.0]
    feed(control, hud, 1.6)
    assert control.zone_worker.calls == [1.0, 1.6]


def test_reproducible_zip_includes_opus_and_crc(tmp_path):
    release = tmp_path / 'Dofusic'
    (release / 'Musiques').mkdir(parents=True)
    (release / 'Data').mkdir()
    (release / 'Dofusic.exe').write_bytes(b'fake-for-test')
    (release / 'Data' / 'runtime.dat').write_bytes(os.urandom(10000))
    (release / 'Musiques' / 'personal.opus').write_bytes(b'PUBLIC OPUS')
    first, second = tmp_path / '1.zip', tmp_path / '2.zip'
    builder._make_zip(release, first)
    builder.validate_archive(release, first)
    # Unrelated machine filesystem timestamps must not affect binary ZIP bytes.
    os.utime(release / 'Dofusic.exe', (time.time() - 80000, time.time() - 80000))
    os.utime(release / 'Data' / 'runtime.dat', (time.time() - 15000, time.time() - 15000))
    builder._make_zip(release, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(second) as archive:
        assert archive.read('Dofusic/Musiques/personal.opus') == b'PUBLIC OPUS'


def test_rejects_missing_or_corrupted_portable_zip(tmp_path):
    release = tmp_path / 'Dofusic'
    release.mkdir()
    (release / 'Dofusic.exe').write_bytes(b'something')
    (release / 'Data').mkdir()
    (release / 'Musiques').mkdir()
    (release / 'Musiques/test.opus').write_bytes(b'OggS')
    zip_path = tmp_path / 'broken.zip'
    builder._make_zip(release, zip_path)
    (release / 'another.bin').write_bytes(b'added-after-pack')
    with pytest.raises(builder.BuildError, match='ZIP invalide'):
        builder.validate_archive(release, zip_path)
    (release / 'another.bin').unlink()
    zip_path.write_bytes(zip_path.read_bytes()[:-15])
    with pytest.raises(builder.BuildError, match='ZIP illisible'):
        builder.validate_archive(release, zip_path)


def test_windows_build_rejects_non_windows_instead_of_claiming_success(tmp_path):
    if os.name == 'nt':
        pytest.skip('The native Windows build is verified by the CI Windows job')
    with pytest.raises(builder.BuildError, match='Windows x64'):
        builder.build(tmp_path)

class _FakeMixerMusic:
    def __init__(self):
        self.events = []
        self.busy = False

    def load(self, path):
        self.events.append(('load', path))

    def play(self, count, fade_ms):
        self.events.append(('play', fade_ms))
        self.busy = True

    def stop(self):
        self.events.append(('stop',))
        self.busy = False

    def fadeout(self, duration):
        raise AssertionError('Blocking pygame fadeout must not be called')

    def set_volume(self, value):
        self.events.append(('volume', value))

    def get_busy(self):
        return self.busy


def test_audio_multiple_rapid_requests_latest_wins_without_loading_intermediate_track(monkeypatch):
    from dofusic.audio import player as audio_module
    clock = [10.0]
    monkeypatch.setattr(audio_module.time, 'monotonic', lambda: clock[0])
    player = audio_module.MusicPlayer(fade_ms=650, spectrum_enabled=False, normalize_loudness=False)
    music = _FakeMixerMusic()
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
    player._ready = True
    player._start_track_analysis = lambda *_args: None
    a, b, c = Path('exploration.opus'), Path('combat.opus'), Path('dungeon.opus')
    assert player.play(a)
    assert player.play(b)
    clock[0] = 10.1
    assert player.play(c)
    clock[0] = 10.7
    player.tick(now=10.7)
    assert player.current == c
    assert [event[1] for event in music.events if event[0] == 'load'] == [str(a), str(c)]
    # The player must not write a negative volume or exceed the SDL mixer range.
    assert all(0 <= event[1] <= 1 for event in music.events if event[0] == 'volume')
    player._ready = False
    player.close()


def test_audio_mute_remains_silent_during_fade(monkeypatch):
    from dofusic.audio import player as audio_module
    clock = [20.0]
    monkeypatch.setattr(audio_module.time, 'monotonic', lambda: clock[0])
    player = audio_module.MusicPlayer(fade_ms=650, spectrum_enabled=False, normalize_loudness=False)
    music = _FakeMixerMusic()
    player._pygame = SimpleNamespace(mixer=SimpleNamespace(music=music))
    player._ready = True
    player._start_track_analysis = lambda *_args: None
    assert player.play(Path('a.opus'))
    assert player.play(Path('b.opus'))
    player.set_muted(True)
    clock[0] = 20.3
    player.tick(now=20.3)
    clock[0] = 20.7
    player.tick(now=20.7)
    assert music.events[-1] == ('volume', 0.0)
    assert player.current == Path('b.opus')
    player._ready = False
    player.close()


def test_optional_windows_smoke_script_is_read_only_except_for_started_processes():
    text = (Path(__file__).resolve().parents[1] / 'VERIFIER_PORTABLE_WINDOWS.ps1').read_text(encoding='utf-8')
    assert 'Dofusic.exe' in text
    assert '--self-test' in text
    assert 'CloseMainWindow()' in text
    assert 'Get-CimInstance' in text
    assert 'Stop-Process' not in text
