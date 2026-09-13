from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image


_ASSET_DIR = Path(__file__).resolve().parent / 'assets'
BRAND_PNG_PATH = _ASSET_DIR / 'dofusic.png'
BRAND_ICO_PATH = _ASSET_DIR / 'dofusic.ico'


@lru_cache(maxsize=16)
def _resized_logo(size: int) -> Image.Image:
    with Image.open(BRAND_PNG_PATH) as source:
        image = source.convert('RGBA')
    return image.resize((size, size), Image.Resampling.LANCZOS)


def create_dofusic_logo(size: int = 128) -> Image.Image:
    """Return the canonical Dofusic icon resized for the UI."""
    normalized_size = max(16, int(size))
    return _resized_logo(normalized_size).copy()
