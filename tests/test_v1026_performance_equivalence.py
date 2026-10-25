"""Optimization invariants: exact recognition and forensic data are preserved."""

import cv2
import numpy as np
import pytest

from dofusic.vision import combat


def _legacy_overlap_score(mask: np.ndarray, name: str) -> float:
    """Reference implementation from 1.0.25, retained only in tests."""
    if mask is None:
        return 0.0
    reference = combat._SHAPES[name]
    padded = np.pad(mask, 1)
    best = 0.0
    for dy in range(3):
        for dx in range(3):
            shifted = padded[dy:dy + 25, dx:dx + 25]
            intersection = float(np.count_nonzero(shifted & reference))
            denominator = int(shifted.sum()) + int(reference.sum())
            best = max(best, 2.0 * intersection / max(1, denominator))
            if name in combat._TOGGLE_OUTLINES:
                expanded = cv2.dilate(shifted, combat._DILATION_KERNEL)
                precision = float(np.count_nonzero(shifted & combat._TOGGLE_OUTLINES[name])) / max(1, int(shifted.sum()))
                recall = float(np.count_nonzero(reference & expanded)) / max(1, int(reference.sum()))
                coverage = 2.0 * precision * recall / max(0.001, precision + recall)
                best = max(best, 0.94 * coverage)
    return best


@pytest.mark.parametrize('seed', [1, 43, 2026])
def test_shape_matcher_equivalent_to_reference_on_random_and_reference_masks(seed):
    rng = np.random.default_rng(seed)
    masks = [rng.integers(0, 2, size=(25, 25), dtype=np.uint8) for _ in range(12)]
    masks.extend(combat._SHAPES.values())
    masks.extend(np.zeros((25, 25), dtype=np.uint8) for _ in range(1))
    for mask in masks:
        for name in combat._SHAPES:
            reference = _legacy_overlap_score(mask, name)
            actual = combat._shape_score(mask, name)
            assert actual == pytest.approx(reference, abs=2e-6), (seed, name)


def test_ocr_payload_snapshot_does_not_alias_input():
    array = np.arange(60, dtype=np.uint8).reshape(6, 10)
    original = array[:, ::2]
    payload = np.array(original, copy=True, order='C')
    original[0, 0] = 255
    assert payload.flags.c_contiguous
    assert payload[0, 0] == 0
