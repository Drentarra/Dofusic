"""Regressions for a permanently missing overlay until fullscreen toggle.

The native Win32 path still requires confirmation on Windows; synthetic
captures isolate the transitions which previously required manual intervention.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.app import DofusicController
from dofusic.capture.dofus_window import CapturedFrame, DofusCapture
from dofusic.capture.service import CaptureService


def _await(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.mark.parametrize('first_mode,first_source', [
    ('fullscreen', 'printwindow'),
    ('fullscreen', None),
    ('unknown', None),
])
def test_capture_recovers_without_fullscreen_toggle(first_mode, first_source):
    factories = []
    closed = []

    class Backend:
        def __init__(self, index):
            self.index = index
            self.window_mode = first_mode
        def capture(self):
            if self.index == 1:
                self.window_mode = first_mode
                if first_source is None:
                    return None
                return CapturedFrame(np.zeros((6, 8, 3), dtype=np.uint8), first_source, 33)
            self.window_mode = 'fullscreen'
            return CapturedFrame(np.zeros((6, 8, 3), dtype=np.uint8), 'visible', 33,
                                 screen_rect=(0, 0, 8, 6))
        def close(self):
            closed.append(self.index)

    def factory():
        factories.append(len(factories) + 1)
        return Backend(factories[-1])

    service = CaptureService(factory, fps=60)
    service._unusable_capture_timeout_sec = 0.12
    service.start()
    try:
        assert _await(lambda: len(factories) >= 2)
        assert _await(lambda: (snap := service.latest()) is not None
                      and snap.frame is not None and snap.frame.source == 'visible')
    finally:
        service.close(timeout=2)
    assert len(factories) == 2
    assert closed.count(1) == 1


def test_windowed_mode_does_not_recreate_capture_backend():
    built = []
    class Backend:
        window_mode = 'windowed'
        def capture(self):
            return None
        def close(self):
            pass
    service = CaptureService(lambda: (built.append(1) or Backend()), fps=60)
    service._unusable_capture_timeout_sec = 0.08
    service.start()
    try:
        assert _await(lambda: (snap := service.latest()) is not None and snap.sequence >= 2)
        time.sleep(0.17)
        assert len(built) == 1
    finally:
        service.close(timeout=2)


def test_unknown_display_mode_preserves_confirmed_runtime_and_audio():
    controller = DofusicController.__new__(DofusicController)
    controller.logger = logging.getLogger('test-capture-recovery')
    controller.state = SimpleNamespace(
        window_found=True, overlay_hwnd=11, combat_overlay_rect=(1, 2, 5, 5),
        zone_overlay_rect=(1, 10, 30, 20), position_overlay_rect=(1, 35, 20, 15),
        fullscreen_required=False, game_detected=True, status='',
        music='Zone confirmée', position_x=10, position_y=27,
    )
    controller._automatic_runtime_active = True
    controller._mode_unavailable_since = None
    controller._stop_automatic_runtime = lambda: (_ for _ in ()).throw(
        AssertionError('A mode unknown must not tear down a healthy session')
    )
    for now in (1., 2., 14., 30., 70.):
        assert controller._capture_unavailable(now, mode='unknown') is False
        assert controller.state.overlay_hwnd is None
        assert controller.state.fullscreen_required is False
    assert controller.state.music == 'Zone confirmée'
    assert (controller.state.position_x, controller.state.position_y) == (10, 27)
    assert controller._automatic_runtime_active


def test_capture_roi_does_not_mistake_passive_overlay_for_hidden_game():
    capture = DofusCapture.__new__(DofusCapture)
    capture.win32gui = SimpleNamespace(GetForegroundWindow=lambda: 333)
    capture._root_and_pid = lambda hwnd: (111, 444) if hwnd == 333 else None
    capture.root_hwnd = 111
    capture.pid = 444
    capture._client_capture_rect = lambda hwnd: (_ for _ in ()).throw(
        AssertionError('WindowFromPoint is not authoritative for foreground game')
    )
    assert capture._roi_visible(111)


def test_zone_and_combat_overlay_remain_when_position_ocr_is_unavailable(monkeypatch):
    from test_user_overlay_hover_regressions import scenario

    overlay, window, physical, calls, permitted, top = scenario(monkeypatch)
    overlay.enabled = True
    overlay._foreground_role = lambda _hwnd: 'dofus'
    overlay._ensure_capture_excluded = lambda: True
    overlay._set_topmost = lambda _enabled: None
    zone = (10, 40, 230, 34)
    combat = (10, 0, 38, 34)
    overlay.update(41, combat, zone, None)
    assert window.visible
    assert window.geometry == (6, -4, 238, 82)
    assert window.drawn_rects is not None
    assert window.drawn_rects[2][2:] == (0, 0)


def test_capture_visibility_skips_only_our_own_excluded_overlay():
    capture = DofusCapture.__new__(DofusCapture)
    capture.win32gui = SimpleNamespace(
        GetForegroundWindow=lambda: 200,
        ClientToScreen=lambda _hwnd, point: point,
        WindowFromPoint=lambda point: 31 if point[0] < 100 else 32,
    )
    capture._root_and_pid = lambda hwnd: {
        200: (200, 888),
        31: (31, 444),   # Our passive, excluded overlay
        32: (32, 999),  # Dofus
    }.get(hwnd)
    capture.root_hwnd = 32
    capture.pid = 999
    capture._client_capture_rect = lambda hwnd: (0, 0, 260, 100)
    capture._is_own_excluded_overlay = lambda root, pid: root == 31 and pid == 444
    assert capture._roi_visible(32)

    # A foreign topmost window is not an excluded overlay even when it appears
    # above the HUD: do not hallucinate valid on-screen pixels.
    capture._is_own_excluded_overlay = lambda _root, _pid: False
    assert not capture._roi_visible(32)
