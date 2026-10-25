"""Fenêtre de bibliothèque locale de Dofusic (aucune dépendance réseau)."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

from dofusic.ui.themes import get_theme, recolor_widget_tree
from dofusic.ui.window_chrome import apply_window_chrome, schedule_window_chrome


def filter_local_tracks(tracks: tuple[Path, ...], query: str) -> tuple[Path, ...]:
    """Recherche insensible à la casse et aux accents approximatifs du nom."""
    from dofusic.text import normalize_text

    term = normalize_text(query or '')
    if not term:
        return tracks
    return tuple(path for path in tracks if term in normalize_text(path.stem))


class MusicWindow:
    POLL_MS = 150
    DEFAULT_SIZE = (760, 540)
    MIN_SIZE = (700, 460)
    RESULTS_SCROLLBAR_WIDTH = 12
    RESULTS_SCROLLBAR_GAP = 4
    RESULTS_SCROLLBAR_RIGHT_PAD = 2

    def __init__(self, parent, session, config) -> None:
        self.parent = parent
        self.session = session
        self.config = config
        self.p = get_theme(config.theme)
        self.window = tk.Toplevel(parent)
        self.window.title('Dofusic Musique locale')
        self.window.geometry(f'{self.DEFAULT_SIZE[0]}x{self.DEFAULT_SIZE[1]}')
        self.window.minsize(*self.MIN_SIZE)
        self.window.resizable(True, True)
        self.window.configure(bg=self.p.bg)
        try:
            self.window.attributes('-topmost', bool(config.always_on_top))
        except tk.TclError:
            pass
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self._closed = False
        self._local_tracks_snapshot: tuple[Path, ...] = ()
        self._last_queue_signature = None
        self._last_playback_signature = None
        self._build()
        apply_window_chrome(self.window, self.p)
        self._refresh_library()
        self._poll()

    def _build(self) -> None:
        p = self.p
        header = tk.Frame(self.window, bg=p.bg)
        header.pack(fill='x', padx=16, pady=(14, 9))
        tk.Label(header, text='♫  Musiques locales', bg=p.bg, fg=p.text,
                 font=('Segoe UI', 15, 'bold')).pack(side='left')
        self.status_var = tk.StringVar(value='')
        tk.Button(
            header, text='Reprendre Dofus', command=self._resume_dofus,
            bg=p.card_alt, fg=p.text, activebackground=p.accent_dark,
            activeforeground=p.text, relief='flat', padx=12, pady=6,
        ).pack(side='right')

        search = tk.Frame(self.window, bg=p.card, padx=10, pady=8)
        search.pack(fill='x', padx=16)
        self.search_var = tk.StringVar(value=self.session.search_query)
        self.search_entry = tk.Entry(
            search, textvariable=self.search_var, bg=p.card_field, fg=p.text,
            insertbackground=p.text, relief='flat', font=('Segoe UI', 11),
        )
        self.search_entry.pack(side='left', fill='x', expand=True, ipady=6)
        self.search_entry.bind('<KeyRelease>', self._filter_local_results)
        self.search_entry.bind('<Return>', self._play_first_result)
        tk.Button(
            search, text='Actualiser', command=self._refresh_library,
            bg=p.card_alt, fg=p.text, activebackground=p.accent_dark,
            activeforeground=p.text, relief='flat', padx=10, pady=5,
        ).pack(side='right', padx=(8, 0))

        body = tk.Frame(self.window, bg=p.bg)
        body.pack(fill='both', expand=True, padx=16, pady=(10, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        self.left = tk.Frame(body, bg=p.card)
        self.left.grid(row=0, column=0, sticky='nsew')
        self.right = tk.Frame(body, bg=p.card_alt, width=210)
        self.right.grid(row=0, column=1, sticky='ns', padx=(10, 0))
        self.right.grid_propagate(False)
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(4, weight=1)

        tk.Label(self.left, text='MUSIQUES LOCALES', bg=p.card, fg=p.accent,
                 font=('Segoe UI', 8, 'bold')).pack(anchor='w', padx=12, pady=(10, 4))
        holder = tk.Frame(self.left, bg=p.card)
        holder.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.results_canvas = tk.Canvas(holder, bg=p.card, highlightthickness=0, bd=0)
        self.results_scrollbar = tk.Scrollbar(
            holder, orient='vertical', command=self.results_canvas.yview,
            bg=p.card_alt, troughcolor=p.card, activebackground=p.accent_dark,
            highlightthickness=0, bd=0, relief='flat',
            width=self.RESULTS_SCROLLBAR_WIDTH,
        )
        self.results_canvas.configure(yscrollcommand=self.results_scrollbar.set)
        self.results_canvas.pack(side='left', fill='both', expand=True)
        self.results_scrollbar.pack(side='right', fill='y', padx=(self.RESULTS_SCROLLBAR_GAP, self.RESULTS_SCROLLBAR_RIGHT_PAD))
        self.results_frame = tk.Frame(self.results_canvas, bg=p.card)
        self._results_window = self.results_canvas.create_window((0, 0), window=self.results_frame, anchor='nw')
        self.results_frame.bind('<Configure>', self._results_rows_configured)
        self.results_canvas.bind('<Configure>', self._results_canvas_configured)
        self.results_canvas.bind('<MouseWheel>', self._results_mousewheel)

        tk.Label(self.right, text='EN COURS', bg=p.card_alt, fg=p.accent,
                 font=('Segoe UI', 8, 'bold')).grid(row=0, column=0, sticky='w', padx=12, pady=(12, 5))
        self.current_var = tk.StringVar(value='Musique Dofus')
        tk.Label(self.right, textvariable=self.current_var, wraplength=180, justify='left',
                 bg=p.card_alt, fg=p.text, font=('Segoe UI', 9, 'bold')).grid(
                     row=1, column=0, sticky='ew', padx=12)
        controls = tk.Frame(self.right, bg=p.card_alt)
        controls.grid(row=2, column=0, sticky='ew', padx=12, pady=8)
        self.repeat_button = tk.Button(
            controls, text='↻ Boucle OFF', command=self._toggle_repeat,
            bg=p.card_field, fg=p.text, activebackground=p.accent_dark,
            activeforeground=p.text, relief='flat', padx=8, pady=4,
        )
        self.repeat_button.pack(side='left')
        tk.Label(self.right, text='FILE D’ATTENTE', bg=p.card_alt, fg=p.accent,
                 font=('Segoe UI', 8, 'bold')).grid(row=3, column=0, sticky='w', padx=12, pady=(4, 5))
        queue_holder = tk.Frame(self.right, bg=p.card_alt)
        queue_holder.grid(row=4, column=0, sticky='nsew', padx=12)
        self.queue_canvas = tk.Canvas(queue_holder, bg=p.card_field, highlightthickness=0, bd=0)
        self.queue_scrollbar = tk.Scrollbar(
            queue_holder, orient='vertical', command=self.queue_canvas.yview,
            bg=p.card_alt, troughcolor=p.card_field, activebackground=p.accent_dark,
            highlightthickness=0, bd=0, relief='flat', width=8,
        )
        self.queue_canvas.configure(yscrollcommand=self.queue_scrollbar.set)
        self.queue_canvas.pack(side='left', fill='both', expand=True)
        self.queue_scrollbar.pack(side='right', fill='y')
        self.queue_rows = tk.Frame(self.queue_canvas, bg=p.card_field)
        self._queue_window = self.queue_canvas.create_window((0, 0), window=self.queue_rows, anchor='nw')
        self.queue_rows.bind('<Configure>', self._queue_rows_configured)
        self.queue_canvas.bind('<Configure>', self._queue_canvas_configured)
        self.queue_canvas.bind('<MouseWheel>', self._queue_mousewheel)
        footer = tk.Frame(self.right, bg=p.card_alt)
        footer.grid(row=5, column=0, sticky='ew', padx=12, pady=(8, 10))
        tk.Button(
            footer, text='Vider', command=self._clear_queue, bg=p.card_field, fg=p.text,
            activebackground=p.border, activeforeground=p.text, relief='flat', padx=10, pady=4,
        ).pack(side='right')
        self.status_label = tk.Label(self.window, textvariable=self.status_var, bg=p.bg,
                                     fg=p.muted, anchor='w', font=('Segoe UI', 8))
        self.status_label.pack(fill='x', padx=16, pady=(0, 10))
        self.search_entry.focus_set()

    def _refresh_library(self) -> None:
        try:
            self._local_tracks_snapshot = self.session.local_tracks(refresh=True)
        except Exception:
            self._local_tracks_snapshot = ()
            self.status_var.set('Impossible de lire le dossier des musiques')
        self._filter_local_results()

    def _filtered_local_tracks(self, query: str) -> tuple[Path, ...]:
        return filter_local_tracks(self._local_tracks_snapshot, query)

    def _filter_local_results(self, _event=None) -> None:
        query = self.search_var.get()
        self.session.remember_search(query)
        tracks = self._filtered_local_tracks(query)
        if tracks != getattr(self, '_last_results', None):
            self._render_local_results(tracks)
            self._last_results = tracks

    def _play_first_result(self, _event=None) -> None:
        tracks = self._filtered_local_tracks(self.search_var.get())
        if tracks:
            self._play_local(tracks[0])

    def _render_local_results(self, tracks: tuple[Path, ...]) -> None:
        p = self.p
        for widget in self.results_frame.winfo_children():
            widget.destroy()
        if not tracks:
            tk.Label(
                self.results_frame, text='Aucune musique locale', bg=p.card, fg=p.muted,
            ).pack(anchor='w', padx=12, pady=12)
        for path in tracks:
            row = tk.Frame(self.results_frame, bg=p.card_field)
            row.pack(fill='x', padx=3, pady=(0, 4))
            controls = tk.Frame(row, bg=p.card_field)
            controls.pack(side='right', padx=(4, 8))
            tk.Button(
                controls, text='▶', command=lambda item=path: self._play_local(item),
                bg=p.accent_dark, fg=p.text, relief='flat', padx=9, pady=5,
            ).pack(side='left', padx=(0, 3))
            tk.Button(
                controls, text='+', command=lambda item=path: self._enqueue_local(item),
                bg=p.card_alt, fg=p.text, relief='flat', padx=9, pady=5,
            ).pack(side='left')
            label = tk.Label(row, text=path.stem, bg=p.card_field, fg=p.text,
                             anchor='w', font=('Segoe UI', 9))
            label.pack(side='left', fill='x', expand=True, padx=(8, 0))
            label.bind('<Double-Button-1>', lambda _ev, item=path: self._play_local(item))
            for widget in (row, controls, label):
                widget.bind('<MouseWheel>', self._results_mousewheel)
        self.results_canvas.yview_moveto(0)
        self._results_rows_configured()

    def _play_local(self, path: Path) -> None:
        self.session.play_local_track(path)
        self._refresh_playback_panel()

    def _enqueue_local(self, path: Path) -> None:
        self.session.enqueue_local_track(path)
        self._refresh_playback_panel()

    def _resume_dofus(self) -> None:
        self.session.resume_dofus_music()
        self._refresh_playback_panel()

    def _toggle_repeat(self) -> None:
        self.session.set_repeat(not self.session.repeat_current)
        self._refresh_playback_panel()

    def _remove_queue_index(self, index: int) -> None:
        self.session.remove_pending(index)
        self._refresh_playback_panel()

    def _clear_queue(self) -> None:
        self.session.clear_queue()
        self._refresh_playback_panel()

    def _refresh_playback_panel(self) -> None:
        p = self.p
        current = self.session.current
        pending = self.session.pending
        repeat = self.session.repeat_current
        self.status_var.set(self.session.status)
        playback_key = (current.key if current else None, bool(repeat))
        if playback_key != self._last_playback_signature:
            self._last_playback_signature = playback_key
            self.current_var.set(current.title if current else 'Musique Dofus')
            self.repeat_button.configure(text='↻ Boucle ON' if repeat else '↻ Boucle OFF')
        queue_key = tuple(item.key for item in pending)
        if queue_key != self._last_queue_signature:
            self._last_queue_signature = queue_key
            for widget in self.queue_rows.winfo_children():
                widget.destroy()
            for index, item in enumerate(pending):
                row = tk.Frame(self.queue_rows, bg=p.card_field)
                row.pack(fill='x', padx=2, pady=2)
                tk.Button(
                    row, text='×', command=lambda idx=index: self._remove_queue_index(idx),
                    bg=p.card_alt, fg=p.muted, relief='flat', bd=0, padx=4,
                ).pack(side='right')
                tk.Label(
                    row, text=f'{index + 1}.  {item.title}', bg=p.card_field,
                    fg=p.text, anchor='w', wraplength=135,
                ).pack(side='left', fill='x', expand=True)
                row.bind('<MouseWheel>', self._queue_mousewheel)
            self._queue_rows_configured()

    def _results_rows_configured(self, _event=None) -> None:
        self.results_canvas.configure(scrollregion=self.results_canvas.bbox('all'))

    def _results_canvas_configured(self, event) -> None:
        self.results_canvas.itemconfigure(self._results_window, width=event.width)

    def _results_mousewheel(self, event) -> None:
        self.results_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def _queue_rows_configured(self, _event=None) -> None:
        self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox('all'))

    def _queue_canvas_configured(self, event) -> None:
        self.queue_canvas.itemconfigure(self._queue_window, width=event.width)

    def _queue_mousewheel(self, event) -> None:
        self.queue_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            self._refresh_playback_panel()
        finally:
            if not self._closed:
                try:
                    self.window.after(self.POLL_MS, self._poll)
                except tk.TclError:
                    pass

    def apply_preferences(self) -> None:
        self.p = get_theme(self.config.theme)
        recolor_widget_tree(self.window, self.p)
        try:
            self.window.configure(bg=self.p.bg)
            self.window.attributes('-topmost', bool(self.config.always_on_top))
            self.window.minsize(*self.MIN_SIZE)
            schedule_window_chrome(self.window, self.p)
            self.results_canvas.configure(bg=self.p.card)
            self.queue_canvas.configure(bg=self.p.card_field)
        except tk.TclError:
            return
        self._last_queue_signature = None
        self._last_playback_signature = None
        self._last_results = None
        self._filter_local_results()
        self._refresh_playback_panel()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.session.remember_search(self.search_var.get())
        try:
            self.window.destroy()
        except tk.TclError:
            pass
