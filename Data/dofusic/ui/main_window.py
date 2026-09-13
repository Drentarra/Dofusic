from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

from dofusic.app import DofusicController
from dofusic.config import AppConfig, save_config
from dofusic.online.session import MusicSession
from dofusic.ui.brand import create_dofusic_logo
from dofusic.ui.music_window import MusicWindow
from dofusic.ui.settings_window import SettingsWindow
from dofusic.ui.themes import get_theme, theme_color_mapping
from dofusic.ui.window_chrome import apply_window_chrome, schedule_window_chrome
from dofusic.ui.volume import VolumeGesture, mute_visual_active
from dofusic.ui.overlay import TrackingOverlay
from dofusic.version import __version__

BG = '#06100e'
BG_TOP = '#071411'
CARD = '#0b1a17'
CARD_ALT = '#0a1715'
CARD_FIELD = '#0a1515'
BORDER = '#214139'
BORDER_GREEN = '#197a55'
TEXT = '#f4f8f6'
MUTED = '#8fa69f'
GREEN = '#43e39e'
GREEN_SOFT = '#91f0c1'
GREEN_DARK = '#0d3025'
AMBER = '#f0c56c'
RED = '#ff7c7c'
TRACK = '#233b35'
TRACK_ACTIVE = '#39d995'

VISUALIZER_BANDS = 24
VISUALIZER_FPS = 24
VISUALIZER_INTERVAL_MS = max(1, round(1000 / VISUALIZER_FPS))


def restore_secondary_window(secondary) -> bool:
    """Bring an existing secondary window back even if Windows/Tk hid it."""
    if secondary is None:
        return False
    try:
        window = secondary.window
        if not window.winfo_exists():
            return False
        try:
            state = str(window.state() or '').lower()
        except Exception:
            state = ''
        if state in {'withdrawn', 'iconic'}:
            window.deiconify()
        window.lift()
        window.focus_force()
        return True
    except Exception:
        return False


def _ui_bold_font(font_px: int):
    size = max(8, int(font_px))
    candidates = []
    windir = os.environ.get('WINDIR', '').strip()
    if windir:
        candidates.extend([
            str(Path(windir) / 'Fonts' / 'segoeuib.ttf'),
            str(Path(windir) / 'Fonts' / 'arialbd.ttf'),
        ])
    candidates.extend(['segoeuib.ttf', 'arialbd.ttf', 'DejaVuSans-Bold.ttf'])
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def render_faded_text_image(
    text: str,
    *,
    width: int,
    height: int,
    color: str,
    font_px: int,
    fade_width: int = 44,
) -> Image.Image:
    """Render one unwrapped title line and alpha-fade only when it overflows."""
    width = max(1, int(width))
    height = max(1, int(height))
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    value = str(text or '')
    if not value:
        return image

    font = _ui_bold_font(font_px)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), value, font=font)
    text_width = max(0, bbox[2] - bbox[0])
    text_height = max(0, bbox[3] - bbox[1])
    y = max(0, (height - text_height) // 2 - bbox[1])
    draw.text((0, y), value, font=font, fill=color)

    fade_width = max(8, min(width, int(fade_width)))
    if text_width > width - max(4, fade_width // 4):
        start = max(0, width - fade_width)
        fade_mask = Image.new('L', (width, height), 255)
        mask_draw = ImageDraw.Draw(fade_mask)
        span = max(1, width - 1 - start)
        for x in range(start, width):
            alpha = int(round(255 * (width - 1 - x) / span))
            mask_draw.line((x, 0, x, height), fill=max(0, min(255, alpha)))
        image.putalpha(ImageChops.multiply(image.getchannel('A'), fade_mask))
    return image


def format_zone_value(ocr_text: str | None, place: str | None) -> str:
    raw = (ocr_text or '').strip()
    if raw:
        return raw
    fallback = (place or '').strip()
    if fallback and fallback not in {'—', 'En attente'}:
        return fallback
    return 'En attente…'


def format_position_value(value: int | None) -> str:
    return '—' if value is None else str(int(value))


def _round_points(x1: int, y1: int, x2: int, y2: int, radius: int) -> tuple[int, ...]:
    r = max(1, min(int(radius), (x2 - x1) // 2, (y2 - y1) // 2))
    return (
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    )


def _rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, *, radius: int,
                  fill: str, outline: str = '', width: int = 1, tags=()) -> int:
    return canvas.create_polygon(
        _round_points(x1, y1, x2, y2, radius),
        smooth=True,
        splinesteps=32,
        fill=fill,
        outline=outline,
        width=width,
        tags=tags,
    )


def detection_badge(state) -> tuple[str, str]:
    """Return the single user-facing detection state shown in the header chip."""
    statuses = ' | '.join((
        str(getattr(state, 'worker_status', '') or ''),
        str(getattr(state, 'zone_worker_status', '') or ''),
        str(getattr(state, 'position_worker_status', '') or ''),
    )).casefold()
    if any(token in statuses for token in ('erreur', 'crash', 'fatal')):
        return 'Échec détection', 'red'
    if not bool(getattr(state, 'window_found', False)):
        return 'En attente de Dofus', 'waiting'
    if 'ocr prêt' in str(getattr(state, 'worker_status', '') or '').casefold():
        return 'Dofus détecté', 'ok'
    return 'Initialisation…', 'waiting'


def combat_badge(state) -> tuple[str, str]:
    """Return the compact combat state shown beside the detected zone."""
    if not bool(getattr(state, 'window_found', False)):
        return 'Combat : attente', 'waiting'
    if not bool(getattr(state, 'automatic_detection_unlocked', True)):
        return 'Combat : attente position', 'waiting'
    if bool(getattr(state, 'in_combat', False)):
        return 'Combat détecté', 'combat'
    return 'Hors combat', 'idle'


class _CanvasBinding:
    """Small Label-compatible adapter around a canvas text item."""

    def __init__(self, canvas: tk.Canvas, item: int) -> None:
        self.canvas = canvas
        self.item = item

    def config(self, **kwargs) -> None:
        options = dict(kwargs)
        if 'fg' in options:
            options['fill'] = options.pop('fg')
        if 'bg' in options:
            options.pop('bg')
        self.canvas.itemconfigure(self.item, **options)

    configure = config


class _FadingTextBinding:
    """Label-like single-line title renderer with a real alpha fade at the right edge."""

    def __init__(
        self,
        parent_canvas: tk.Canvas,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        text: str,
        fg: str,
        bg: str,
        font_pt: int = 18,
        fade_width: int = 46,
    ) -> None:
        self.parent_canvas = parent_canvas
        self.width = int(width)
        self.height = int(height)
        self.text = text
        self.fg = fg
        self.bg = bg
        self.font_pt = int(font_pt)
        self.fade_width = int(fade_width)
        self.canvas = tk.Canvas(
            parent_canvas,
            width=self.width,
            height=self.height,
            bg=self.bg,
            highlightthickness=0,
            bd=0,
            relief='flat',
        )
        self.window_item = parent_canvas.create_window(x, y, anchor='nw', window=self.canvas)
        self.image_item = self.canvas.create_image(0, self.height // 2, anchor='w')
        self._photo = None
        self._render()

    def _font_px(self) -> int:
        try:
            return max(8, int(round(self.font_pt * float(self.canvas.winfo_fpixels('1p')))))
        except Exception:
            return max(8, int(round(self.font_pt * 4 / 3)))

    def _render(self) -> None:
        image = render_faded_text_image(
            self.text,
            width=self.width,
            height=self.height,
            color=self.fg,
            font_px=self._font_px(),
            fade_width=self.fade_width,
        )
        self.canvas.configure(bg=self.bg)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self.image_item, image=self._photo)

    def config(self, **kwargs) -> None:
        changed = False
        if 'text' in kwargs:
            value = str(kwargs['text'] or '')
            if value != self.text:
                self.text = value
                changed = True
        if 'fg' in kwargs:
            value = str(kwargs['fg'])
            if value != self.fg:
                self.fg = value
                changed = True
        if 'bg' in kwargs:
            value = str(kwargs['bg'])
            if value != self.bg:
                self.bg = value
                changed = True
        if changed:
            self._render()

    configure = config


class MainWindow:
    """Public Dofusic dashboard drawn as a custom canvas instead of classic Tk widgets."""

    WIDTH = 560
    HEIGHT = 520

    def __init__(self, controller: DofusicController, config: AppConfig, *, config_file: Path | None = None) -> None:
        self.controller = controller
        self.config = config
        self.config_file = config_file
        self.palette = get_theme(config.theme)
        self.music_session: MusicSession | None = None
        self.music_window: MusicWindow | None = None
        self.settings_window: SettingsWindow | None = None
        self.root = tk.Tk()
        self.root.title(f'Dofusic {__version__}')
        self.root.geometry(f'{self.WIDTH}x{self.HEIGHT}')
        self.root.configure(bg=self.palette.bg)
        self.root.resizable(False, False)
        try:
            self.root.attributes('-topmost', bool(config.always_on_top))
        except tk.TclError:
            pass
        self.root.protocol('WM_DELETE_WINDOW', self.close)

        self._last_rendered: dict[str, tuple[tuple[str, object], ...]] = {}
        self._closed = False
        self._last_state = None
        self._visualizer_display_levels = [0.0] * VISUALIZER_BANDS
        self.mute_var = tk.IntVar(value=1 if config.mute else 0)
        self.volume_var = tk.IntVar(value=config.volume)
        self._icon_tk = ImageTk.PhotoImage(create_dofusic_logo(64))
        try:
            self.root.iconphoto(True, self._icon_tk)
        except tk.TclError:
            pass
        apply_window_chrome(self.root, self.palette)

        self.overlay = TrackingOverlay(self.root)
        self._build()
        self._apply_theme()

    def _build(self) -> None:
        self.canvas = tk.Canvas(
            self.root,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill='both', expand=True)
        c = self.canvas

        # Subtle top glow/green tint, deliberately restrained.
        c.create_rectangle(0, 0, self.WIDTH, 72, fill=BG_TOP, outline='')
        c.create_oval(415, -85, 610, 95, fill='#092019', outline='')

        # Header: two compact entry points keep advanced features out of the dashboard.
        _rounded_rect(
            c, 18, 22, 56, 60, radius=11, fill='#0a1b17', outline=BORDER, width=1,
            tags=('music-button-bg', 'music-hit'),
        )
        c.create_text(
            37, 41, text='♫', fill=GREEN, font=('Segoe UI Symbol', 16, 'bold'),
            tags=('music-icon', 'music-hit'),
        )
        c.tag_bind('music-hit', '<Button-1>', lambda _event: self._open_music())
        _rounded_rect(c, 64, 22, 102, 60, radius=11, fill='#0a1b17', outline=BORDER, width=1, tags=('settings-hit',))
        c.create_text(83, 41, text='⚙', fill=TEXT, font=('Segoe UI Symbol', 15), tags=('settings-hit',))
        c.tag_bind('settings-hit', '<Button-1>', lambda _event: self._open_settings())

        # Dofus detection chip.
        _rounded_rect(c, 374, 22, 538, 60, radius=12, fill='#0a1b17', outline=BORDER_GREEN, width=1)
        self.window_indicator_dot = _CanvasBinding(
            c, c.create_oval(391, 36, 401, 46, fill=AMBER, outline=''),
        )
        self.window_indicator = _CanvasBinding(
            c,
            c.create_text(414, 41, text='En attente de Dofus', anchor='w', fill=MUTED,
                          font=('Segoe UI', 10, 'bold')),
        )

        # Zone card -------------------------------------------------------------
        zx1, zy1, zx2, zy2 = 18, 82, 542, 190
        _rounded_rect(c, zx1, zy1, zx2, zy2, radius=16, fill='#0b1d18', outline=BORDER_GREEN, width=1)
        # pin icon
        c.create_oval(42, 109, 62, 129, fill=GREEN, outline='')
        c.create_polygon(45, 124, 59, 124, 52, 142, fill=GREEN, outline='')
        c.create_oval(49, 116, 55, 122, fill='#0a1d18', outline='')
        c.create_text(82, 108, text='ZONE DÉTECTÉE', anchor='nw', fill=MUTED,
                      font=('Segoe UI', 9, 'bold'))
        self.combat_indicator = _CanvasBinding(
            c,
            c.create_text(520, 108, text='Combat : attente', anchor='ne', fill=MUTED,
                          font=('Segoe UI', 8, 'bold')),
        )
        self.zone_value_label = _CanvasBinding(
            c,
            c.create_text(82, 141, text='En attente…', anchor='w', fill=TEXT,
                          font=('Segoe UI', 17, 'bold'), width=430),
        )

        # Position card ---------------------------------------------------------
        px1, py1, px2, py2 = 18, 202, 542, 326
        _rounded_rect(c, px1, py1, px2, py2, radius=16, fill=CARD_ALT, outline=BORDER, width=1)
        # crosshair icon
        c.create_oval(42, 226, 62, 246, outline=GREEN, width=2)
        c.create_oval(49, 233, 55, 239, fill=GREEN, outline='')
        c.create_line(52, 220, 52, 252, fill=GREEN, width=2)
        c.create_line(36, 236, 68, 236, fill=GREEN, width=2)
        c.create_text(82, 225, text='POSITION', anchor='nw', fill=MUTED,
                      font=('Segoe UI', 9, 'bold'))
        _rounded_rect(c, 82, 262, 300, 307, radius=10, fill=CARD_FIELD, outline=BORDER, width=1)
        _rounded_rect(c, 314, 262, 532, 307, radius=10, fill=CARD_FIELD, outline=BORDER, width=1)
        c.create_text(101, 284, text='X', anchor='w', fill=MUTED, font=('Segoe UI', 10))
        c.create_text(333, 284, text='Y', anchor='w', fill=MUTED, font=('Segoe UI', 10))
        self.position_x_value = _CanvasBinding(
            c, c.create_text(135, 284, text='—', anchor='w', fill=TEXT, font=('Segoe UI', 17, 'bold')),
        )
        self.position_y_value = _CanvasBinding(
            c, c.create_text(367, 284, text='—', anchor='w', fill=TEXT, font=('Segoe UI', 17, 'bold')),
        )

        # Music card ------------------------------------------------------------
        mx1, my1, mx2, my2 = 18, 338, 542, 448
        _rounded_rect(c, mx1, my1, mx2, my2, radius=16, fill='#0a1b17', outline=BORDER_GREEN, width=1)
        c.create_text(42, 373, text='♪', anchor='w', fill=GREEN, font=('Segoe UI Symbol', 30, 'bold'))
        c.create_text(82, 361, text='MUSIQUE EN COURS', anchor='nw', fill=MUTED,
                      font=('Segoe UI', 9, 'bold'))
        self.music_value_label = _FadingTextBinding(
            c, x=82, y=381, width=305, height=42, text='Aucune musique',
            fg=GREEN_SOFT, bg='#0a1b17', font_pt=18, fade_width=46,
        )
        # Contextual escape hatch: visible only while an online track owns audio.
        _rounded_rect(
            c, 402, 347, 530, 376, radius=9, fill=CARD_FIELD, outline=BORDER, width=1,
            tags=('local-return-hit',),
        )
        c.create_text(415, 361, text='↩', anchor='w', fill=GREEN,
                      font=('Segoe UI Symbol', 11, 'bold'), tags=('local-return-hit',))
        c.create_text(434, 361, text='Musique Dofus', anchor='w', fill=TEXT,
                      font=('Segoe UI', 8, 'bold'), tags=('local-return-hit',))
        c.tag_bind('local-return-hit', '<Button-1>', self._return_to_local_music)
        c.itemconfigure('local-return-hit', state='hidden')
        # 24-band real FFT spectrum: bass on the left, treble on the right.
        # The animation has its own 30 FPS loop and never waits for OCR.
        self._visualizer_baseline = 421
        self._visualizer_max_height = 44
        self._visualizer_bars = []
        x0 = 408
        for index in range(24):
            x = x0 + index * 5
            bar = c.create_rectangle(x, self._visualizer_baseline - 2, x + 3, self._visualizer_baseline,
                                     fill='#13513c', outline='',
                                     state='normal' if self.config.show_visualizer else 'hidden')
            self._visualizer_bars.append(bar)

        # Volume ----------------------------------------------------------------
        c.create_text(20, 470, text='VOLUME', anchor='w', fill=MUTED, font=('Segoe UI', 9, 'bold'))
        self.volume_value_label = _CanvasBinding(
            c, c.create_text(520, 470, text=f'{self.config.volume}%', anchor='e', fill=TEXT,
                             font=('Segoe UI', 9, 'bold')),
        )
        self._speaker_icon = c.create_text(22, 500, text='🔊', anchor='w', fill=TEXT, font=('Segoe UI Emoji', 10))
        self._speaker_mute_slash = c.create_line(18, 493, 35, 507, fill=RED, width=2, state='hidden')
        self._volume_x1 = 55
        self._volume_x2 = 415
        self._volume_y = 500
        _rounded_rect(c, self._volume_x1, 496, self._volume_x2, 504, radius=4, fill=TRACK, outline='')
        self._volume_fill = _rounded_rect(c, self._volume_x1, 496, self._volume_x1 + 1, 504,
                                          radius=4, fill=TRACK_ACTIVE, outline='')
        self._volume_knob = c.create_oval(0, 0, 0, 0, fill=TEXT, outline='')
        c.create_rectangle(438, 491, 452, 505, fill=BG, outline=BORDER, width=1, tags=('mute-hit',))
        self._mute_tick = c.create_text(445, 498, text='', fill=GREEN, font=('Segoe UI', 11, 'bold'), tags=('mute-hit',))
        c.create_text(460, 498, text='Muet', anchor='w', fill=MUTED, font=('Segoe UI', 10), tags=('mute-hit',))
        c.tag_bind('mute-hit', '<Button-1>', self._toggle_mute)
        c.configure(cursor='arrow')
        c.bind('<Button-1>', self._on_canvas_click, add='+')
        c.bind('<B1-Motion>', self._on_canvas_drag, add='+')
        c.bind('<ButtonRelease-1>', self._on_canvas_release, add='+')
        self._volume_gesture = VolumeGesture(self._volume_x1, self._volume_x2, self._volume_y, hit_half_height=10)
        self._redraw_volume(int(self.config.volume))
        self._redraw_mute()


    @staticmethod
    def _theme_mapping(palette) -> dict[str, str]:
        return theme_color_mapping(palette)

    def _apply_theme(self) -> None:
        self.palette = get_theme(self.config.theme)
        mapping = self._theme_mapping(self.palette)
        self.root.configure(bg=self.palette.bg)
        self.canvas.configure(bg=self.palette.bg)
        schedule_window_chrome(self.root, self.palette)
        for item in self.canvas.find_all():
            for option in ('fill', 'outline'):
                try:
                    value = str(self.canvas.itemcget(item, option) or '')
                except tk.TclError:
                    continue
                replacement = mapping.get(value.casefold())
                if replacement:
                    try:
                        self.canvas.itemconfigure(item, **{option: replacement})
                    except tk.TclError:
                        pass
        try:
            self.music_value_label.config(fg=self.palette.accent_soft, bg=self.palette.music_card)
        except Exception:
            pass
        self._update_music_entry_state()
        self._last_rendered.clear()
        if self._last_state is not None:
            self._render_state(self._last_state)

    def _apply_preferences(self) -> None:
        self.palette = get_theme(self.config.theme)
        try:
            self.root.attributes('-topmost', bool(self.config.always_on_top))
        except tk.TclError:
            pass
        self._apply_theme()

        if not self.config.online_music_enabled:
            if self.music_window is not None:
                try:
                    self.music_window.close()
                except Exception:
                    pass
                self.music_window = None
            if self.music_session is not None:
                try:
                    self.music_session.stop_online()
                except Exception:
                    pass
                try:
                    self.music_session.close()
                except Exception:
                    pass
                self.music_session = None

        for secondary in (self.music_window, self.settings_window):
            if secondary is None:
                continue
            try:
                if secondary.window.winfo_exists():
                    secondary.apply_preferences()
            except Exception:
                pass
        if self.music_session is not None:
            try:
                self.music_session.apply_config()
            except Exception:
                pass
        try:
            self.controller.player.fade_ms = max(0, min(5000, int(self.config.fade_ms)))
            self.controller.player.set_spectrum_enabled(bool(self.config.show_visualizer))
            self.controller.player.set_normalize_loudness(bool(self.config.normalize_loudness))
        except Exception:
            pass
        for bar in getattr(self, '_visualizer_bars', ()):
            try:
                self.canvas.itemconfigure(bar, state='normal' if self.config.show_visualizer else 'hidden')
            except tk.TclError:
                pass
        # Provider/cache settings are picked up on the next Music panel session.
        if self.music_session is not None and not self.controller.online_audio_active:
            music_open = bool(self.music_window and self.music_window.window.winfo_exists())
            if not music_open:
                self.music_session.close()
                self.music_session = None

    def _update_music_entry_state(self) -> None:
        enabled = bool(self.config.online_music_enabled)
        try:
            self.canvas.itemconfigure(
                'music-icon',
                fill=self.palette.accent if enabled else self.palette.muted,
            )
            self.canvas.itemconfigure(
                'music-button-bg',
                fill=self.palette.header_chip if enabled else self.palette.card_alt,
                outline=self.palette.border,
            )
        except Exception:
            pass

    def _open_settings(self) -> None:
        if restore_secondary_window(self.settings_window):
            return
        self.settings_window = None
        self.settings_window = SettingsWindow(
            self.root, self.config, config_file=self.config_file, on_apply=self._apply_preferences,
        )

    def _get_music_session(self) -> MusicSession:
        if self.music_session is None:
            self.music_session = MusicSession(self.controller, self.config)
        return self.music_session

    def _open_music(self) -> None:
        if not self.config.online_music_enabled:
            return
        if restore_secondary_window(self.music_window):
            return
        self.music_window = None
        session = self._get_music_session()
        self.music_window = MusicWindow(
            self.root, session, self.config, self.controller.repository,
            on_library_saved=lambda _path: None,
        )

    def _return_to_local_music(self, _event=None) -> None:
        if self.music_session is not None:
            self.music_session.stop_online()
        else:
            try:
                self.controller.resume_local_audio()
            except Exception:
                pass
        if self.music_window is not None:
            try:
                if self.music_window.window.winfo_exists():
                    self.music_window._refresh_playback_panel()
            except Exception:
                pass
        self._update_local_return_button()

    def _update_local_return_button(self) -> None:
        try:
            active = bool(getattr(self.controller, 'audio_override_active', self.controller.online_audio_active))
            state = 'normal' if active else 'hidden'
            self.canvas.itemconfigure('local-return-hit', state=state)
        except Exception:
            pass

    def _redraw_visualizer(self, levels) -> None:
        bars = getattr(self, '_visualizer_bars', ())
        if not bars:
            return
        values = [max(0.0, min(1.0, float(v))) for v in (levels or ())]
        previous = list(getattr(self, '_visualizer_display_levels', [0.0] * len(bars)))
        if len(previous) != len(bars):
            previous = [0.0] * len(bars)
        smoothed: list[float] = []
        for index, bar in enumerate(bars):
            target = values[index] if index < len(values) else 0.0
            old = previous[index]
            # Fast attack, slower release: responsive without jittering violently.
            level = old + (target - old) * (0.72 if target >= old else 0.22)
            if target <= 0.005 and level < 0.015:
                level = 0.0
            smoothed.append(level)
            height = 2 + int(round(level * (self._visualizer_max_height - 2)))
            x1, _y1, x2, _y2 = self.canvas.coords(bar)
            self.canvas.coords(bar, x1, self._visualizer_baseline - height, x2, self._visualizer_baseline)
            palette = getattr(self, 'palette', get_theme('emerald'))
            self.canvas.itemconfigure(bar, fill=palette.track_active if level >= 0.10 else palette.visualizer_idle)
        self._visualizer_display_levels = smoothed

    def _visualizer_tick(self) -> None:
        if getattr(self, '_closed', False):
            return
        try:
            session = self.music_session
            current = session.current if session is not None else None
            if current is not None and current.source == 'online':
                levels = session.online_visualizer_levels(VISUALIZER_BANDS)
            else:
                levels = self.controller.player.spectrum_levels(VISUALIZER_BANDS)
            self._redraw_visualizer(levels)
        except Exception:
            self._redraw_visualizer((0.0,) * VISUALIZER_BANDS)
        finally:
            if not getattr(self, '_closed', False):
                self.root.after(VISUALIZER_INTERVAL_MS, self._visualizer_tick)

    def _redraw_volume(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        x = self._volume_x1 + (self._volume_x2 - self._volume_x1) * volume / 100.0
        self.canvas.coords(self._volume_fill, *_round_points(self._volume_x1, 496, max(self._volume_x1 + 1, int(x)), 504, 4))
        self.canvas.coords(self._volume_knob, x - 7, self._volume_y - 7, x + 7, self._volume_y + 7)
        self.volume_var.set(volume)

    def _redraw_mute(self) -> None:
        muted = bool(self.mute_var.get())
        self.canvas.itemconfigure(self._mute_tick, text='✓' if muted else '')
        visual_muted = mute_visual_active(muted=muted, volume=self.volume_var.get())
        self.canvas.itemconfigure(self._speaker_mute_slash, state='normal' if visual_muted else 'hidden')

    def _on_canvas_click(self, event) -> None:
        if self._volume_gesture.press(event.x, event.y):
            self._on_volume(str(self._volume_gesture.value_from_x(event.x)))

    def _on_canvas_drag(self, event) -> None:
        value = self._volume_gesture.drag(event.x, event.y)
        if value is not None:
            self._on_volume(str(value))

    def _on_canvas_release(self, _event=None) -> None:
        self._volume_gesture.release()

    def _toggle_mute(self, _event=None) -> None:
        self.mute_var.set(0 if self.mute_var.get() else 1)
        self._on_mute()
        self._redraw_mute()

    def _on_volume(self, value: str) -> None:
        try:
            volume = self.controller.set_volume(float(value))
        except Exception:
            return
        self.config.volume = volume
        if self.music_session is not None:
            try:
                self.music_session.set_volume(volume)
            except Exception:
                pass
        self._redraw_volume(volume)
        self._redraw_mute()
        self.volume_value_label.config(text=f'{volume}%')

    def _on_mute(self) -> None:
        muted = bool(self.mute_var.get())
        self.config.mute = muted
        self.controller.set_muted(muted)
        if self.music_session is not None:
            try:
                self.music_session.set_muted(muted)
            except Exception:
                pass


    def _set_widget(self, key: str, widget, **options) -> None:
        rendered = tuple(sorted(options.items()))
        if self._last_rendered.get(key) == rendered:
            return
        self._last_rendered[key] = rendered
        widget.config(**options)

    def _render_state(self, state) -> None:
        zone = format_zone_value(state.ocr_text, state.place or state.location)
        self._set_widget('zone', self.zone_value_label, text=zone)
        self._set_widget('x', self.position_x_value, text=format_position_value(state.position_x))
        self._set_widget('y', self.position_y_value, text=format_position_value(state.position_y))
        self._set_widget('music', self.music_value_label, text=state.music or 'Aucune musique')
        palette = getattr(self, 'palette', get_theme('emerald'))
        combat_label, combat_role = combat_badge(state)
        combat_color = (
            palette.red if combat_role == 'combat'
            else palette.amber if combat_role == 'waiting'
            else palette.muted
        )
        self._set_widget('combat', self.combat_indicator, text=combat_label, fg=combat_color)
        label, role = detection_badge(state)
        color = palette.accent if role == 'ok' else palette.red if role == 'red' else palette.amber
        self._set_widget('window', self.window_indicator, text=label, fg=(palette.text if role != 'waiting' else palette.muted))
        self._set_widget('window-dot', self.window_indicator_dot, fg=color)

    def _render_overlay(self, state) -> None:
        zone_key = state.location_key or None
        self.overlay.update(
            state.overlay_hwnd,
            state.combat_overlay_rect,
            state.zone_overlay_rect,
            state.position_overlay_rect,
            zone_key,
        )

    def _update(self) -> None:
        try:
            state = self.controller.tick()
            if self.music_session is not None:
                self.music_session.tick()
                state = self.controller.state
            self._last_state = state
            self._render_state(state)
            self._update_local_return_button()
            self._render_overlay(state)
        except Exception:
            self.overlay.hide()
            palette = getattr(self, 'palette', get_theme('emerald'))
            self._set_widget('window', self.window_indicator, text='Erreur Dofusic', fg=palette.text)
            self._set_widget('window-dot', self.window_indicator_dot, fg=palette.red)
        finally:
            self.root.after(self.config.ui_tick_ms, self._update)

    def run(self) -> None:
        self.controller.start()
        self._update()
        self._visualizer_tick()
        self.root.mainloop()

    def close(self) -> None:
        self._closed = True
        self.config.volume = int(getattr(self.controller.player, 'volume', self.config.volume))
        self.config.mute = bool(getattr(self.controller.player, 'muted', self.config.mute))
        try:
            save_config(self.config, self.config_file)
        except Exception:
            pass
        if self.music_session is not None:
            try:
                self.music_session.close()
            except Exception:
                pass
        self.overlay.close()
        self.controller.close()
        try:
            self.root.destroy()
        except Exception:
            pass
