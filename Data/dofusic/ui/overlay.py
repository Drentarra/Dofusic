from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import tkinter as tk
import time
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk


Rect = tuple[int, int, int, int]


def native_toplevel_hwnd(tk_hwnd: int, *, get_parent=None) -> int:
    """Return the native Win32 wrapper HWND for a Tk toplevel."""
    hwnd = int(tk_hwnd)
    if get_parent is None:
        if os.name != 'nt':
            return hwnd
        user32 = ctypes.windll.user32
        get_parent = user32.GetParent
        get_parent.argtypes = [wintypes.HWND]
        get_parent.restype = wintypes.HWND
    try:
        parent = int(get_parent(hwnd) or 0)
    except Exception:
        parent = 0
    return parent or hwnd




def position_overlay_window(
    hwnd: int,
    rect: Rect,
    *,
    set_window_pos=None,
) -> bool:
    """Position the overlay with absolute Win32 screen coordinates.

    Tk interprets negative geometry offsets relative to the right/bottom edge.
    The OCR overlay can legitimately start at a negative coordinate because of
    its small padding (for example x=-4 when Dofus starts at x=0), so native
    positioning is required.  SWP_NOZORDER and SWP_NOACTIVATE guarantee this
    operation cannot reorder or focus Dofus/Dofusic.
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    x, y, width, height = (int(v) for v in rect)
    width = max(1, width)
    height = max(1, height)

    if set_window_pos is None:
        if os.name != 'nt':
            return False
        user32 = ctypes.windll.user32
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL

    HWND_TOP = 0  # ignored because SWP_NOZORDER is set
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    try:
        return bool(set_window_pos(hwnd, HWND_TOP, x, y, width, height, flags))
    except Exception:
        return False


def set_overlay_topmost(
    hwnd: int,
    enabled: bool,
    *,
    set_window_pos=None,
) -> bool:
    """Move only the passive overlay between topmost and normal Z bands.

    The call never activates the overlay and never touches the Dofus or
    Dofusic HWNDs. This keeps the tracking outlines above Dofus while the game
    is active, then returns them to the normal Z band as soon as Dofusic takes
    focus.
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False

    if set_window_pos is None:
        if os.name != 'nt':
            return False
        user32 = ctypes.windll.user32
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL

    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    insert_after = HWND_TOPMOST if bool(enabled) else HWND_NOTOPMOST
    flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
    try:
        return bool(set_window_pos(hwnd, insert_after, 0, 0, 0, 0, flags))
    except Exception:
        return False


def exclude_window_from_capture(
    hwnd: int,
    *,
    set_window_display_affinity=None,
) -> bool:
    """Exclude this process-owned overlay from Windows screen capture.

    WDA_EXCLUDEFROMCAPTURE (0x11) is available on modern Windows 10/11.
    The call applies only to the passive overlay HWND; Dofus itself is never
    modified.  Returning False is intentionally non-fatal so unsupported
    Windows builds still run normally.
    """
    hwnd = int(hwnd or 0)
    if not hwnd:
        return False
    if set_window_display_affinity is None:
        if os.name != 'nt':
            return False
        user32 = ctypes.windll.user32
        set_window_display_affinity = user32.SetWindowDisplayAffinity
        set_window_display_affinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        set_window_display_affinity.restype = wintypes.BOOL
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        return bool(set_window_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def rounded_corner_radius(width: int, height: int, requested: int = 6) -> int:
    """Return a restrained radius so short OCR slots never become pills."""
    width = max(0, int(width))
    height = max(0, int(height))
    requested = max(0, int(requested))
    if width <= 1 or height <= 1:
        return 0
    return min(requested, max(0, (width - 2) // 2), max(0, (height - 2) // 2))



def build_merged_outline_mask(
    zone_rect: Rect,
    position_rect: Rect,
    union_rect: Rect,
    *,
    radius: int = 6,
    line_width: int = 2,
) -> Image.Image:
    """Render the *union* outline of the two OCR tracking rectangles.

    The Dofus HUD puts the position line slightly underneath/inside the zone
    line. Drawing two independent rectangle outlines therefore creates a
    visible horizontal separator.  Building a filled union first and then
    extracting only its inner boundary produces the intended stepped contour:
    both tracking areas are represented, but their shared edge is absent.
    """
    left, top, width, height = (int(v) for v in union_rect)
    width = max(1, width)
    height = max(1, height)
    radius = max(0, int(radius))
    line_width = max(1, int(line_width))

    filled = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(filled)

    for rect in (zone_rect, position_rect):
        x, y, rect_width, rect_height = (int(v) for v in rect)
        if rect_width <= 0 or rect_height <= 0:
            continue
        x1 = x - left
        y1 = y - top
        x2 = x1 + rect_width - 1
        y2 = y1 + rect_height - 1
        rect_radius = rounded_corner_radius(rect_width, rect_height, radius)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=rect_radius, fill=255)

    # The two HUD crops share the same left edge and overlap vertically by a
    # few pixels.  Their *internal* rounded corners must not survive the union,
    # otherwise they create a small inward notch on the left border.  Bridge
    # only that seam; the true outer top-left and bottom-left corners stay
    # rounded.
    zx, zy, zw, zh = (int(v) for v in zone_rect)
    px, py, pw, ph = (int(v) for v in position_rect)
    zone_bottom = zy + zh - 1
    position_bottom = py + ph - 1
    if zx == px and zy <= py <= zone_bottom:
        bridge_radius = min(
            rounded_corner_radius(zw, zh, radius),
            rounded_corner_radius(pw, ph, radius),
        )
        if bridge_radius > 0:
            bridge_x1 = zx - left
            bridge_x2 = bridge_x1 + bridge_radius
            bridge_y1 = max(zy, py - bridge_radius) - top
            bridge_y2 = min(position_bottom, zone_bottom + bridge_radius) - top
            draw.rectangle(
                (bridge_x1, bridge_y1, bridge_x2, bridge_y2),
                fill=255,
            )

    # MinFilter performs an erosion.  Subtracting the eroded union from the
    # original union leaves a clean inside-only border and, importantly, no
    # line where the two OCR rectangles overlap.
    kernel = (line_width * 2) + 1
    eroded = filled.filter(ImageFilter.MinFilter(kernel))
    return ImageChops.subtract(filled, eroded)

def overlay_bounds(*rects: Rect, pad: int = 4) -> Rect:
    """Return one screen rectangle containing every tracking region plus margin."""
    if not rects:
        raise ValueError('au moins un rectangle est requis')
    pad = max(0, int(pad))
    normalized = [tuple(int(v) for v in rect) for rect in rects]
    left = min(x for x, _y, _w, _h in normalized) - pad
    top = min(y for _x, y, _w, _h in normalized) - pad
    right = max(x + width for x, _y, width, _height in normalized) + pad
    bottom = max(y + height for _x, y, _width, height in normalized) + pad
    return left, top, max(1, right - left), max(1, bottom - top)


def overlay_local_rects(rects: tuple[Rect, ...], bounds: Rect) -> tuple[Rect, ...]:
    """Translate screen rectangles into overlay-local coordinates.

    Pure screen movement then leaves the drawable shape unchanged, allowing the
    native overlay window to move without rebuilding its PIL/Tk image.
    """
    left, top, _width, _height = (int(v) for v in bounds)

    def local(rect: Rect) -> Rect:
        x, y, width, height = (int(v) for v in rect)
        return x - left, y - top, width, height

    return tuple(local(rect) for rect in rects)


def build_tracking_outline_mask(
    combat_rect: Rect,
    zone_rect: Rect,
    position_rect: Rect,
    bounds: Rect,
    *,
    radius: int = 6,
    line_width: int = 2,
) -> Image.Image:
    """Render all tracking contours on one transparent-overlay mask.

    The combat button is a separate rounded rectangle. Zone and position keep
    their historical merged contour so the overlapping OCR slots never show an
    artificial horizontal separator.
    """
    mask = build_merged_outline_mask(
        zone_rect,
        position_rect,
        bounds,
        radius=radius,
        line_width=line_width,
    )
    left, top, _width, _height = (int(v) for v in bounds)
    x, y, width, height = (int(v) for v in combat_rect)
    if width <= 0 or height <= 0:
        return mask
    x1 = x - left
    y1 = y - top
    x2 = x1 + width - 1
    y2 = y1 + height - 1
    rect_radius = rounded_corner_radius(width, height, radius)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=rect_radius,
        outline=255,
        width=max(1, int(line_width)),
    )
    return mask


@dataclass(slots=True)
class _OverlayWindow:
    top: tk.Toplevel
    canvas: tk.Canvas
    native_hwnd: int | None = None
    visible: bool = False
    geometry: Rect | None = None
    drawn_rects: tuple[Rect, Rect, Rect] | None = None
    image_ref: object | None = None
    capture_excluded: bool = False
    last_exclusion_attempt_at: float = 0.0


class TrackingOverlay:
    """One passive screen overlay containing combat, zone and position outlines.

    The overlay is deliberately *not* owned by Dofus and never manipulates the
    Dofus HWND. It remains visible while either Dofus or the Dofusic UI is
    foreground, but it never owns or reorders the Dofus window. When focus
    returns to Dofus, only the passive overlay itself is raised once without
    activation. Other applications hide the overlay.
    """

    CHROMA = '#010203'
    BORDER = '#ffffff'
    FILL = ''
    PAD = 4
    RADIUS = 6
    ALPHA = 0.74
    LINE_WIDTH = 2

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.enabled = os.name == 'nt'
        self.window = self._make_window() if self.enabled else None
        self._last_foreground_role: str | None = None

    def _make_window(self) -> _OverlayWindow:
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=self.CHROMA)
        try:
            top.attributes('-transparentcolor', self.CHROMA)
        except tk.TclError:
            pass
        try:
            top.attributes('-alpha', self.ALPHA)
        except tk.TclError:
            pass

        canvas = tk.Canvas(top, bg=self.CHROMA, highlightthickness=0, bd=0)
        canvas.pack(fill='both', expand=True)
        top.update_idletasks()
        window = _OverlayWindow(top=top, canvas=canvas)
        self._configure_passive_window(window)
        return window

    @staticmethod
    def _configure_passive_window(window: _OverlayWindow) -> None:
        """Make the overlay click-through/no-activate without assigning an owner."""
        if os.name != 'nt':
            return
        user32 = ctypes.windll.user32
        hwnd = native_toplevel_hwnd(int(window.top.winfo_id()))
        window.native_hwnd = hwnd

        if hasattr(user32, 'GetWindowLongPtrW'):
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOACTIVATE = 0x08000000

        style = int(get_long(hwnd, GWL_EXSTYLE))
        style |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
        set_long(hwnd, GWL_EXSTYLE, ctypes.c_ssize_t(style).value)
        window.capture_excluded = exclude_window_from_capture(hwnd)
        window.last_exclusion_attempt_at = time.monotonic()

    def _ensure_capture_excluded(self) -> bool:
        """Fail closed: never show an overlay that can enter MSS capture."""
        window = getattr(self, 'window', None)
        if window is None:
            return False
        if bool(getattr(window, 'capture_excluded', False)):
            return True
        now = time.monotonic()
        last_attempt = float(getattr(window, 'last_exclusion_attempt_at', 0.0) or 0.0)
        if last_attempt > 0.0 and now - last_attempt < 1.0:
            return False
        hwnd = int(getattr(window, 'native_hwnd', 0) or native_toplevel_hwnd(int(window.top.winfo_id())))
        window.native_hwnd = hwnd
        window.last_exclusion_attempt_at = now
        window.capture_excluded = exclude_window_from_capture(hwnd)
        return bool(window.capture_excluded)

    def _foreground_role(self, target_hwnd: int) -> str:
        """Classify the active top-level window without changing Z-order.

        Returns ``dofus`` when the game is active, ``dofusic`` when this UI is
        active, and ``other`` for every other application.
        """
        if os.name != 'nt' or not target_hwnd:
            return 'other'
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            GA_ROOT = 2

            foreground = int(user32.GetForegroundWindow() or 0)
            if not foreground:
                return 'other'
            foreground_root = int(user32.GetAncestor(foreground, GA_ROOT) or foreground)
            target_root = int(user32.GetAncestor(int(target_hwnd), GA_ROOT) or int(target_hwnd))

            ui_hwnd = native_toplevel_hwnd(int(self.root.winfo_id()))
            ui_root = int(user32.GetAncestor(int(ui_hwnd), GA_ROOT) or int(ui_hwnd))

            if foreground_root == target_root:
                return 'dofus'
            if foreground_root == ui_root:
                return 'dofusic'
            return 'other'
        except Exception:
            return 'other'

    def _set_topmost(self, enabled: bool) -> None:
        """Change only the overlay Z band, and only when the state changes."""
        window = self.window
        if window is None:
            return
        enabled = bool(enabled)
        if getattr(self, '_overlay_topmost', None) is enabled:
            return
        hwnd = int(window.native_hwnd or native_toplevel_hwnd(int(window.top.winfo_id())))
        window.native_hwnd = hwnd
        if set_overlay_topmost(hwnd, enabled):
            self._overlay_topmost = enabled

    @classmethod
    def _draw_tracking_outline(
        cls,
        canvas: tk.Canvas,
        combat_rect: Rect,
        zone_rect: Rect,
        position_rect: Rect,
        *,
        geometry: Rect,
    ) -> object:
        """Draw all tracking contours on the single passive overlay surface."""
        _, _, width, height = geometry
        mask = build_tracking_outline_mask(
            combat_rect,
            zone_rect,
            position_rect,
            geometry,
            radius=cls.RADIUS,
            line_width=cls.LINE_WIDTH,
        )
        image = Image.new('RGB', (int(width), int(height)), cls.CHROMA)
        image.paste((255, 255, 255), mask=mask)
        photo = ImageTk.PhotoImage(image=image, master=canvas)
        canvas.create_image(0, 0, image=photo, anchor='nw')
        return photo

    def _place(
        self,
        combat_rect: Rect,
        zone_rect: Rect,
        position_rect: Rect,
        zone_key: str | None = None,
    ) -> None:
        """Move the surface cheaply; redraw only when its local shape changes."""
        window = self.window
        if window is None:
            return

        screen_rects = (combat_rect, zone_rect, position_rect)
        geometry = overlay_bounds(*screen_rects, pad=self.PAD)
        left, top, width, height = geometry
        local_rects = overlay_local_rects(screen_rects, geometry)
        old_geometry = window.geometry
        geometry_changed = old_geometry != geometry
        size_changed = old_geometry is None or old_geometry[2:] != geometry[2:]
        was_visible = window.visible

        if size_changed:
            window.canvas.configure(width=width, height=height)
        window.geometry = geometry

        if window.drawn_rects != local_rects or size_changed:
            window.canvas.delete('all')
            local_geometry = (0, 0, width, height)
            window.image_ref = self._draw_tracking_outline(
                window.canvas,
                local_rects[0],
                local_rects[1],
                local_rects[2],
                geometry=local_geometry,
            )
            window.drawn_rects = local_rects

        if not window.visible:
            window.top.deiconify()
            window.visible = True

        if geometry_changed or not was_visible:
            hwnd = int(window.native_hwnd or native_toplevel_hwnd(int(window.top.winfo_id())))
            window.native_hwnd = hwnd
            position_overlay_window(hwnd, geometry)

    def update(
        self,
        hwnd: int | None,
        combat_rect: Rect | None,
        zone_rect: Rect | None,
        position_rect: Rect | None,
        zone_key: str | None = None,
    ) -> None:
        if (
            not self.enabled
            or not hwnd
            or combat_rect is None
            or zone_rect is None
            or position_rect is None
        ):
            self.hide()
            return

        role = self._foreground_role(int(hwnd))
        if role == 'other':
            self.hide()
            return

        if getattr(self, 'window', None) is not None and not self._ensure_capture_excluded():
            self.hide()
            return

        self._place(combat_rect, zone_rect, position_rect, zone_key)
        self._set_topmost(role == 'dofus')
        self._last_foreground_role = role

    def hide(self) -> None:
        window = self.window
        if window is None or not window.visible:
            return
        try:
            window.top.withdraw()
        except tk.TclError:
            pass
        finally:
            window.visible = False
            self._last_foreground_role = None
            self._overlay_topmost = None

    def close(self) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.top.destroy()
        except tk.TclError:
            pass
        finally:
            window.visible = False
