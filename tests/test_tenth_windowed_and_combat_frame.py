"""Régressions de cadre combat et de capture d'un client Windows déplacé.

Tests purs pour les dimensions; Win32 est simulé uniquement pour les points de
visibilité. Ils ne remplacent pas une validation native avec Dofus.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from dofusic.capture.dofus_window import DofusCapture
from dofusic.vision.layout import HUDInputs, HUDTransform, tracking_screen_rects


def _overlay_width(*, buttons, measured, scale=1.0):
    hud = HUDInputs(zone=np.zeros((34, 220, 3), np.uint8),
                    position=np.zeros((29, 180, 3), np.uint8),
                    combat=np.zeros((40, 320, 3), np.uint8),
                    transform=HUDTransform(scale=scale), toolbar_width=measured)
    rects = tracking_screen_rects(
        capture_screen_rect=(1400, 150, 660, 110),
        capture_image_shape=(110, 660, 3), hud=hud,
        toolbar_buttons=buttons,
    )
    return rects.combat[2]


@pytest.mark.parametrize('state,spuriously_measured,minimum', [
    (1, 18, 35), (2, 19, 70), (6, 28, 198),
    (6, 38, 198), (6, 76, 198), (2, 38, 70),
])
def test_short_fake_rim_must_never_clip_recognized_toolbar(state, spuriously_measured, minimum):
    assert _overlay_width(buttons=state, measured=spuriously_measured) >= minimum


@pytest.mark.parametrize('state,physical_width', [(1, 38), (2, 76), (6, 205), (2, 206), (6, 305)])
def test_real_toolbar_widths_remain_eligible(state, physical_width):
    assert abs(_overlay_width(buttons=state, measured=physical_width) - physical_width) <= 3


def test_windowed_capture_uses_visible_hud_not_far_edge_of_game_window():
    """Client partly right of desktop: HUD present but centre/end offscreen."""
    capture = DofusCapture.__new__(DofusCapture)
    capture.pid = 510
    capture._client_capture_rect = lambda hwnd: (0, 0, 1200, 106)
    capture.win32gui = SimpleNamespace(
        ClientToScreen=lambda hwnd, xy: (1450 + xy[0], 120 + xy[1]),
        WindowFromPoint=lambda point: 67 if point[0] < 1680 else 99,
    )
    capture._root_and_pid = lambda hwnd: (hwnd, 510 if hwnd == 67 else 5100)
    assert capture._roi_visible(67)


def test_windowed_capture_rejects_foreign_window_over_hud():
    capture = DofusCapture.__new__(DofusCapture)
    capture.pid = 510
    capture._client_capture_rect = lambda hwnd: (0, 0, 1200, 106)
    capture.win32gui = SimpleNamespace(
        ClientToScreen=lambda hwnd, xy: (200 + xy[0], 120 + xy[1]),
        WindowFromPoint=lambda point: 99,
    )
    capture._root_and_pid = lambda hwnd: (hwnd, 510 if hwnd == 67 else 5100)
    assert not capture._roi_visible(67)


def test_capture_uses_the_exact_screen_rectangle_used_for_grab(monkeypatch):
    """Dragging a window between the screenshot and frame publication must not
    give the screenshot the NEXT position of the game window."""
    from dofusic.capture import dofus_window
    capture = DofusCapture.__new__(DofusCapture)
    capture._find_window = lambda: 67
    capture._display_mode = lambda hwnd: 'fullscreen'
    screenshot = np.full((106, 450, 3), 55, np.uint8)
    original_rect = (1450, 80, 450, 106)
    capture._capture_visible_roi = lambda hwnd: (screenshot, original_rect)
    capture._roi_screen_rect = lambda hwnd: {
        'left': 600, 'top': 250, 'width': 450, 'height': 106,
    }
    capture.geometry = dofus_window.HUDGeometry()
    capture._client_size = lambda hwnd: (1920, 1080)
    capture._last_client_size = None
    capture._resolution_transform = None
    frame = capture.capture()
    assert frame is not None
    assert frame.source == 'visible'
    assert frame.screen_rect == original_rect
    assert frame.image is screenshot


def test_mss_grab_and_reported_rectangle_are_one_atomic_observation():
    capture = DofusCapture.__new__(DofusCapture)
    capture._roi_visible = lambda hwnd: True
    seen = []
    requested = {'left': -170, 'top': 90, 'width': 640, 'height': 105}
    capture._roi_screen_rect = lambda hwnd: seen.append(hwnd) or requested
    bgra = np.zeros((105, 640, 4), np.uint8)
    bgra[:, :, 0] = 97
    capture.sct = SimpleNamespace(grab=lambda rect: bgra if rect == requested else None)
    result = capture._capture_visible_roi(21)
    assert result is not None
    image, rect = result
    assert seen == [21]  # No second ClientToScreen after the capture.
    assert rect == (-170, 90, 640, 105)
    assert image.shape == (105, 640, 3)
    assert (image[:, :, 0] == 97).all()
