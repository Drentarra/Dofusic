"""Fullscreen requirement: no native screenshot/OCR/automatic audio in windowed mode.

The Windows monitor/window geometry is tested as a pure function. The lifecycle
is tested against a threaded capture backend; actual Win32 behavior remains a
native Windows verification point.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import dofusic.app as app_module
from dofusic.app import DofusicController
from dofusic.capture.dofus_window import CapturedFrame, DofusCapture, client_fills_monitor
from dofusic.config import AppConfig
from dofusic.ui.main_window import combat_badge, detection_badge


@pytest.mark.parametrize('client,monitor,expected', [
    ((0, 0, 1920, 1080), (0, 0, 1920, 1080), True),
    ((1920, 0, 4480, 1440), (1920, 0, 4480, 1440), True),
    ((-2560, -100, 0, 1340), (-2560, -100, 0, 1340), True),
    ((1, 0, 1919, 1079), (0, 0, 1920, 1080), True),
    ((0, 0, 1920, 1040), (0, 0, 1920, 1080), False),
    ((0, 30, 1920, 1080), (0, 0, 1920, 1080), False),
    ((200, 100, 1400, 900), (0, 0, 1920, 1080), False),
    ((0, 0, 1920, 1080), (0, 0, 3840, 2160), False),
    ((0, 0, 0, 0), (0, 0, 1920, 1080), False),
])
def test_fullscreen_client_geometry(client, monitor, expected):
    assert client_fills_monitor(client, monitor) is expected


def test_windowed_dofus_never_calls_capture_or_hud_calibration():
    capture = DofusCapture.__new__(DofusCapture)
    capture._find_window = lambda: 88
    capture._display_mode = lambda _hwnd: 'windowed'
    capture._capture_visible_roi = lambda _hwnd: pytest.fail('Capture prohibited')
    assert capture.capture() is None
    assert capture.window_mode == 'windowed'
    capture._display_mode = lambda _hwnd: 'unknown'
    assert capture.capture() is None
    assert capture.window_mode == 'unknown'


def test_real_display_mode_uses_client_and_own_monitor():
    capture = DofusCapture.__new__(DofusCapture)
    capture._client_size = lambda _hwnd: (2560, 1440)
    capture.win32con = SimpleNamespace(MONITOR_DEFAULTTONEAREST=2)
    capture.win32gui = SimpleNamespace(ClientToScreen=lambda _hwnd, xy: (1920, 0))
    capture.win32api = SimpleNamespace(
        MonitorFromWindow=lambda _hwnd, _flag: 27,
        GetMonitorInfo=lambda _monitor: {'Monitor': (1920, 0, 4480, 1440), 'Work': (1920, 0, 4480, 1400)},
    )
    assert capture._display_mode(55) == 'fullscreen'
    capture._client_size = lambda _hwnd: (2560, 1400)
    assert capture._display_mode(55) == 'windowed'
    capture.win32api.GetMonitorInfo = lambda _monitor: (_ for _ in ()).throw(OSError('Win32 failure'))
    assert capture._display_mode(55) == 'unknown'


def test_windowed_fullscreen_windowed_switch_stops_and_recovers_workers(monkeypatch, tmp_path):
    mode = ['windowed']
    activity = threading.Event()

    class Backend:
        def __init__(self, **_kwargs):
            self.window_mode = 'missing'
        def capture(self):
            self.window_mode = mode[0]
            activity.set()
            if self.window_mode != 'fullscreen':
                return None
            return CapturedFrame(np.zeros((70, 360, 3), dtype=np.uint8), 'visible', 81)
        def close(self):
            pass

    class Worker:
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

    class Player:
        muted = False
        def __init__(self):
            self.plays = 0
            self.stops = 0
        def set_muted(self, value):
            self.muted = value
        def play(self, *_args, **_kwargs):
            self.plays += 1
            return True
        def stop(self, **_kwargs):
            self.stops += 1
        def close(self):
            pass

    class Library:
        def resolve(self, location, *, combat=False, **_kwargs):
            return tmp_path / 'Musique.opus' if location is None and not combat else None

    monkeypatch.setattr(app_module, 'DofusCapture', Backend)
    zone, position, player = Worker(), Worker(), Player()
    ctrl = DofusicController(
        AppConfig(capture_fps=30), player=player,
        zone_worker=zone, position_worker=position,
        music_library=Library(), process_probe=lambda _name: True,
    )

    def wait_mode(expected, newer_than=0):
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            snap = ctrl.capture_service.latest(0)
            if snap is not None and snap.sequence > newer_than and snap.window_mode == expected:
                return snap
            threading.Event().wait(0.01)
        raise AssertionError(f'No snapshot for {expected}')

    try:
        ctrl.start()
        snap = wait_mode('windowed')
        ctrl.tick()
        assert ctrl.state.fullscreen_required
        assert detection_badge(ctrl.state)[0] == 'Plein écran requis'
        assert combat_badge(ctrl.state)[0] == 'Plein écran requis'
        assert (zone.starts, position.starts, player.plays) == (0, 0, 0)
        assert not ctrl._automatic_runtime_active

        mode[0] = 'fullscreen'
        snap = wait_mode('fullscreen', snap.sequence)
        ctrl.tick()
        assert ctrl._automatic_runtime_active
        assert not ctrl.state.fullscreen_required
        assert (zone.starts, position.starts) == (1, 1)
        assert player.plays == 1

        mode[0] = 'windowed'
        snap = wait_mode('windowed', snap.sequence)
        ctrl.tick()
        assert ctrl.state.fullscreen_required
        assert ctrl._automatic_runtime_active  # one missed fullscreen sample is harmless
        ctrl.tick(now=time.monotonic() + 0.80)
        assert not ctrl._automatic_runtime_active
        assert (zone.closes, position.closes) == (1, 1)
        assert player.stops >= 1
        assert ctrl.state.overlay_hwnd is None
        assert ctrl.state.location == 'En attente'

        mode[0] = 'fullscreen'
        wait_mode('fullscreen', snap.sequence)
        ctrl.tick()
        assert not ctrl.state.fullscreen_required
        assert (zone.starts, position.starts) == (2, 2)
    finally:
        ctrl.close()
