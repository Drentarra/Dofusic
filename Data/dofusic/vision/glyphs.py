"""Shared, bounded silhouette matching for HUD geometry and state detection."""
from functools import lru_cache

import cv2
import numpy as np

from dofusic.vision.combat_shapes import GLYPH_ROWS

_GLYPH_SIZE = 25


def _glyph_mask(patch: np.ndarray) -> np.ndarray | None:
    """Extract a silhouette using contrast against its own corner background.

    Absolute RGB, hue, and whether the glyph is lighter or darker than its
    background are immaterial. The strongest channel difference also preserves
    coloured glyphs with similar grayscale luminance to their theme background.
    """
    if patch.shape[:2] != (_GLYPH_SIZE, _GLYPH_SIZE):
        return None
    if patch.ndim == 2:
        patch = patch[:, :, None]
    corners = np.concatenate((
        patch[:2, :2].reshape(-1, patch.shape[2]),
        patch[:2, -2:].reshape(-1, patch.shape[2]),
        patch[-2:, :2].reshape(-1, patch.shape[2]),
        patch[-2:, -2:].reshape(-1, patch.shape[2]),
    ))
    background = np.median(corners, axis=0)
    contrast = np.abs(patch.astype(np.float32) - background).max(axis=2)
    peak = float(np.percentile(contrast, 98))
    if peak < 5:
        return None
    mask = (contrast > max(4.0, peak * 0.34)).astype(np.uint8)
    if not 8 <= int(mask.sum()) <= 480:
        return None
    return mask


def _shape_score(mask: np.ndarray | None, name: str) -> float:
    """Compare binary shapes, tolerating small resampling/position differences."""
    if mask is None:
        return 0.0
    return _cached_shape_score(mask.tobytes(), name)


@lru_cache(maxsize=256)
def _cached_shape_score(pixels: bytes, name: str) -> float:
    # HUD silhouettes usually stay identical between frames even when the map
    # animates. Cache only the binary patch/name, bounded to about 160 KiB of
    # pixel keys; every frame still extracts the currently visible glyphs.
    mask = np.frombuffer(pixels, dtype=np.uint8).reshape(_GLYPH_SIZE, _GLYPH_SIZE)
    reference = _SHAPES[name]
    # A symmetric overlap prevents a partial icon or arbitrary solid window
    # from matching just a small fragment of a known glyph.
    shifted = np.lib.stride_tricks.sliding_window_view(np.pad(mask, 1), (_GLYPH_SIZE, _GLYPH_SIZE))
    areas = np.count_nonzero(shifted, axis=(-2, -1))
    intersection = np.count_nonzero(shifted & reference, axis=(-2, -1))
    best = float(np.max(2.0 * intersection / np.maximum(1, areas + _SHAPE_AREAS[name])))
    if name in _TOGGLE_OUTLINES:
        # The +/- strokes are only 1-2 pixels thick. Their reference pixels are
        # well inside the patch, so dilating once before shifting gives the same
        # coverage as nine per-shift dilations, including at fractional scales.
        expanded = cv2.dilate(mask, _DILATION_KERNEL)
        expanded_shifts = np.lib.stride_tricks.sliding_window_view(np.pad(expanded, 1), (_GLYPH_SIZE, _GLYPH_SIZE))
        precision = np.count_nonzero(shifted & _TOGGLE_OUTLINES[name], axis=(-2, -1)) / np.maximum(1, areas)
        recall = np.count_nonzero(reference & expanded_shifts, axis=(-2, -1)) / _SHAPE_AREAS[name]
        coverage = 2.0 * precision * recall / np.maximum(0.001, precision + recall)
        best = max(best, float(np.max(0.94 * coverage)))
    return best


_SHAPES = {
    name: np.array([[(row >> x) & 1 for x in range(_GLYPH_SIZE)] for row in rows], dtype=np.uint8)
    for name, rows in GLYPH_ROWS.items()
}
_SHAPE_AREAS = {name: int(np.count_nonzero(shape)) for name, shape in _SHAPES.items()}
_DILATION_KERNEL = np.ones((3, 3), dtype=np.uint8)
_TOGGLE_OUTLINES = {
    name: cv2.dilate(_SHAPES[name], _DILATION_KERNEL)
    for name in ('plus', 'minus')
}
