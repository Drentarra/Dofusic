from __future__ import annotations

import ctypes
import ntpath
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry, HUDTransform, estimate_hud_transform


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_basename(pid: int) -> str:
    """Return a Windows process image basename without a third-party dependency."""
    if os.name != 'nt' or int(pid) <= 0:
        return ''
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    query_name = kernel32.QueryFullProcessImageNameW
    query_name.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
    query_name.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    handle = open_process(_PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
    if not handle:
        return ''
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.c_uint32(len(buffer))
        if not query_name(handle, 0, buffer, ctypes.byref(length)):
            return ''
        return ntpath.basename(buffer.value[:length.value]).casefold()
    finally:
        close_handle(handle)



@dataclass(slots=True)
class CapturedFrame:
    image: np.ndarray
    source: str
    hwnd: int
    screen_rect: tuple[int, int, int, int] | None = None
    hud_transform: HUDTransform | None = None


def enable_dpi_awareness() -> None:
    if os.name != 'nt':
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class DofusCapture:
    """Capture the fixed Dofus HUD while keeping one cached window/process identity."""

    def __init__(
        self,
        *,
        process_name: str = 'dofus.exe',
        geometry: HUDGeometry | None = None,
        window_refresh_sec: float = 1.5,
        printwindow_cooldown_sec: float = 0.8,
    ) -> None:
        if os.name != 'nt':
            raise RuntimeError('DofusCapture nécessite Windows')
        enable_dpi_awareness()
        import mss
        import win32con
        import win32gui
        import win32process
        import win32ui

        self.mss_mod = mss
        self.win32con = win32con
        self.win32gui = win32gui
        self.win32process = win32process
        self.win32ui = win32ui
        self.sct = mss.MSS()

        self.process_name = process_name.lower()
        self.geometry = geometry or HUDGeometry()
        self.window_refresh_sec = float(window_refresh_sec)
        self.printwindow_cooldown_sec = float(printwindow_cooldown_sec)

        self.hwnd: Optional[int] = None
        self.pid: Optional[int] = None
        self.root_hwnd: Optional[int] = None
        self.last_window_refresh = 0.0
        self.last_printwindow = 0.0
        self._hud_scale: float | None = None

    def _root_and_pid(self, hwnd: int) -> tuple[int, int] | None:
        try:
            root = int(self.win32gui.GetAncestor(hwnd, self.win32con.GA_ROOT) or hwnd)
            _thread_id, pid = self.win32process.GetWindowThreadProcessId(root)
            return root, int(pid)
        except Exception:
            return None

    def _client_size(self, hwnd: int) -> Tuple[int, int]:
        try:
            left, top, right, bottom = self.win32gui.GetClientRect(hwnd)
            return max(0, right - left), max(0, bottom - top)
        except Exception:
            return 0, 0

    def _window_shape_valid(self, hwnd: int) -> bool:
        try:
            if not self.win32gui.IsWindow(hwnd):
                return False
            if not self.win32gui.IsWindowVisible(hwnd) or self.win32gui.IsIconic(hwnd):
                return False
            width, height = self._client_size(hwnd)
            return width > 300 and height > 200
        except Exception:
            return False

    def _cached_identity_valid(self, hwnd: int) -> bool:
        if hwnd != self.hwnd or self.pid is None or self.root_hwnd is None:
            return False
        if not self._window_shape_valid(hwnd):
            return False
        identity = self._root_and_pid(hwnd)
        return identity == (self.root_hwnd, self.pid)

    def _candidate_valid(self, hwnd: int) -> bool:
        if not self._window_shape_valid(hwnd):
            return False
        identity = self._root_and_pid(hwnd)
        if identity is None:
            return False
        _root, pid = identity
        return _process_basename(pid) == self.process_name

    def _valid_hwnd(self, hwnd: Optional[int]) -> bool:
        if not hwnd:
            return False
        if hwnd == self.hwnd and self.pid is not None and self.root_hwnd is not None:
            return self._cached_identity_valid(hwnd)
        return self._candidate_valid(hwnd)

    def _remember_identity(self, hwnd: int) -> bool:
        identity = self._root_and_pid(hwnd)
        if identity is None:
            return False
        if self.hwnd != int(hwnd):
            self._hud_scale = None
        self.hwnd = int(hwnd)
        self.root_hwnd, self.pid = identity
        return True

    def _clear_identity(self) -> None:
        self.hwnd = None
        self.pid = None
        self.root_hwnd = None
        self._hud_scale = None

    def _find_window(self) -> Optional[int]:
        now = time.monotonic()
        if self._valid_hwnd(self.hwnd) and now - self.last_window_refresh < self.window_refresh_sec:
            return self.hwnd

        self.last_window_refresh = now
        found: list[tuple[int, int]] = []

        def enum_window(hwnd: int, _extra) -> None:
            if self._candidate_valid(hwnd):
                width, height = self._client_size(hwnd)
                found.append((hwnd, width * height))

        try:
            self.win32gui.EnumWindows(enum_window, None)
        except Exception:
            found = []
        if not found:
            self._clear_identity()
            return None

        foreground = self.win32gui.GetForegroundWindow()
        selected = next((hwnd for hwnd, _area in found if hwnd == foreground), None)
        if selected is None:
            selected = max(found, key=lambda item: item[1])[0]
        if not self._remember_identity(selected):
            self._clear_identity()
            return None
        return self.hwnd

    def _client_capture_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        width, height = self._client_size(hwnd)
        if width <= 0 or height <= 0:
            return None
        x, y, roi_width, roi_height = self.geometry.capture_rect(width, height, ui_scale=self._hud_scale)
        if x >= width or y >= height:
            return None
        roi_width = min(roi_width, width - x)
        roi_height = min(roi_height, height - y)
        if roi_width <= 0 or roi_height <= 0:
            return None
        return x, y, roi_width, roi_height

    def _roi_screen_rect(self, hwnd: int) -> Optional[dict]:
        rect = self._client_capture_rect(hwnd)
        if rect is None:
            return None
        x, y, width, height = rect
        left, top = self.win32gui.ClientToScreen(hwnd, (x, y))
        return {'left': int(left), 'top': int(top), 'width': int(width), 'height': int(height)}

    def _roi_visible(self, hwnd: int) -> bool:
        rect = self._client_capture_rect(hwnd)
        if rect is None or self.pid is None:
            return False
        x, y, width, height = rect
        points = (
            (x + 6, y + 6),
            (x + max(6, width // 2), y + 6),
            (x + max(6, width - 6), y + 6),
            (x + 6, y + max(6, height - 6)),
            (x + max(6, width - 6), y + max(6, height - 6)),
        )
        try:
            visible = 0
            for point in points:
                screen_point = self.win32gui.ClientToScreen(hwnd, point)
                visible_hwnd = self.win32gui.WindowFromPoint(screen_point)
                identity = self._root_and_pid(visible_hwnd)
                if identity is not None and identity[1] == self.pid:
                    visible += 1
            return visible >= 3
        except Exception:
            return False

    def _capture_visible_roi(self, hwnd: int) -> Optional[np.ndarray]:
        if not self._roi_visible(hwnd):
            return None
        rect = self._roi_screen_rect(hwnd)
        if rect is None:
            return None
        try:
            shot = self.sct.grab(rect)
            image = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
            return image if image.size else None
        except Exception:
            return None

    def _capture_printwindow_roi(self, hwnd: int) -> Optional[np.ndarray]:
        client_width, client_height = self._client_size(hwnd)
        rect = self._client_capture_rect(hwnd)
        if rect is None:
            return None
        x, y, roi_width, roi_height = rect

        for flag in (3, 2, 1, 0):
            hwnd_dc = src_dc = mem_dc = bitmap = None
            try:
                hwnd_dc = self.win32gui.GetWindowDC(hwnd)
                src_dc = self.win32ui.CreateDCFromHandle(hwnd_dc)
                mem_dc = src_dc.CreateCompatibleDC()
                bitmap = self.win32ui.CreateBitmap()
                bitmap.CreateCompatibleBitmap(src_dc, client_width, client_height)
                mem_dc.SelectObject(bitmap)
                ok = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), flag)
                if not ok:
                    continue
                info = bitmap.GetInfo()
                bits = bitmap.GetBitmapBits(True)
                image = np.frombuffer(bits, dtype=np.uint8).reshape((info['bmHeight'], info['bmWidth'], 4))
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
                if image.size == 0 or float(np.mean(image)) < 2.0:
                    continue
                crop = image[y:y + roi_height, x:x + roi_width]
                if crop.size:
                    return np.ascontiguousarray(crop)
            except Exception:
                continue
            finally:
                try:
                    if bitmap:
                        self.win32gui.DeleteObject(bitmap.GetHandle())
                    if mem_dc:
                        mem_dc.DeleteDC()
                    if src_dc:
                        src_dc.DeleteDC()
                    if hwnd_dc:
                        self.win32gui.ReleaseDC(hwnd, hwnd_dc)
                except Exception:
                    pass
        return None

    def capture(self) -> CapturedFrame | None:
        hwnd = self._find_window()
        if not hwnd:
            return None

        image = self._capture_visible_roi(hwnd)
        source = 'visible'
        if image is None:
            now = time.monotonic()
            if now - self.last_printwindow < self.printwindow_cooldown_sec:
                return None
            self.last_printwindow = now
            image = self._capture_printwindow_roi(hwnd)
            source = 'printwindow'
        if image is None:
            return None
        rect = self._roi_screen_rect(hwnd)
        screen_rect = None if rect is None else (rect['left'], rect['top'], rect['width'], rect['height'])
        transform = estimate_hud_transform(image, self.geometry)
        frame = CapturedFrame(
            image=image,
            source=source,
            hwnd=hwnd,
            screen_rect=screen_rect,
            hud_transform=transform,
        )
        # Calibration belongs to the captured frame and is reused by the app.
        # Only the *next* capture adopts the new height, so screen_rect and image
        # can never describe different coordinate systems.
        if transform is not None:
            self._hud_scale = float(transform.scale)
        return frame

    def close(self) -> None:
        try:
            self.sct.close()
        except Exception:
            pass
