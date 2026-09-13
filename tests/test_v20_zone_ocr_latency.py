import types
import numpy as np

from dofusic.vision.engines import ZoneOCREngine


class _Backend:
    def __init__(self, text, confidence):
        self.text = text
        self.confidence = confidence
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        return types.SimpleNamespace(txts=(self.text,), scores=(self.confidence,))


def test_live_zone_read_runs_exactly_one_small_inference_even_at_low_confidence():
    small = _Backend("Astrub (Cité d'Astrub)", 0.61)
    engine = ZoneOCREngine(fast_backend=small, fast_accept_confidence=0.76)

    result = engine.read(np.zeros((34, 320, 3), dtype=np.uint8))

    assert result.text == "Astrub (Cité d'Astrub)"
    assert result.confidence == 0.61
    assert small.calls == 1
    assert 'small-live' in result.engine


