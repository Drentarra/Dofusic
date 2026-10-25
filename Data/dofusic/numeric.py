"""Finite confidence values shared by OCR, decision and position ranking."""
from __future__ import annotations

import math


def clamp_confidence(value: object, *, default: float = 0.0) -> float:
    """Return a bounded confidence, treating malformed/non-finite input as zero.

    A NaN or an infinity must never be interpreted as certain recognition.
    Threshold settings can provide their original safe default instead.
    """
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(confidence):
        return default
    return max(0.0, min(1.0, confidence))
