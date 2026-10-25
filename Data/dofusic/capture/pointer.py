"""Best-effort foreground pointer query; no mouse hooks or polling worker."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


def screen_cursor_position() -> tuple[int, int] | None:
    """Return physical screen coordinates on Windows, or None if unavailable."""
    if os.name != 'nt':
        return None
    point = wintypes.POINT()
    try:
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
    except (AttributeError, OSError, ValueError):
        return None
    return int(point.x), int(point.y)
