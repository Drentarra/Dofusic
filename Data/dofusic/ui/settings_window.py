from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from dofusic.config import AppConfig, default_music_dir, save_config, user_data_dir
from dofusic.ui.themes import get_theme, recolor_widget_tree, theme_choices
from dofusic.ui.window_chrome import apply_window_chrome, schedule_window_chrome


def _open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == 'nt':
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
    except Exception:
        pass


class SettingsWindow:
    DEFAULT_SIZE = (520, 520)

    def __init__(self, parent, config: AppConfig, *, config_file=None, on_apply=None) -> None:
        self.parent = parent
        self.config = config
        self.config_file = config_file
        self.on_apply = on_apply or (lambda: None)
        self.window = tk.Toplevel(parent)
        self.window.title('Dofusic — Paramètres')
        self.window.geometry(f'{self.DEFAULT_SIZE[0]}x{self.DEFAULT_SIZE[1]}')
        self.window.resizable(False, False)
        self.window.transient(parent)
        try:
            self.window.attributes('-topmost', bool(self.config.always_on_top))
        except tk.TclError:
            pass
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.p = get_theme(self.config.theme)
        self._build()
        apply_window_chrome(self.window, self.p)

    def _build(self) -> None:
        p = self.p
        self.window.configure(bg=p.bg)
        self._style = ttk.Style(self.window)
        try:
            self._style.theme_use('clam')
        except tk.TclError:
            pass
        self._configure_ttk_style(p)

        title = tk.Label(self.window, text='Paramètres', bg=p.bg, fg=p.text, font=('Segoe UI', 17, 'bold'))
        title.pack(anchor='w', padx=20, pady=(16, 10))

        self.notebook = ttk.Notebook(self.window, style='Dofusic.TNotebook')
        self.notebook.pack(fill='both', expand=True, padx=18, pady=(0, 12))
        appearance = tk.Frame(self.notebook, bg=p.card)
        online = tk.Frame(self.notebook, bg=p.card)
        advanced = tk.Frame(self.notebook, bg=p.card)
        self.notebook.add(appearance, text='Apparence')
        self.notebook.add(online, text='Musique online')
        self.notebook.add(advanced, text='Avancé')

        self.theme_var = tk.StringVar(value=self.config.theme)
        self.visualizer_var = tk.BooleanVar(value=self.config.show_visualizer)
        self.normalize_var = tk.BooleanVar(value=self.config.normalize_loudness)
        self.fade_var = tk.IntVar(value=self.config.fade_ms)
        self.topmost_var = tk.BooleanVar(value=self.config.always_on_top)
        self.online_var = tk.BooleanVar(value=self.config.online_music_enabled)
        self.suggestions_var = tk.BooleanVar(value=self.config.online_suggestions)
        self.results_var = tk.IntVar(value=self.config.online_search_results)
        self.delay_var = tk.IntVar(value=self.config.online_suggest_delay_ms)
        self.cache_var = tk.IntVar(value=self.config.online_cache_mb)
        self.debug_var = tk.BooleanVar(value=self.config.debug)

        self._section_label(appearance, 'THÈME', p).pack(anchor='w', padx=18, pady=(18, 8))
        labels = {key: label for key, label in theme_choices()}
        combo = ttk.Combobox(appearance, state='readonly', values=[label for _key, label in theme_choices()], width=30, style='Dofusic.TCombobox')
        self.theme_combo = combo
        combo.set(labels.get(self.config.theme, labels['emerald']))
        combo.pack(anchor='w', padx=18)
        def choose_theme(_event=None):
            label = combo.get()
            reverse = {value: key for key, value in labels.items()}
            self.theme_var.set(reverse.get(label, 'emerald'))
            self._apply(live_theme=True)
        combo.bind('<<ComboboxSelected>>', choose_theme)
        self._section_label(appearance, 'AUDIO', p).pack(anchor='w', padx=18, pady=(18, 6))
        self._check(appearance, 'Afficher le visualiseur audio', self.visualizer_var, p).pack(anchor='w', padx=18, pady=2)
        self._check(appearance, 'Normaliser le volume entre les morceaux', self.normalize_var, p).pack(anchor='w', padx=18, pady=2)
        self._field(appearance, 'Fondu audio en ms (0–5000)', self.fade_var, p, minimum=0, maximum=5000, increment=50).pack(fill='x', padx=18, pady=(8, 4))
        topmost_check = self._check(appearance, 'Toujours au premier plan', self.topmost_var, p)
        topmost_check.configure(command=lambda: self._apply(live_theme=True))
        topmost_check.pack(anchor='w', padx=18, pady=2)

        self._section_label(online, 'ONLINE', p).pack(anchor='w', padx=18, pady=(18, 8))
        online_check = self._check(online, 'Activer la musique online', self.online_var, p)
        online_check.configure(command=lambda: self._apply(live_theme=True))
        online_check.pack(anchor='w', padx=18, pady=2)
        self._check(online, 'Suggestions pendant la frappe', self.suggestions_var, p).pack(anchor='w', padx=18, pady=2)
        self._field(online, 'Résultats affichés (3–20)', self.results_var, p, minimum=3, maximum=20, increment=1).pack(fill='x', padx=18, pady=(12, 4))
        self._field(online, 'Délai suggestions en ms (150–1200)', self.delay_var, p, minimum=150, maximum=1200, increment=50).pack(fill='x', padx=18, pady=4)
        self._field(online, 'Cache online en Mo (64–4096)', self.cache_var, p, minimum=64, maximum=4096, increment=64).pack(fill='x', padx=18, pady=4)

        self._section_label(advanced, 'DIAGNOSTIC', p).pack(anchor='w', padx=18, pady=(8, 8))
        tk.Label(
            advanced, text='Le moteur online utilise yt-dlp sans navigateur intégré ni compte YouTube.',
            bg=p.card, fg=p.muted, font=('Segoe UI', 9), wraplength=450, justify='left',
        ).pack(anchor='w', padx=18, pady=(0, 10))
        self._check(advanced, 'Mode debug au prochain démarrage', self.debug_var, p).pack(anchor='w', padx=18, pady=(2, 14))
        buttons = tk.Frame(advanced, bg=p.card)
        buttons.pack(fill='x', padx=18, pady=4)
        self._button(buttons, 'Ouvrir Musiques', lambda: _open_path(Path(self.config.music_dir) if self.config.music_dir else default_music_dir()), p).pack(side='left', padx=(0, 8))
        self._button(buttons, 'Ouvrir données Dofusic', lambda: _open_path(user_data_dir()), p).pack(side='left')

        footer = tk.Frame(self.window, bg=p.bg)
        footer.pack(fill='x', padx=18, pady=(0, 16))
        self._button(footer, 'Appliquer', self._apply, p, accent=True).pack(side='right')
        self._button(footer, 'Fermer', self.close, p).pack(side='right', padx=(0, 8))

    def _configure_ttk_style(self, p) -> None:
        style = self._style
        style.configure('Dofusic.TNotebook', background=p.bg, borderwidth=0)
        style.configure('Dofusic.TNotebook.Tab', background=p.card_alt, foreground=p.muted, padding=(14, 8))
        style.map('Dofusic.TNotebook.Tab', background=[('selected', p.accent_dark)], foreground=[('selected', p.text)])
        style.configure(
            'Dofusic.TCombobox',
            fieldbackground=p.card_field, background=p.card_alt, foreground=p.text,
            arrowcolor=p.text, bordercolor=p.border, lightcolor=p.border, darkcolor=p.border,
        )
        style.map(
            'Dofusic.TCombobox',
            fieldbackground=[('readonly', p.card_field)],
            foreground=[('readonly', p.text)],
            selectbackground=[('readonly', p.accent_dark)],
            selectforeground=[('readonly', p.text)],
        )

    def apply_preferences(self) -> None:
        self.p = get_theme(self.config.theme)
        recolor_widget_tree(self.window, self.p)
        try:
            self.window.configure(bg=self.p.bg)
            self.window.attributes('-topmost', bool(self.config.always_on_top))
            self.window.resizable(False, False)
            schedule_window_chrome(self.window, self.p)
        except tk.TclError:
            pass
        self._configure_ttk_style(self.p)
    @staticmethod
    def _section_label(parent, text, p):
        return tk.Label(parent, text=text, bg=p.card, fg=p.accent, font=('Segoe UI', 9, 'bold'))

    @staticmethod
    def _check(parent, text, variable, p):
        return tk.Checkbutton(parent, text=text, variable=variable, bg=p.card, fg=p.text, activebackground=p.card,
                              activeforeground=p.text, selectcolor=p.card_field, font=('Segoe UI', 10), relief='flat')

    @staticmethod
    def _field(parent, label, variable, p, *, minimum, maximum, increment=1):
        frame = tk.Frame(parent, bg=p.card)
        tk.Label(frame, text=label, bg=p.card, fg=p.muted, font=('Segoe UI', 9)).pack(side='left')
        tk.Spinbox(
            frame, textvariable=variable, from_=minimum, to=maximum, increment=increment, width=8,
            bg=p.card_field, fg=p.text, insertbackground=p.text, buttonbackground=p.card_alt,
            relief='flat', font=('Segoe UI', 9),
        ).pack(side='right')
        return frame

    @staticmethod
    def _button(parent, text, command, p, *, accent=False):
        return tk.Button(parent, text=text, command=command, bg=p.accent_dark if accent else p.card_alt,
                         fg=p.text, activebackground=p.accent, activeforeground=p.bg, relief='flat',
                         bd=0, padx=14, pady=7, font=('Segoe UI', 9, 'bold'), cursor='hand2')

    def _apply(self, live_theme: bool = False) -> None:
        try:
            selected_tab = self.notebook.select()
        except Exception:
            selected_tab = None

        self.config.theme = self.theme_var.get()
        self.config.show_visualizer = bool(self.visualizer_var.get())
        self.config.normalize_loudness = bool(self.normalize_var.get())
        self.config.fade_ms = int(self.fade_var.get())
        self.config.always_on_top = bool(self.topmost_var.get())
        self.config.online_music_enabled = bool(self.online_var.get())
        self.config.online_suggestions = bool(self.suggestions_var.get())
        self.config.online_search_results = int(self.results_var.get())
        self.config.online_suggest_delay_ms = int(self.delay_var.get())
        self.config.online_cache_mb = int(self.cache_var.get())
        self.config.debug = bool(self.debug_var.get())
        clean = self.config.sanitized()
        for field in clean.__dataclass_fields__:
            setattr(self.config, field, getattr(clean, field))
        try:
            save_config(self.config, self.config_file)
        except Exception:
            pass
        self.on_apply()
        if selected_tab:
            try:
                self.notebook.select(selected_tab)
            except Exception:
                pass
        if live_theme:
            def keep_settings_visible():
                try:
                    if not self.window.winfo_exists():
                        return
                    if str(self.window.state() or '').lower() in {'withdrawn', 'iconic'}:
                        self.window.deiconify()
                    self.window.lift()
                    self.window.focus_force()
                except Exception:
                    pass

            try:
                self.window.after_idle(keep_settings_visible)
            except Exception:
                pass
            return

    def close(self) -> None:
        try:
            self._apply()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass
