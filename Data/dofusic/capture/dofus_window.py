from __future__ import annotations

import ctypes
import ntpath
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from dofusic.vision.layout import HUDGeometry, HUDTransform, hud_transform_from_client_size

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


def client_fills_monitor(
    client_rect: tuple[int, int, int, int],
    monitor_rect: tuple[int, int, int, int],
    *, tolerance: int = 3,
) -> bool:
    """Accept exclusive or borderless fullscreen, never a maximized client area.

    Rectangles are screen coordinates (left, top, right, bottom). Compare
    against the physical monitor, not its work area (which excludes the taskbar).
    """
    left, top, right, bottom = client_rect
    ml, mt, mr, mb = monitor_rect
    return (
        right > left and bottom > top and mr > ml and mb > mt
        and all(abs(actual - expected) <= tolerance for actual, expected in zip(
            (left, top, right, bottom), (ml, mt, mr, mb)
        ))
    )


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
        import win32api
        import win32con
        import win32gui
        import win32process
        import win32ui

        self.win32con = win32con
        self.win32api = win32api
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
        self.window_mode = 'missing'
        self._last_client_size: tuple[int, int] | None = None
        self._resolution_transform: HUDTransform | None = None

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
            self._last_client_size = None
            self._resolution_transform = None
        self.hwnd = int(hwnd)
        self.root_hwnd, self.pid = identity
        return True

    def _clear_identity(self) -> None:
        self.hwnd = None
        self.pid = None
        self.root_hwnd = None
        self._last_client_size = None
        self._resolution_transform = None

    def _find_window(self) -> Optional[int]:
        now = time.monotonic()
        if now - self.last_window_refresh < self.window_refresh_sec:
            if self.hwnd is None:
                # No game yet: avoid EnumWindows and process queries on every
                # 10 FPS idle capture tick. Still retry after the refresh delay.
                return None
            if self._valid_hwnd(self.hwnd):
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

    def _resolution_hud(self, hwnd: int) -> HUDTransform | None:
        """Single authoritative HUD geometry from Dofus' Win32 client size.

        The game HUD scale comes from the offline-fitted resolution curve.
        Its origin is client (0, 0). Nothing in a captured image (notifications, inventory,
        map art) can modify this geometry. Cache until client size or game HWND
        changes. The right edges of combat/zone are measured elsewhere.
        """
        size = self._client_size(hwnd)
        if size != getattr(self, '_last_client_size', None) or not hasattr(self, '_resolution_transform'):
            self._last_client_size = size
            self._resolution_transform = hud_transform_from_client_size(*size, geometry=self.geometry)
        return self._resolution_transform

    def _client_capture_rect(self, hwnd: int) -> tuple[int, int, int, int] | None:
        transform = self._resolution_hud(hwnd)
        if transform is None:
            return None
        width, height = self._last_client_size
        # The reference strip ends at 106px at 1x; the cached resolution
        # curve determines the strip height, with no live HUD search.
        x, y, roi_width, roi_height = self.geometry.capture_rect(
            width, height, ui_scale=transform.scale,
        )
        if x >= width or y >= height:
            return None
        roi_width = min(roi_width, width - x)
        roi_height = min(roi_height, height - y)
        if roi_width <= 0 or roi_height <= 0:
            return None
        return x, y, roi_width, roi_height

    def _display_mode(self, hwnd: int) -> str:
        """Inspect the actual Dofus *client* against its own monitor.

        Maximized windows still have borders/title bar and therefore fail.
        Any Win32 failure is unknown, never mistaken for fullscreen.
        """
        try:
            width, height = self._client_size(hwnd)
            left, top = self.win32gui.ClientToScreen(hwnd, (0, 0))
            monitor = self.win32api.MonitorFromWindow(
                hwnd, self.win32con.MONITOR_DEFAULTTONEAREST,
            )
            monitor_rect = tuple(self.win32api.GetMonitorInfo(monitor)['Monitor'])
            rect = (int(left), int(top), int(left + width), int(top + height))
            return 'fullscreen' if client_fills_monitor(rect, monitor_rect) else 'windowed'
        except Exception:
            return 'unknown'

    def _roi_screen_rect(self, hwnd: int) -> Optional[dict]:
        rect = self._client_capture_rect(hwnd)
        if rect is None:
            return None
        x, y, width, height = rect
        left, top = self.win32gui.ClientToScreen(hwnd, (x, y))
        return {'left': int(left), 'top': int(top), 'width': int(width), 'height': int(height)}

    @staticmethod
    def _is_own_excluded_overlay(root_hwnd: int, owner_pid: int) -> bool:
        """Treat only our capture-excluded Tk overlay as visually transparent.

        WindowFromPoint may return a layer above Dofus even though MSS omits
        that layer. Other processes' windows are never exempted.
        """
        if os.name != 'nt' or owner_pid != os.getpid() or not root_hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            get_affinity = user32.GetWindowDisplayAffinity
            get_affinity.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_affinity.restype = ctypes.c_int
            affinity = ctypes.c_uint32(0)
            return bool(get_affinity(int(root_hwnd), ctypes.byref(affinity))) and affinity.value == 0x11
        except Exception:
            return False

    def _roi_visible(self, hwnd: int) -> bool:
        # Passive click-through overlays can be reported by WindowFromPoint
        # instead of the underlying Dofus client. A verified foreground Dofus
        # process is independent, positive visibility evidence; avoid falling
        # into PrintWindow-only capture because of our own tracking outlines.
        try:
            foreground = int(self.win32gui.GetForegroundWindow() or 0)
            identity = self._root_and_pid(foreground) if foreground else None
            if identity is not None and identity == (self.root_hwnd, self.pid):
                return True
        except Exception:
            pass
        rect = self._client_capture_rect(hwnd)
        if rect is None or self.pid is None:
            return False
        x, y, width, height = rect
        # Only the top-left HUD is captured and interpreted. Sampling the
        # *middle and far right of the entire client* falsely declares a
        # window invisible when it is moved partly beyond a monitor edge,
        # despite all combat/zone controls remaining onscreen. Check the HUD
        # itself, spreading samples across toolbar, zone and coordinates.
        offsets = ((6, 6), (18, 26), (85, 56), (45, 86), (190, 18))
        points = tuple((x + min(width - 1, px), y + min(height - 1, py))
                       for px, py in offsets)
        try:
            visible = 0
            for point in points:
                screen_point = self.win32gui.ClientToScreen(hwnd, point)
                visible_hwnd = self.win32gui.WindowFromPoint(screen_point)
                identity = self._root_and_pid(visible_hwnd)
                if identity is not None and (
                    identity[1] == self.pid
                    or self._is_own_excluded_overlay(identity[0], identity[1])
                ):
                    visible += 1
            return visible >= 3
        except Exception:
            return False

    def _capture_visible_roi(
        self, hwnd: int,
    ) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
        """Return the pixels WITH their actual capture-time screen rectangle.

        A dragged window may move between MSS.grab and the controller tick;
        asking ClientToScreen a second time would project the old pixels onto
        the *new* location and make the outlines jump independently of OCR.
        """
        if not self._roi_visible(hwnd):
            return None
        rect = self._roi_screen_rect(hwnd)
        if rect is None:
            return None
        try:
            shot = self.sct.grab(rect)
            image = cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)
            if image.size == 0:
                return None
            return image, (rect['left'], rect['top'], rect['width'], rect['height'])
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
            self.window_mode = 'missing'
            return None

        self.window_mode = self._display_mode(hwnd)
        if self.window_mode != 'fullscreen':
            # Do not grab pixels, calibrate HUD or submit OCR while windowed.
            return None

        visible_frame = self._capture_visible_roi(hwnd)
        source = 'visible'
        if visible_frame is None:
            now = time.monotonic()
            if now - self.last_printwindow < self.printwindow_cooldown_sec:
                return None
            self.last_printwindow = now
            image = self._capture_printwindow_roi(hwnd)
            source = 'printwindow'
            # PrintWindow is never projected onto the live screen as an overlay.
            screen_rect = None
        else:
            image, screen_rect = visible_frame
        if image is None:
            return None
        # Deterministic resolution-only transform. No image/pixel-based
        # calibration, no temporal smoother, no Discord-induced false scales.
        transform = self._resolution_hud(hwnd)
        if transform is None:
            return None
        frame = CapturedFrame(
            image=image,
            source=source,
            hwnd=hwnd,
            screen_rect=screen_rect,
            hud_transform=transform,
        )
        return frame

    def close(self) -> None:
        try:
            self.sct.close()
        except Exception:
            pass
