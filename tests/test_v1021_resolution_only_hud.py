"""Regression tests for Dofusic 1.0.21 resolution-only HUD mapping.

All tests are offline; the HUD now uses a fixed, empirical scale curve.
"""
from __future__ import annotations

import numpy as np
import pytest

from dofusic.capture import dofus_window
from dofusic.vision.layout import (
    HUDGeometry,
    HUDTransform,
    extract_hud_inputs,
    hud_transform_from_client_size,
    tracking_screen_rects,
)

REFERENCE = HUDTransform()
BAD_DISCORD_TRANSFORM = HUDTransform(scale=91 / 37, origin_x=68, origin_y=1)


def fake_capture(*, client=(1920, 1080), old=None):
    if isinstance(client, tuple):
        client = [client]
    capture = dofus_window.DofusCapture.__new__(dofus_window.DofusCapture)
    capture.geometry = HUDGeometry()
    capture._client_size = lambda _hwnd: client[0]
    capture._find_window = lambda: 1234
    capture._display_mode = lambda _hwnd: 'fullscreen'
    capture._capture_visible_roi = lambda _hwnd: (
        np.zeros((220, min(client[0][0], 1920), 3), np.uint8),
        (0, 0, min(client[0][0], 1920), 220),
    )
    capture._last_client_size = None
    capture._resolution_transform = old
    capture.pid = 314
    capture.root_hwnd = 1234
    capture.last_printwindow = 0.0
    return capture


@pytest.mark.parametrize('size, expected', [
    ((1920, 1080), 1.0),
    ((1600, 900), 30 / 37),
    ((1680, 1050), 31 / 37),
    ((1280, 960), 23 / 37),
    ((1280, 720), 23 / 37),
    ((2560, 1440), 1.0),
    ((3840, 2160), 1.0),
    ((3440, 1440), 1.0),
    ((7680, 4320), 1.0),
])
def test_pure_resolution_transform_and_fixed_origin(size, expected):
    transform = hud_transform_from_client_size(*size)
    assert transform == HUDTransform(scale=expected, origin_x=0, origin_y=0)
    geo = HUDGeometry()
    rect = geo.capture_rect(*size, ui_scale=transform.scale)
    assert rect == (0, 0, size[0], max(40, round(106 * expected)))


@pytest.mark.parametrize('invalid', [(0, 1080), (1920, 0), (-10, 720)])
def test_invalid_dimensions_never_generate_fake_hud(invalid):
    assert hud_transform_from_client_size(*invalid) is None


def test_discord_false_transform_cannot_poison_runtime():
    client = [(1920, 1080)]
    cap = fake_capture(client=client, old=BAD_DISCORD_TRANSFORM)

    # Also guard the offline detector, which is intentionally no longer imported
    # by dofus_window; the normal capture method must not even reference it.
    assert not hasattr(dofus_window, 'validated_hud_measurement')
    for _ in range(8):
        frame = cap.capture()
        assert frame is not None and frame.hud_transform == REFERENCE
        assert cap._resolution_transform == REFERENCE
        assert cap._client_capture_rect(1234) == (0, 0, 1920, 106)


def test_resize_and_return_to_reference_without_persisting_bad_scale():
    client = [(1920, 1080)]
    cap = fake_capture(client=client)
    assert cap.capture().hud_transform == REFERENCE
    client[0] = (2560, 1440)
    assert cap.capture().hud_transform == REFERENCE
    assert cap._client_capture_rect(1234) == (0, 0, 2560, 106)
    client[0] = (1920, 1080)
    assert cap.capture().hud_transform == REFERENCE
    assert cap._client_capture_rect(1234) == (0, 0, 1920, 106)


def test_windowed_fullscreen_transition_does_not_reset_resolution_geometry():
    cap = fake_capture()
    mode = ['fullscreen']
    cap._display_mode = lambda hwnd: mode[0]
    assert cap.capture().hud_transform == REFERENCE
    mode[0] = 'windowed'
    assert cap.capture() is None
    mode[0] = 'fullscreen'
    assert cap.capture().hud_transform == REFERENCE


@pytest.mark.parametrize('size,expected_combat_h,expected_zone_h,expected_y', [
    ((1920, 1080), 37, 34, 40),
    ((1600, 900), 30, 28, 32),
    ((2560, 1440), 37, 34, 40),
    ((3840, 2160), 37, 34, 40),
    ((1280, 960), 23, 21, 25),
    ((1680, 1050), 31, 28, 34),
])
def test_ocr_and_overlay_share_the_same_resolution_geometry(size, expected_combat_h, expected_zone_h, expected_y):
    geo = HUDGeometry()
    transform = hud_transform_from_client_size(*size, geometry=geo)
    crop = geo.capture_rect(*size, ui_scale=transform.scale)
    frame = np.zeros((crop[3], crop[2], 3), dtype=np.uint8)
    hud = extract_hud_inputs(frame, geo, transform=transform)
    rects = tracking_screen_rects(
        capture_screen_rect=(50, 90, crop[2], crop[3]),
        capture_image_shape=frame.shape,
        hud=hud, geometry=geo,
    )
    assert rects.combat[:2] == (50, 90)
    assert rects.combat[3] == expected_combat_h
    assert rects.zone[:2] == (50, 90 + expected_y)
    assert rects.zone[3] == expected_zone_h
    assert hud.zone.shape[0] == expected_zone_h


def test_right_edge_measure_never_calls_global_origin_search(monkeypatch):
    def prohibited(*_args, **_kwargs):
        raise AssertionError('The full X/Y rim scan must not be used by live HUD extraction')
    monkeypatch.setattr('tools.hud_reference.measure_toolbar_rim', prohibited)
    img = np.zeros((106, 1920, 3), dtype=np.uint8)
    hud = extract_hud_inputs(img, HUDGeometry(), transform=HUDTransform())
    assert hud.transform == HUDTransform()
    assert hud.toolbar_width is None


def test_resolution_measure_is_cached_until_client_size_changes(monkeypatch):
    cap = fake_capture()
    original = dofus_window.hud_transform_from_client_size
    calls = []
    def traced(width, height, *, geometry):
        calls.append((width, height))
        return original(width, height, geometry=geometry)
    monkeypatch.setattr(dofus_window, 'hud_transform_from_client_size', traced)
    for _ in range(20):
        cap.capture()
    assert calls == [(1920, 1080)]
