from __future__ import annotations

from dofusic.models import PositionOCRResult

# Reference metrics calibrated against the actual Dofus HUD at the reference
# 29 px position-row height.  The overlay is intentionally derived from the
# accepted numeric token only; raw OCR suffix text and image pixels are never
# consulted here.
_REFERENCE_HEIGHT = 29.0
_LEFT_INSET = 7.0
_RIGHT_PADDING = 4.0
_ADVANCE = {
    '0': 12.4, '1': 12.4, '2': 12.4, '3': 12.4, '4': 12.4,
    '5': 12.4, '6': 12.4, '7': 12.4, '8': 12.4, '9': 12.4,
    ',': 5.3,
    ' ': 5.4,
    '-': 8.5,
    '+': 8.5,
}


def coordinate_token(result: PositionOCRResult) -> str:
    """Return the canonical Dofus coordinate token for an accepted OCR result."""
    coordinates = result.coordinates
    if coordinates is None:
        return ''
    return f'{coordinates.x}, {coordinates.y}'


def coordinate_overlay_width(result: PositionOCRResult, roi_height: int) -> int:
    """Return overlay width in ROI pixels from the accepted numeric token only.

    The function never reads ``result.raw_text`` except indirectly through the
    already-accepted ``x``/``y`` fields.  Therefore ``- Niveau ...`` cannot make
    the rectangle grow and unrelated image noise cannot cut the final digit.
    """
    token = coordinate_token(result)
    if not token:
        return 0
    height = max(1.0, float(roi_height))
    scale = height / _REFERENCE_HEIGHT
    reference_width = _LEFT_INSET + _RIGHT_PADDING
    for char in token:
        reference_width += _ADVANCE.get(char, 12.4)
    return max(1, int(round(reference_width * scale)))
