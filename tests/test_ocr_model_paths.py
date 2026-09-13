from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import dofusic.vision.engines as engines


class _FakeRapidOCR:
    calls: list[dict[str, object]] = []

    def __init__(self, *, params):
        self.params = dict(params)
        self.calls.append(self.params)

    def __call__(self, *args, **kwargs):
        return types.SimpleNamespace(txts=(), scores=())


@pytest.fixture
def fake_rapidocr(monkeypatch):
    _FakeRapidOCR.calls.clear()
    module = types.SimpleNamespace(
        EngineType=types.SimpleNamespace(ONNXRUNTIME='onnxruntime'),
        OCRVersion=types.SimpleNamespace(PPOCRV6='PP-OCRv6'),
        ModelType=types.SimpleNamespace(SMALL='small'),
        RapidOCR=_FakeRapidOCR,
    )
    monkeypatch.setitem(sys.modules, 'rapidocr', module)
    return module


def test_zone_uses_only_bundled_small_model(monkeypatch, tmp_path, fake_rapidocr):
    models = tmp_path / 'Models'
    models.mkdir()
    small = models / 'PP-OCRv6_rec_small.onnx'
    small.write_bytes(b'small')
    monkeypatch.setattr(engines, 'models_dir', lambda: models)

    engine = engines.ZoneOCREngine(cpu_threads=2)
    engine.load()

    assert len(_FakeRapidOCR.calls) == 1
    assert _FakeRapidOCR.calls[0]['Rec.model_path'] == str(small)


def test_position_uses_bundled_small_model(monkeypatch, tmp_path, fake_rapidocr):
    models = tmp_path / 'Models'
    models.mkdir()
    small = models / 'PP-OCRv6_rec_small.onnx'
    small.write_bytes(b'small')
    monkeypatch.setattr(engines, 'models_dir', lambda: models)

    engine = engines.PositionOCREngine(cpu_threads=1)
    engine.load()

    assert _FakeRapidOCR.calls[0]['Rec.model_path'] == str(small)


def test_missing_bundled_model_fails_before_rapidocr_download(monkeypatch, tmp_path, fake_rapidocr):
    models = tmp_path / 'Models'
    models.mkdir()
    monkeypatch.setattr(engines, 'models_dir', lambda: models)

    with pytest.raises(FileNotFoundError, match='PP-OCRv6_rec_small.onnx'):
        engines.ZoneOCREngine().load()
    assert _FakeRapidOCR.calls == []
