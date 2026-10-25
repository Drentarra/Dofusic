"""Regression: obstructed HUD cannot block capture with a 1-second rim scan."""
from __future__ import annotations

import time

import cv2
import numpy as np

from dofusic.vision.layout import HUDTransform
from tools.hud_reference import estimate_hud_transform


def test_unrecognized_hud_returns_quickly_with_busy_visual_noise():
    rng = np.random.default_rng(53)
    obscured = rng.integers(0, 256, (106, 1920, 3), dtype=np.uint8)
    start = time.perf_counter()
    assert estimate_hud_transform(obscured, previous=HUDTransform(), require_rim=True) is None
    assert time.perf_counter() - start < 0.7


def test_flat_inventory_window_does_not_trigger_bruteforce_scan():
    obscured = np.full((106, 1920, 3), (90, 120, 140), dtype=np.uint8)
    start = time.perf_counter()
    assert estimate_hud_transform(obscured, previous=HUDTransform(), require_rim=True) is None
    assert time.perf_counter() - start < 0.7


def test_horizontal_inventory_bands_do_not_trigger_bruteforce_scan():
    rng = np.random.default_rng(23)
    band_colors = rng.integers(40, 170, (106, 1, 3), dtype=np.uint8)
    obscured = np.tile(band_colors, (1, 1920, 1))
    start = time.perf_counter()
    assert estimate_hud_transform(obscured, previous=HUDTransform(), require_rim=True) is None
    assert time.perf_counter() - start < 0.7


def test_grayscale_image_still_supported():
    source = np.full((106, 1920), 70, dtype=np.uint8)
    assert estimate_hud_transform(source, previous=HUDTransform(), require_rim=True) is None


def test_real_hud_still_recalibrates_after_occlusion():
    from pathlib import Path
    source = Path(__file__).parent / 'fixtures' / 'hud_live_october_2026' / 'case_3.png'
    hud = cv2.imdecode(np.fromfile(str(source), np.uint8), cv2.IMREAD_COLOR)
    assert hud is not None
    assert estimate_hud_transform(hud, previous=HUDTransform(), require_rim=True) == HUDTransform()
    covered = hud.copy()
    covered[:84, :480] = (90, 120, 140)
    assert estimate_hud_transform(covered, previous=HUDTransform(), require_rim=True) is None
    assert estimate_hud_transform(hud, previous=HUDTransform(), require_rim=True) == HUDTransform()


def test_two_colour_inventory_panel_does_not_trigger_bruteforce_scan():
    # Reproduces a large flat GUI panel with a hard vertical boundary; the
    # naive per-offset border scan spent >2 seconds searching for a lower rim.
    obscured = np.zeros((106, 1920, 3), dtype=np.uint8)
    obscured[:, :500] = (90, 120, 140)
    obscured[:, 500:] = (170, 82, 30)
    start = time.perf_counter()
    assert estimate_hud_transform(obscured, previous=HUDTransform(), require_rim=True) is None
    assert time.perf_counter() - start < 0.7
