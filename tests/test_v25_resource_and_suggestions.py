from __future__ import annotations

import json

import numpy as np

from dofusic.config import AppConfig
from dofusic.online import discovery


def test_youtube_suggestions_use_clean_json_firefox_client(monkeypatch):
    seen = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit):
            return json.dumps(['sha', ['shaka ponk', 'shaka ponk im picky']]).encode('utf-8')

    def fake_urlopen(request, timeout):
        seen['url'] = request.full_url
        seen['timeout'] = timeout
        return _Response()

    monkeypatch.setattr(discovery, 'urlopen', fake_urlopen)
    values = tuple(discovery._default_suggestion_runner('sha', 8))

    assert values == ('shaka ponk', 'shaka ponk im picky')
    assert 'client=firefox' in seen['url']
    assert 'ds=yt' in seen['url']


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


def test_search_result_thumbnail_prefers_small_youtube_image():
    track = discovery._track_from_row({
        'id': 'abcdefghijk',
        'title': 'Titre',
        'thumbnail': 'https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg',
    })
    assert track is not None
    assert track.thumbnail_url == 'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg'
