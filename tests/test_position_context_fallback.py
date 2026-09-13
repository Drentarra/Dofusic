from __future__ import annotations

import types

import numpy as np

from dofusic.vision.engines import PositionOCREngine


class _SequenceBackend:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        text, confidence = self.outputs[self.calls]
        self.calls += 1
        return types.SimpleNamespace(txts=(text,), scores=(confidence,))


def test_large_jump_from_previous_position_forces_variant_consensus():
    backend = _SequenceBackend([
        ('5,-19', 0.96),
        ('5,-1', 0.96),
        ('5,-19', 0.88),
        ('5,-19', 0.87),
    ])
    engine = PositionOCREngine(backend=backend, fast_accept_confidence=0.84)
    image = np.zeros((29, 96, 3), dtype=np.uint8)

    first = engine.read(image)
    second = engine.read(image)

    assert (first.x, first.y) == (5, -19)
    assert (second.x, second.y) == (5, -19)
    assert backend.calls == 4
    assert second.engine.endswith('[context fallback]')
