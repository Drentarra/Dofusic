"""Regression tests for opening Dofusic before Dofus.exe.

Tests cover the actual background capture thread, with controlled in-memory
Windows window arrivals. They do NOT claim native Win32 validation.
"""

import threading
import time
from types import SimpleNamespace

import numpy as np

import dofusic.app as app_module
from dofusic.app import DofusicController
from dofusic.capture.dofus_window import CapturedFrame, DofusCapture
from dofusic.config import AppConfig
from dofusic.ui.main_window import combat_badge, detection_badge


class FakeWorker:
    def __init__(self):
        self.starts = 0
        self.closes = 0
        self.alive = False

    def start(self):
        self.starts += 1
        self.alive = True

    def close(self):
        self.closes += 1
        self.alive = False

    def poll(self):
        return []


class FakePlayer:
    muted = False
    def __init__(self):
        self.played = []
        self.stopped = []
    def set_muted(self, muted):
        self.muted = muted
    def play(self, path, **kwargs):
        self.played.append((path, kwargs))
        return True
    def stop(self, **kwargs):
        self.stopped.append(kwargs)
    def close(self):
        pass


class FakeLibrary:
    def __init__(self, path):
        self.path = path
    def resolve(self, location, *, combat=False, **kwargs):
        return self.path if location is None and not combat else None


def _await_snapshot(service, predicate, *, timeout=2.0):
    """Wait for the background thread, without sleeps or elapsed-time assertions."""
    deadline = time.monotonic() + timeout
    event = threading.Event()
    while time.monotonic() < deadline:
        snapshot = service.latest(0)
        if snapshot is not None and predicate(snapshot):
            return snapshot
        event.wait(0.015)
    raise AssertionError('capture watcher did not publish the expected snapshot')


def test_window_watcher_recovers_if_game_is_started_later_even_if_process_probe_misses_it(monkeypatch, tmp_path):
    game_window = threading.Event()
    attempted = threading.Event()

    class Backend:
        def __init__(self, **kwargs):
            pass
        def capture(self):
            attempted.set()
            self.window_mode = 'fullscreen' if game_window.is_set() else 'missing'
            if not game_window.is_set():
                return None
            return CapturedFrame(np.zeros((90, 380, 3), dtype=np.uint8), 'visible', 421)
        def close(self):
            pass

    monkeypatch.setattr(app_module, 'DofusCapture', Backend)
    zone, position, player = FakeWorker(), FakeWorker(), FakePlayer()
    controller = DofusicController(
        AppConfig(capture_fps=30),
        music_library=FakeLibrary(tmp_path / 'Musique.mp3'),
        player=player, zone_worker=zone, position_worker=position,
        process_probe=lambda _name: False,  # simulate a missed process enumeration
    )
    try:
        controller.start()
        assert attempted.wait(2.0)
        controller._sync_game_process(time.monotonic())
        assert not controller.game_process_running
        assert zone.starts == position.starts == 0
        assert not player.played
        assert controller.state.status == 'En attente de Dofus.exe'

        game_window.set()
        found = _await_snapshot(controller.capture_service, lambda s: s.frame is not None)
        assert found.frame.hwnd == 421
        assert controller._sync_game_process(time.monotonic()) is True
        assert controller.game_process_running
        assert zone.starts == position.starts == 1
        assert len(player.played) == 1
        assert controller.capture_service.alive
        controller._sync_game_process(time.monotonic())
        assert zone.starts == position.starts == 1  # never restart every UI tick

        # The watcher keeps running after the game closes, to permit relaunch.
        game_window.clear()
        _await_snapshot(controller.capture_service,
                        lambda s: s.sequence > found.sequence and s.frame is None)
        assert controller._sync_game_process(time.monotonic()) is False
        assert zone.closes == position.closes == 0  # tolerate short missing window
        assert controller._sync_game_process(time.monotonic() + 2.6) is False
        assert zone.closes == position.closes == 1
        assert controller.capture_service.alive

        game_window.set()
        _await_snapshot(controller.capture_service,
                        lambda s: s.sequence > found.sequence and s.frame is not None)
        assert controller._sync_game_process(time.monotonic()) is True
        assert zone.starts == position.starts == 2
    finally:
        controller.close()
    assert not controller.capture_service.alive


def test_window_absence_is_cached_briefly_but_late_window_is_discovered(monkeypatch):
    now = [10.0]
    monkeypatch.setattr('dofusic.capture.dofus_window.time.monotonic', lambda: now[0])
    candidates = []
    scans = []
    def enum_windows(callback, unused):
        scans.append(now[0])
        for hwnd in candidates:
            callback(hwnd, unused)
    backend = SimpleNamespace(
        hwnd=None, last_window_refresh=0.0, window_refresh_sec=1.5,
        _valid_hwnd=lambda _hwnd: False,
        _candidate_valid=lambda _hwnd: True,
        _client_size=lambda _hwnd: (800, 600),
        _remember_identity=None,
        _clear_identity=lambda: None,
        win32gui=SimpleNamespace(EnumWindows=enum_windows, GetForegroundWindow=lambda: 22),
    )
    backend._remember_identity = lambda hwnd: (setattr(backend, 'hwnd', hwnd) or True)
    assert DofusCapture._find_window(backend) is None
    assert len(scans) == 1
    candidates.append(22)
    now[0] = 10.5
    backend._remember_identity = lambda hwnd: (setattr(backend, 'hwnd', hwnd) or True)
    assert DofusCapture._find_window(backend) is None
    assert len(scans) == 1
    now[0] = 11.6
    assert DofusCapture._find_window(backend) == 22
    assert len(scans) == 2


def test_waiting_ui_describes_dofus_exe_not_combat_detection():
    state = SimpleNamespace(window_found=False, worker_status='Arrêté',
                            zone_worker_status='Arrêté', position_worker_status='Arrêté')
    assert detection_badge(state)[0] == 'En attente de Dofus.exe'
    assert combat_badge(state)[0] == 'En attente de Dofus.exe'
