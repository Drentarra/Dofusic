"""Canonical Dofusic version and versioned build artifact naming."""

import re

__version__ = "2.0.0"


def portable_zip_name(version: str | None = None) -> str:
    """Derive the portable archive name without duplicating a release number."""
    value = __version__ if version is None else version
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError(f"Invalid application version: {value!r}")
    return f"Dofusic_{value}_LOCAL_WINDOWS_X64_AVEC_MUSIQUES.zip"
