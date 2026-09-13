from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from dofusic.ui.overlay import native_toplevel_hwnd


def colorref_from_hex(value: str) -> int:
    """Convert #RRGGBB to Win32 COLORREF (0x00BBGGRR)."""
    raw = str(value or '').strip().lstrip('#')
    if len(raw) != 6:
        raise ValueError(f'Couleur invalide: {value!r}')
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return red | (green << 8) | (blue << 16)


def _set_dwm_color(dwm, hwnd: int, attr: int, value: int) -> None:
    color = ctypes.c_uint32(int(value))
    dwm.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_uint(attr),
        ctypes.byref(color),
        ctypes.sizeof(color),
    )


def apply_window_chrome(window, palette) -> None:
    """Give every Tk toplevel the same themed native Windows caption.

    Exact caption/border/text colors are supported by modern Windows 11. On
    older Windows builds the immersive-dark attribute still removes the bright
    white title bar, so the function safely degrades instead of failing.
    """
    if os.name != 'nt':
        return
    try:
        window.update_idletasks()
        hwnd = native_toplevel_hwnd(int(window.winfo_id()))
        dwm = ctypes.windll.dwmapi

        dark = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            try:
                result = dwm.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd),
                    ctypes.c_uint(attr),
                    ctypes.byref(dark),
                    ctypes.sizeof(dark),
                )
                if result == 0:
                    break
            except Exception:
                continue

        # Windows 11: border / caption / text colors.
        for attr, color in (
            (34, palette.border),
            (35, palette.bg),
            (36, palette.text),
        ):
            try:
                _set_dwm_color(dwm, hwnd, attr, colorref_from_hex(color))
            except Exception:
                continue
    except Exception:
        pass


def schedule_window_chrome(window, palette, *, applier=None):
    """Refresh native caption colors after the active Tk event has finished.

    On Windows, changing DWM attributes synchronously from a combobox/theme
    callback can momentarily unmap a Tk toplevel. Deferring the native call
    keeps Tk's own event/state transition complete before touching the HWND.
    """
    callback = applier or apply_window_chrome

    def refresh():
        try:
            if hasattr(window, 'winfo_exists') and not window.winfo_exists():
                return
        except Exception:
            return
        try:
            callback(window, palette)
        except Exception:
            pass

    try:
        return window.after_idle(refresh)
    except Exception:
        return None
