from __future__ import annotations

import numpy as np

from dofusic.config import AppConfig


def test_default_runtime_profile_is_low_load():
    cfg = AppConfig().sanitized()
    assert cfg.capture_fps <= 10
    assert cfg.ui_tick_ms >= 80
    assert cfg.zone_ocr_min_interval_sec >= 0.50
    assert cfg.position_ocr_min_interval_sec >= 0.35
    assert cfg.zone_ocr_idle_refresh_sec >= 1.50
    assert cfg.position_ocr_idle_refresh_sec >= 0.80


def test_ocr_change_gate_skips_static_frames_but_periodically_refreshes():
    from dofusic.vision.change_gate import OCRChangeGate

    gate = OCRChangeGate(change_threshold=4.0, idle_refresh_sec=2.0)
    static = np.full((32, 180, 3), 100, dtype=np.uint8)

    assert gate.should_scan(static, now=1.0)
    gate.mark_scanned(now=1.0)
    assert not gate.should_scan(static.copy(), now=1.5)
    assert not gate.should_scan(static.copy(), now=2.9)
    assert gate.should_scan(static.copy(), now=3.0)


def test_ocr_change_gate_detects_real_text_region_change_before_idle_refresh():
    from dofusic.vision.change_gate import OCRChangeGate

    gate = OCRChangeGate(change_threshold=4.0, idle_refresh_sec=2.0)
    before = np.zeros((32, 180, 3), dtype=np.uint8)
    after = before.copy()
    after[:, 50:130] = 255

    assert gate.should_scan(before, now=1.0)
    gate.mark_scanned(now=1.0)
    assert gate.should_scan(after, now=1.2)
