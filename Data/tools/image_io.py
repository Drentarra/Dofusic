"""Unicode-safe loading of HUD reference images.

OpenCV's ``imread`` does not reliably accept Windows Unicode paths.  Python's
``Path.read_bytes`` handles those paths; OpenCV then decodes the bytes without
needing to interpret a filename.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def read_image(path: str | Path, *, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    """Decode an image regardless of Unicode characters in its filesystem path.

    A missing file raises FileNotFoundError; corrupt or unsupported image bytes
    raise ValueError so a broken fixture cannot silently pass a test.
    """
    source = Path(path)
    encoded = np.frombuffer(source.read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(encoded, flags)
    if image is None or image.size == 0:
        raise ValueError(f"Image non décodable : {source}")
    return image
