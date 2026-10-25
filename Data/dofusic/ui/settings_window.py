from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from dataclasses import fields, replace
from pathlib import Path
from tkinter import messagebox, ttk

from dofusic.config import AppConfig, default_music_dir, save_config, user_data_dir
from dofusic.ui.themes import get_theme, recolor_widget_tree, theme_choices
from dofusic.ui.window_chrome import apply_window_chrome, schedule_window_chrome


def _open_path(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            messagebox.showerror('Dossier inaccessible', f'Impossible d’ouvrir ce dossier :\n{path}\n{exc}')
        except tk.TclError:
            pass


class SettingsWindow:
    DEFAULT_SIZE = (520, 440)

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
        advanced = tk.Frame(self.notebook, bg=p.card)
        self.notebook.add(appearance, text='Apparence')
        self.notebook.add(advanced, text='Avancé')

        self.theme_var = tk.StringVar(value=self.config.theme)
        self.visualizer_var = tk.BooleanVar(value=self.config.show_visualizer)
        self.normalize_var = tk.BooleanVar(value=self.config.normalize_loudness)
        self.fade_var = tk.IntVar(value=self.config.fade_ms)
        self.topmost_var = tk.BooleanVar(value=self.config.always_on_top)

        self._section_label(appearance, 'THÈME', p).pack(anchor='w', padx=18, pady=(18, 8))
        labels = {key: label for key, label in theme_choices()}
        combo = ttk.Combobox(appearance, state='readonly', values=[label for _key, label in theme_choices()], width=30, style='Dofusic.TCombobox')
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

        buttons = tk.Frame(advanced, bg=p.card)
        buttons.pack(fill='x', padx=18, pady=4)
        self._button(buttons, 'Ouvrir Musiques', lambda: _open_path(Path(self.config.music_dir) if self.config.music_dir else default_music_dir()), p).pack(side='left', padx=(0, 8))
        self._button(buttons, 'Ouvrir données Dofusic', lambda: _open_path(user_data_dir()), p).pack(side='left')

        footer = tk.Frame(self.window, bg=p.bg)
        footer.pack(fill='x', padx=18, pady=(0, 16))
        self._button(footer, 'Appliquer', self._apply, p, accent=True).pack(side='right')
        self._button(footer, 'Fermer', self.close, p).pack(side='right', padx=(0, 8))
        # Explicit escape hatch if saving fails (e.g. read-only user folder).
        self._button(footer, 'Annuler', self.cancel, p).pack(side='right', padx=(0, 8))

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

    def _apply(self, live_theme: bool = False) -> bool:
        try:
            selected_tab = self.notebook.select()
        except tk.TclError:
            selected_tab = None

        try:
            candidate = replace(
                self.config,
                theme=self.theme_var.get(),
                show_visualizer=bool(self.visualizer_var.get()),
                normalize_loudness=bool(self.normalize_var.get()),
                fade_ms=int(self.fade_var.get()),
                always_on_top=bool(self.topmost_var.get()),
            ).sanitized()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror('Paramètres invalides', str(exc), parent=self.window)
            return False
        try:
            save_config(candidate, self.config_file)
        except OSError as exc:
            messagebox.showerror(
                'Sauvegarde impossible',
                'Les paramètres n’ont pas été appliqués :\n' + str(exc),
                parent=self.window,
            )
            return False
        # Only publish settings after persistent storage succeeded.
        for field in fields(AppConfig):
            setattr(self.config, field.name, getattr(candidate, field.name))
        self.on_apply()
        if selected_tab:
            try:
                self.notebook.select(selected_tab)
            except tk.TclError:
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
                except tk.TclError:
                    pass

            try:
                self.window.after_idle(keep_settings_visible)
            except tk.TclError:
                pass
        return True

    def close(self) -> None:
        if not self._apply():
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def cancel(self) -> None:
        """Discard unsaved UI changes and close, even on a read-only disk."""
        try:
            self.window.destroy()
        except tk.TclError:
            pass
