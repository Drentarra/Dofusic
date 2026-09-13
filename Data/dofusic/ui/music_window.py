from __future__ import annotations

import base64
import tkinter as tk
from pathlib import Path

from dofusic.online.models import OnlineTrack
from dofusic.location.suggestions import location_name_suggestions
from dofusic.online.search import SearchPhase
from dofusic.online.session import MediaActionPhase
from dofusic.ui.themes import get_theme, recolor_widget_tree
from dofusic.ui.window_chrome import apply_window_chrome, schedule_window_chrome


def online_typing_actions(
    query: str, *, suggestions_enabled: bool, key: str | None = None
) -> tuple[str, ...]:
    """Return only the lightweight work allowed for a typing event.

    Enter is a submission action, not a typing action. Ignoring its KeyRelease
    prevents a late suggestion request from racing the explicit search.
    """
    if str(key or '') in {'Return', 'KP_Enter'}:
        return tuple()
    text = (query or '').strip()
    if len(text) < 2 or not suggestions_enabled:
        return tuple()
    return ('suggest',)



class RenameDownloadDialog:
    def __init__(self, parent, track: OnlineTrack, repository, on_confirm, *, theme='emerald') -> None:
        self.repository = repository
        self.on_confirm = on_confirm
        self.track = track
        self.p = get_theme(theme)
        self.window = tk.Toplevel(parent)
        self.window.title('Ajouter à Musiques')
        self.window.geometry('430x330')
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.configure(bg=self.p.bg)
        apply_window_chrome(self.window, self.p)

        tk.Label(self.window, text='Nom du fichier', bg=self.p.bg, fg=self.p.text,
                 font=('Segoe UI', 15, 'bold')).pack(anchor='w', padx=18, pady=(18, 4))
        self.name_var = tk.StringVar(value='')
        entry = tk.Entry(self.window, textvariable=self.name_var, bg=self.p.card_field, fg=self.p.text,
                         insertbackground=self.p.text, relief='flat', font=('Segoe UI', 11))
        entry.pack(fill='x', padx=18, ipady=8)
        entry.bind('<KeyRelease>', self._refresh_suggestions)
        entry.bind('<Return>', lambda _e: self._confirm())
        entry.focus_set()

        tk.Label(self.window, text='SUGGESTIONS DOFUS', bg=self.p.bg, fg=self.p.accent,
                 font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=18, pady=(16, 6))
        self.suggestions = tk.Listbox(self.window, height=7, bg=self.p.card, fg=self.p.text,
                                      selectbackground=self.p.accent_dark, selectforeground=self.p.text,
                                      relief='flat', bd=0, highlightthickness=0, font=('Segoe UI', 10))
        self.suggestions.pack(fill='both', expand=True, padx=18)
        self.suggestions.bind('<Double-Button-1>', self._choose_suggestion)
        self.suggestions.bind('<Return>', self._choose_suggestion)

        footer = tk.Frame(self.window, bg=self.p.bg)
        footer.pack(fill='x', padx=18, pady=16)
        tk.Button(footer, text='Annuler', command=self.window.destroy, bg=self.p.card_alt, fg=self.p.text,
                  activebackground=self.p.border, activeforeground=self.p.text, relief='flat', padx=14, pady=7).pack(side='right')
        tk.Button(footer, text='Ajouter à Musiques', command=self._confirm, bg=self.p.accent_dark, fg=self.p.text,
                  activebackground=self.p.accent, activeforeground=self.p.bg, relief='flat', padx=14, pady=7,
                  font=('Segoe UI', 9, 'bold')).pack(side='right', padx=(0, 8))

    def _refresh_suggestions(self, _event=None) -> None:
        values = location_name_suggestions(self.repository, self.name_var.get(), limit=8)
        self.suggestions.delete(0, 'end')
        for value in values:
            self.suggestions.insert('end', value)

    def _choose_suggestion(self, _event=None) -> None:
        selection = self.suggestions.curselection()
        if not selection:
            return
        self.name_var.set(self.suggestions.get(selection[0]))
        self._confirm()

    def _confirm(self) -> None:
        value = self.name_var.get().strip() or self.track.title
        self.window.grab_release()
        self.window.destroy()
        self.on_confirm(value)


class MusicWindow:
    POLL_MS = 125
    DEFAULT_SIZE = (760, 540)
    MIN_SIZE = (700, 500)
    THUMBNAIL_SIZE = (112, 63)
    RESULTS_SCROLLBAR_WIDTH = 12
    RESULTS_SCROLLBAR_GAP = 4
    RESULTS_SCROLLBAR_RIGHT_PAD = 2

    def __init__(self, parent, session, config, repository, *, on_library_saved=None) -> None:
        self.parent = parent
        self.session = session
        self.config = config
        self.repository = repository
        self.on_library_saved = on_library_saved or (lambda _path: None)
        self.p = get_theme(config.theme)
        self.window = tk.Toplevel(parent)
        self.window.title('Dofusic — Musique online')
        self.window.geometry(f'{self.DEFAULT_SIZE[0]}x{self.DEFAULT_SIZE[1]}')
        self.window.resizable(True, True)
        self.window.minsize(*self.MIN_SIZE)
        self.window.configure(bg=self.p.bg)
        try:
            self.window.attributes('-topmost', bool(self.config.always_on_top))
        except tk.TclError:
            pass
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self._closed = False
        self._after_id = None
        self._results_query = ''
        self._results: tuple[OnlineTrack, ...] = tuple()
        self._last_queue_signature: tuple[tuple[tuple[str, str], str], ...] | None = None
        self._health_ok = True
        self._spinner_frames = ('◐', '◓', '◑', '◒')
        self._spinner_index = 0
        self._play_buttons: dict[str, tk.Button] = {}
        self._play_title_buttons: dict[str, tk.Button] = {}
        self._download_buttons: dict[str, tk.Button] = {}
        self._thumbnail_labels: dict[str, tk.Label] = {}
        self._thumbnail_images: dict[str, tk.PhotoImage] = {}
        self._last_search_revision = -1
        self._last_suggestion_revision = -1
        self._last_media_revision = -1
        self._source_mode = 'local' if str(getattr(session, 'music_source_mode', 'online')).lower() == 'local' else 'online'
        self._local_tracks_snapshot: tuple[Path, ...] = tuple()
        self._build()
        apply_window_chrome(self.window, self.p)
        self._poll()

    def _build(self) -> None:
        p = self.p
        header = tk.Frame(self.window, bg=p.bg)
        header.pack(fill='x', padx=16, pady=(14, 9))
        tk.Label(header, text='♫  Musiques', bg=p.bg, fg=p.text, font=('Segoe UI', 15, 'bold')).pack(side='left')
        self.status_var = tk.StringVar(value='')
        self.health_canvas = tk.Canvas(header, width=14, height=14, bg=p.bg, highlightthickness=0, bd=0)
        self.health_canvas.pack(side='right', padx=(8, 2))
        self.health_dot = self.health_canvas.create_oval(3, 3, 11, 11, fill=p.accent, outline='')

        source_bar = tk.Frame(self.window, bg=p.bg)
        source_bar.pack(fill='x', padx=16, pady=(0, 8))
        self.online_source_button = tk.Button(
            source_bar, text='ONLINE', command=lambda: self._set_source_mode('online'),
            bg=p.accent_dark, fg=p.text, activebackground=p.accent, activeforeground=p.bg,
            relief='flat', padx=12, pady=5, font=('Segoe UI', 9, 'bold'),
        )
        self.online_source_button.pack(side='left')
        self.local_source_button = tk.Button(
            source_bar, text='LOCAL', command=lambda: self._set_source_mode('local'),
            bg=p.card_alt, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
            relief='flat', padx=12, pady=5, font=('Segoe UI', 9, 'bold'),
        )
        self.local_source_button.pack(side='left', padx=(6, 0))
        self.resume_dofus_button = tk.Button(
            source_bar, text='Reprendre Dofus', command=self._resume_dofus,
            bg=p.card_alt, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
            relief='flat', padx=12, pady=5,
        )
        self.resume_dofus_button.pack(side='right')

        search_frame = tk.Frame(self.window, bg=p.card, padx=10, pady=8)
        search_frame.pack(fill='x', padx=16)
        search_frame.grid_columnconfigure(0, weight=1)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_frame, textvariable=self.search_var, bg=p.card_field, fg=p.text,
            insertbackground=p.text, relief='flat', font=('Segoe UI', 11),
        )
        self.search_entry.grid(row=0, column=0, sticky='ew', ipady=6)
        self.search_busy_label = tk.Label(
            search_frame, text='', width=2, bg=p.card, fg=p.accent,
            font=('Segoe UI Symbol', 12, 'bold'),
        )
        self.search_busy_label.grid(row=0, column=1, padx=(8, 0))
        self.search_busy_label.grid_remove()
        self.search_entry.bind('<KeyRelease>', self._schedule_live_search)
        self.search_entry.bind('<Return>', self._enter_play)

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

        self.suggestion_section = tk.Frame(self.left, bg=p.card)
        self.suggestion_section.pack(fill='x')
        tk.Label(self.suggestion_section, text='SUGGESTIONS', bg=p.card, fg=p.accent, font=('Segoe UI', 8, 'bold')).pack(anchor='w', padx=12, pady=(10, 4))
        self.suggestion_list = tk.Listbox(
            self.suggestion_section, height=3, bg=p.card_field, fg=p.muted,
            selectbackground=p.accent_dark, selectforeground=p.text, relief='flat', bd=0,
            highlightthickness=0, font=('Segoe UI', 9),
        )
        self.suggestion_list.pack(fill='x', padx=12)
        self.suggestion_list.bind('<Double-Button-1>', self._suggestion_clicked)
        self.suggestion_list.bind('<Return>', self._suggestion_clicked)

        self.results_label = tk.Label(self.left, text='RÉSULTATS', bg=p.card, fg=p.accent, font=('Segoe UI', 8, 'bold'))
        self.results_label.pack(anchor='w', padx=12, pady=(10, 4))
        self.results_holder = tk.Frame(self.left, bg=p.card)
        self.results_holder.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.results_canvas = tk.Canvas(
            self.results_holder, bg=p.card, highlightthickness=0, bd=0, relief='flat',
        )
        self.results_scrollbar = tk.Scrollbar(
            self.results_holder, orient='vertical', command=self.results_canvas.yview,
            bg=p.card_alt, troughcolor=p.card, activebackground=p.accent_dark,
            highlightthickness=0, bd=0, relief='flat', width=self.RESULTS_SCROLLBAR_WIDTH,
        )
        self.results_canvas.configure(yscrollcommand=self.results_scrollbar.set)
        self.results_canvas.pack(side='left', fill='both', expand=True)
        self.results_scrollbar.pack(
            side='right', fill='y',
            padx=(self.RESULTS_SCROLLBAR_GAP, self.RESULTS_SCROLLBAR_RIGHT_PAD),
        )
        self.results_frame = tk.Frame(self.results_canvas, bg=p.card)
        self._results_window = self.results_canvas.create_window((0, 0), window=self.results_frame, anchor='nw')
        self.results_frame.bind('<Configure>', self._results_rows_configured)
        self.results_canvas.bind('<Configure>', self._results_canvas_configured)
        self.results_canvas.bind('<MouseWheel>', self._results_mousewheel)
        self._render_results(tuple())

        tk.Label(self.right, text='EN COURS', bg=p.card_alt, fg=p.accent, font=('Segoe UI', 8, 'bold')).grid(
            row=0, column=0, sticky='w', padx=12, pady=(12, 5),
        )
        self.current_var = tk.StringVar(value='Musique Dofus locale')
        tk.Label(
            self.right, textvariable=self.current_var, wraplength=180, justify='left',
            bg=p.card_alt, fg=p.text, font=('Segoe UI', 9, 'bold'),
        ).grid(row=1, column=0, sticky='ew', padx=12)
        controls = tk.Frame(self.right, bg=p.card_alt)
        controls.grid(row=2, column=0, sticky='ew', padx=12, pady=8)
        self.repeat_button = tk.Button(
            controls, text='↻ Boucle OFF', command=self._toggle_repeat, bg=p.card_field, fg=p.text,
            activebackground=p.accent_dark, activeforeground=p.text, relief='flat', padx=8, pady=4,
        )
        self.repeat_button.pack(side='left')

        self.queue_label = tk.Label(self.right, text='FILE D’ATTENTE', bg=p.card_alt, fg=p.accent, font=('Segoe UI', 8, 'bold'))
        self.queue_label.grid(row=3, column=0, sticky='w', padx=12, pady=(4, 5))
        self.queue_holder = tk.Frame(self.right, bg=p.card_alt)
        self.queue_holder.grid(row=4, column=0, sticky='nsew', padx=12)
        self.queue_canvas = tk.Canvas(self.queue_holder, bg=p.card_field, highlightthickness=0, bd=0, relief='flat')
        self.queue_scrollbar = tk.Scrollbar(
            self.queue_holder, orient='vertical', command=self.queue_canvas.yview,
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

        self.queue_buttons = tk.Frame(self.right, bg=p.card_alt)
        self.queue_buttons.grid(row=5, column=0, sticky='ew', padx=12, pady=(8, 10))
        tk.Button(
            self.queue_buttons, text='Vider', command=self._clear_queue, bg=p.card_field, fg=p.text,
            activebackground=p.border, activeforeground=p.text, relief='flat', padx=10, pady=4,
        ).pack(side='right')

        self._set_source_mode(self._source_mode)
        self.search_entry.focus_set()

    def apply_preferences(self) -> None:
        self.p = get_theme(self.config.theme)
        recolor_widget_tree(self.window, self.p)
        try:
            self.window.configure(bg=self.p.bg)
            self.window.attributes('-topmost', bool(self.config.always_on_top))
            self.window.resizable(True, True)
            self.window.minsize(*self.MIN_SIZE)
            schedule_window_chrome(self.window, self.p)
        except tk.TclError:
            pass
        try:
            self.queue_canvas.configure(bg=self.p.card_field)
            self.queue_rows.configure(bg=self.p.card_field)
            self.queue_scrollbar.configure(
                bg=self.p.card_alt, troughcolor=self.p.card_field, activebackground=self.p.accent_dark,
            )
            self.results_canvas.configure(bg=self.p.card)
            self.results_frame.configure(bg=self.p.card)
            self.results_scrollbar.configure(
                bg=self.p.card_alt, troughcolor=self.p.card, activebackground=self.p.accent_dark,
            )
        except Exception:
            pass
        self._set_health(self._health_ok)
        self._set_source_mode(self._source_mode)
        self._refresh_playback_panel()

    def _set_health(self, ok: bool) -> None:
        self._health_ok = bool(ok)
        try:
            self.health_canvas.configure(bg=self.p.bg)
            self.health_canvas.itemconfigure(self.health_dot, fill=self.p.accent if self._health_ok else self.p.red)
        except Exception:
            pass

    def _schedule_live_search(self, event=None) -> None:
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        query = self.search_var.get().strip()
        self.session.remember_search(self._source_mode, query, self._results if self._source_mode == 'online' else tuple())
        if self._source_mode == 'local':
            self._render_local_results(self._filtered_local_tracks(query))
            return
        actions = online_typing_actions(
            query,
            suggestions_enabled=bool(self.config.online_suggestions),
            key=getattr(event, 'keysym', None),
        )
        if not actions:
            if len(query) < 2:
                self._clear_discovery()
            return
        self._after_id = self.window.after(
            self.config.online_suggest_delay_ms,
            lambda q=query: self._begin_suggestions(q),
        )

    def _begin_suggestions(self, query: str) -> None:
        query = (query or '').strip()
        if len(query) < 2 or self._source_mode != 'online':
            return
        # Ignore a delayed callback if the user has already typed something else.
        if self.search_var.get().strip() != query:
            return
        self.session.submit_suggestions(query)

    def _begin_search(self, query: str) -> None:
        query = (query or '').strip()
        if not query:
            return
        snapshot = self.session.search_snapshot()
        if snapshot.phase is SearchPhase.LOADING and snapshot.query.casefold() == query.casefold():
            return
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self.session.remember_search('online', query, self._results)
        self.status_var.set(f'Recherche : {query}')
        # A full search is an explicit action (Enter / suggestion click).
        # Do not queue another suggestions request behind it.
        self.session.submit_search(query)
        self._update_busy_indicators()

    def _enter_play(self, _event=None) -> None:
        """Submit the current query. Enter never starts playback automatically."""
        query = self.search_var.get().strip()
        if not query:
            return
        if self._source_mode == 'local':
            tracks = self._filtered_local_tracks(query)
            if tracks:
                self._play_local(tracks[0])
            return
        self._begin_search(query)

    def _clear_discovery(self) -> None:
        self.suggestion_list.delete(0, 'end')
        self._results = tuple()
        self._results_query = ''
        if self._source_mode == 'local':
            self._render_local_results(self._filtered_local_tracks(''))
        else:
            self._render_results(tuple())

    def _suggestion_clicked(self, _event=None) -> None:
        selection = self.suggestion_list.curselection()
        if not selection:
            return
        query = self.suggestion_list.get(selection[0])
        self.search_var.set(query)
        self._begin_search(query)

    def _remember_current_search(self) -> None:
        try:
            query = self.search_var.get()
        except Exception:
            query = ''
        results = self._results if self._source_mode == 'online' else tuple()
        self.session.remember_search(self._source_mode, query, results)

    def _set_source_mode(self, mode: str) -> None:
        mode = 'local' if str(mode).lower() == 'local' else 'online'
        previous_mode = self._source_mode
        if previous_mode != mode:
            self._remember_current_search()
        self._source_mode = mode
        self.session.music_source_mode = mode
        query, remembered_results = self.session.search_memory(mode)
        p = self.p
        try:
            self.online_source_button.configure(bg=p.accent_dark if mode == 'online' else p.card_alt)
            self.local_source_button.configure(bg=p.accent_dark if mode == 'local' else p.card_alt)
        except Exception:
            pass
        self.search_var.set(query)
        if mode == 'local':
            self._local_tracks_snapshot = tuple(self.session.local_tracks(refresh=True))
            self.suggestion_section.pack_forget()
            self.results_label.configure(text='MUSIQUES LOCALES')
            self._render_local_results(self._filtered_local_tracks(query))
            self.status_var.set('Bibliothèque locale')
            try:
                self.repeat_button.configure(state='normal')
                self.queue_label.grid()
                self.queue_holder.grid()
                self.queue_buttons.grid()
            except Exception:
                pass
        else:
            try:
                self.session.prewarm()
            except Exception:
                pass
            if not self.suggestion_section.winfo_manager():
                self.suggestion_section.pack(fill='x', before=self.results_label)
            self.results_label.configure(text='RÉSULTATS')
            self.suggestion_list.delete(0, 'end')
            self._results = tuple(remembered_results)
            self._results_query = query if remembered_results else ''
            self._render_results(self._results)
            self.status_var.set(f'{len(self._results)} résultat(s)' if self._results else '')
            try:
                self.repeat_button.configure(state='normal')
                self.queue_label.grid()
                self.queue_holder.grid()
                self.queue_buttons.grid()
            except Exception:
                pass
        self.search_entry.focus_set()

    def _filtered_local_tracks(self, query: str) -> tuple[Path, ...]:
        tracks = self._local_tracks_snapshot
        needle = (query or '').strip().casefold()
        if not needle:
            return tracks
        return tuple(path for path in tracks if needle in path.stem.casefold())

    def _render_local_results(self, tracks: tuple[Path, ...]) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()
        p = self.p
        if not tracks:
            tk.Label(
                self.results_frame, text='Aucune musique locale', bg=p.card, fg=p.muted,
                font=('Segoe UI', 9),
            ).pack(anchor='w', padx=8, pady=8)
            self._results_rows_configured()
            return
        for path in tracks:
            row = tk.Frame(self.results_frame, bg=p.card_field, padx=8, pady=6)
            row.pack(fill='x', pady=2)
            title = path.stem if len(path.stem) <= 52 else path.stem[:49] + '…'
            tk.Button(
                row, text=title, command=lambda item=path: self._play_local(item), anchor='w',
                bg=p.card_field, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', bd=0, font=('Segoe UI', 9, 'bold'),
            ).pack(side='left', fill='x', expand=True)
            ext_label = tk.Label(
                row, text=path.suffix.lower().lstrip('.').upper(), bg=p.card_field, fg=p.muted,
                font=('Segoe UI', 8),
            )
            ext_label.pack(side='left', padx=(6, 4))
            add_button = tk.Button(
                row, text='+', command=lambda item=path: self._enqueue_local(item),
                bg=p.card_alt, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', width=3,
            )
            add_button.pack(side='right', padx=(4, 0))
            play_button = tk.Button(
                row, text='▶', command=lambda item=path: self._play_local(item),
                bg=p.accent_dark, fg=p.text, activebackground=p.accent, activeforeground=p.bg,
                relief='flat', width=3,
            )
            play_button.pack(side='right', padx=(6, 0))
            self._bind_results_mousewheel(row)
        self._results_rows_configured()

    def _play_local(self, path: Path) -> None:
        if self.session.play_local_track(path):
            self.status_var.set(f'Lecture locale : {path.stem}')
        else:
            self.status_var.set(self.session.status)
        self._refresh_playback_panel()

    def _enqueue_local(self, path: Path) -> None:
        if not self.session.enqueue_local_track(path):
            self.status_var.set(self.session.status)
        else:
            self.status_var.set(f'Ajouté à la file : {path.stem}')
        self._refresh_playback_panel()

    def _resume_dofus(self) -> None:
        self.session.resume_dofus_music()
        self.status_var.set('Retour à la musique Dofus')
        self._refresh_playback_panel()

    def _render_results(self, results: tuple[OnlineTrack, ...]) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()
        self._play_buttons = {}
        self._play_title_buttons = {}
        self._download_buttons = {}
        self._thumbnail_labels = {}
        self._thumbnail_images = {}
        p = self.p
        if not results:
            self._results_rows_configured()
            return
        for track in results:
            row = tk.Frame(self.results_frame, bg=p.card_field, padx=8, pady=6)
            row.pack(fill='x', pady=2)
            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1, minsize=80)
            row.grid_columnconfigure(2, weight=0)

            thumb_holder = tk.Frame(
                row, bg=p.card_alt, width=self.THUMBNAIL_SIZE[0], height=self.THUMBNAIL_SIZE[1],
            )
            thumb_holder.grid(row=0, column=0, sticky='w')
            thumb_holder.grid_propagate(False)
            thumb_label = tk.Label(
                thumb_holder, text='…', bg=p.card_alt, fg=p.muted,
                font=('Segoe UI', 12, 'bold'), anchor='center',
            )
            thumb_label.pack(fill='both', expand=True)
            self._thumbnail_labels[track.video_id] = thumb_label
            try:
                self.session.request_thumbnail(track, size=self.THUMBNAIL_SIZE)
            except Exception:
                pass

            info = tk.Frame(row, bg=p.card_field)
            info.grid(row=0, column=1, sticky='ew', padx=(8, 0))
            title = track.title if len(track.title) <= 58 else track.title[:55] + '…'
            title_button = tk.Button(
                info, text=title, command=lambda t=track: self._play(t), anchor='w',
                bg=p.card_field, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', bd=0, font=('Segoe UI', 9, 'bold'),
            )
            title_button.pack(fill='x')
            self._play_title_buttons[track.video_id] = title_button
            meta = f'{track.author or "—"}  •  {track.duration_text}'
            tk.Label(info, text=meta, anchor='w', bg=p.card_field, fg=p.muted, font=('Segoe UI', 8)).pack(fill='x')

            # Action controls own a fixed column; titles and thumbnails can never
            # push them outside the result row.
            controls = tk.Frame(row, bg=p.card_field)
            controls.grid(row=0, column=2, sticky='e', padx=(8, 0))
            play_button = tk.Button(
                controls, text='▶', command=lambda t=track: self._play(t),
                bg=p.accent_dark, fg=p.text, activebackground=p.accent, activeforeground=p.bg,
                relief='flat', width=3,
            )
            play_button.pack(side='left')
            tk.Button(
                controls, text='+', command=lambda t=track: self._enqueue(t),
                bg=p.card_alt, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', width=3,
            ).pack(side='left', padx=(4, 0))
            download_button = tk.Button(
                controls, text='↓', command=lambda t=track: self._download(t),
                bg=p.card_alt, fg=p.text, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', width=3,
            )
            download_button.pack(side='left', padx=(4, 0))
            self._play_buttons[track.video_id] = play_button
            self._download_buttons[track.video_id] = download_button
            self._bind_results_mousewheel(row)
        self._update_busy_indicators()
        self._results_rows_configured()

    def _update_busy_indicators(self) -> None:
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        spinner = self._spinner_frames[self._spinner_index]

        search_busy = self.session.search_snapshot().phase is SearchPhase.LOADING
        try:
            if search_busy:
                self.search_busy_label.configure(text=spinner)
                self.search_busy_label.grid()
            else:
                self.search_busy_label.configure(text='')
                self.search_busy_label.grid_remove()
        except Exception:
            pass

        preparing_id = ''
        preparing = self.session.preparing
        if preparing is not None and preparing.online_track is not None:
            preparing_id = preparing.online_track.video_id
        for video_id, button in tuple(self._play_buttons.items()):
            try:
                busy = bool(preparing_id and video_id == preparing_id)
                button.configure(text=spinner if busy else '▶', state='disabled' if preparing_id else 'normal')
            except Exception:
                pass
        for button in tuple(self._play_title_buttons.values()):
            try:
                button.configure(state='disabled' if preparing_id else 'normal')
            except Exception:
                pass

        downloading_id = self.session.media_busy_video_id()
        for video_id, button in tuple(self._download_buttons.items()):
            try:
                busy = bool(downloading_id and video_id == downloading_id)
                button.configure(text=spinner if busy else '↓', state='disabled' if downloading_id else 'normal')
            except Exception:
                pass

    def _play(self, track: OnlineTrack) -> None:
        if self.session.play_now(track):
            self.status_var.set(f'Préparation : {track.title}')
        else:
            self.status_var.set(self.session.status)
        self._update_busy_indicators()
        self._refresh_playback_panel()

    def _enqueue(self, track: OnlineTrack) -> None:
        self.session.enqueue(track)
        self._refresh_playback_panel()

    def _toggle_repeat(self) -> None:
        self.session.set_repeat(not self.session.repeat_current)
        self._refresh_playback_panel()

    def _remove_queue_index(self, index: int) -> None:
        self.session.remove_pending(index)
        self._refresh_playback_panel()

    def _results_rows_configured(self, _event=None) -> None:
        try:
            self.results_canvas.configure(scrollregion=self.results_canvas.bbox('all'))
        except Exception:
            pass

    def _results_canvas_configured(self, event) -> None:
        try:
            self.results_canvas.itemconfigure(self._results_window, width=max(1, int(event.width)))
        except Exception:
            pass

    def _results_mousewheel(self, event) -> None:
        delta = int(getattr(event, 'delta', 0))
        if delta:
            self.results_canvas.yview_scroll(-1 if delta > 0 else 1, 'units')

    def _bind_results_mousewheel(self, widget) -> None:
        try:
            widget.bind('<MouseWheel>', self._results_mousewheel)
            for child in widget.winfo_children():
                self._bind_results_mousewheel(child)
        except Exception:
            pass

    def _queue_rows_configured(self, _event=None) -> None:
        try:
            self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox('all'))
        except Exception:
            pass

    def _queue_canvas_configured(self, event) -> None:
        try:
            self.queue_canvas.itemconfigure(self._queue_window, width=max(1, int(event.width)))
        except Exception:
            pass

    def _queue_mousewheel(self, event) -> None:
        delta = int(getattr(event, 'delta', 0))
        if delta:
            self.queue_canvas.yview_scroll(-1 if delta > 0 else 1, 'units')

    def _clear_queue(self) -> None:
        self.session.clear_queue()
        self._refresh_playback_panel()

    def _download(self, track: OnlineTrack) -> None:
        if not self.session.begin_download(track):
            self.status_var.set('Un téléchargement est déjà en cours')
            return
        self.status_var.set(f'Téléchargement : {track.title}')
        self._update_busy_indicators()

    def _open_rename(self, track: OnlineTrack) -> None:
        RenameDownloadDialog(
            self.window,
            track,
            self.repository,
            lambda name: self._save_download(track, name),
            theme=self.config.theme,
        )

    def _save_download(self, track: OnlineTrack, name: str) -> None:
        if self.session.begin_save(track, name):
            self.status_var.set('Ajout dans le dossier Musiques…')
        else:
            self.status_var.set('Une opération média est déjà en cours')

    def _refresh_playback_panel(self) -> None:
        current = self.session.current
        if current is not None:
            self.current_var.set(current.title)
        else:
            self.current_var.set('Musique Dofus automatique')
        self.repeat_button.configure(text='↻ Boucle ON' if self.session.repeat_current else '↻ Boucle OFF')
        pending = self.session.pending
        signature = tuple((item.key, item.title) for item in pending)
        if signature == self._last_queue_signature:
            return
        self._last_queue_signature = signature
        for child in self.queue_rows.winfo_children():
            child.destroy()
        p = self.p
        for index, item in enumerate(pending):
            row = tk.Frame(self.queue_rows, bg=p.card_field, padx=6, pady=4)
            row.pack(fill='x', pady=(0, 1))
            row.grid_columnconfigure(1, weight=1, minsize=1)
            number = tk.Label(row, text=f'{index + 1}.', bg=p.card_field, fg=p.muted, font=('Segoe UI', 8))
            number.grid(row=0, column=0, sticky='w')
            title = item.title if len(item.title) <= 28 else item.title[:27] + '…'
            label = tk.Label(row, text=title, anchor='w', bg=p.card_field, fg=p.text, font=('Segoe UI', 8))
            label.grid(row=0, column=1, sticky='ew', padx=(4, 3))
            remove = tk.Button(
                row, text='×', command=lambda i=index: self._remove_queue_index(i),
                bg=p.card_field, fg=p.muted, activebackground=p.accent_dark, activeforeground=p.text,
                relief='flat', bd=0, padx=4, pady=0, font=('Segoe UI', 10, 'bold'), cursor='hand2',
            )
            remove.grid(row=0, column=2, sticky='e')
            for widget in (row, number, label, remove):
                widget.bind('<MouseWheel>', self._queue_mousewheel)
        self._queue_rows_configured()

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            for video_id, label in tuple(self._thumbnail_labels.items()):
                if label is None or not label.winfo_exists():
                    continue
                png = self.session.take_thumbnail(video_id)
                if not png:
                    continue
                try:
                    image = tk.PhotoImage(data=base64.b64encode(png).decode('ascii'))
                    self._thumbnail_images[video_id] = image
                    label.configure(image=image, text='')
                except Exception:
                    pass

            suggestions = self.session.suggestions_snapshot()
            if suggestions.revision != self._last_suggestion_revision:
                self._last_suggestion_revision = suggestions.revision
                self.suggestion_list.delete(0, 'end')
                for value in suggestions.values:
                    self.suggestion_list.insert('end', value)

            search = self.session.search_snapshot()
            if search.revision != self._last_search_revision and search.phase is not SearchPhase.LOADING:
                self._last_search_revision = search.revision
                if search.phase in {SearchPhase.RESULTS, SearchPhase.EMPTY}:
                    self.session.remember_search('online', search.query, search.results)
                    if self._source_mode == 'online':
                        self._results = search.results
                        self._results_query = search.query
                        self._render_results(search.results)
                        self.status_var.set(f'{len(search.results)} résultat(s)')
                    self._set_health(True)
                elif search.phase is SearchPhase.ERROR:
                    if self._source_mode == 'online':
                        self.status_var.set(search.error or 'Recherche YouTube indisponible')
                    self._set_health(False)

            event = self.session.media_event()
            if event.revision != self._last_media_revision and event.phase in {
                MediaActionPhase.READY, MediaActionPhase.SAVED, MediaActionPhase.ERROR,
            }:
                self._last_media_revision = event.revision
                if event.phase is MediaActionPhase.READY and event.track is not None:
                    self.status_var.set('Téléchargé')
                    self._open_rename(event.track)
                elif event.phase is MediaActionPhase.SAVED and event.path is not None:
                    self.session.register_library_change()
                    self._local_tracks_snapshot = tuple(self.session.local_tracks())
                    if self._source_mode == 'local':
                        self._render_local_results(self._filtered_local_tracks(self.search_var.get()))
                    self.status_var.set(f'Ajouté : {event.path.name}')
                    self.on_library_saved(event.path)
                elif event.phase is MediaActionPhase.ERROR:
                    # A restricted/unavailable video is a media-level failure, not
                    # proof that the YouTube search backend is down. Keep the API
                    # health dot tied to SearchController health only.
                    self.status_var.set(event.error or 'Échec média')
                self.session.acknowledge_media_event(event.revision)

            if self.session.last_error:
                self.status_var.set(self.session.status)
            elif self.session.preparing is not None or self.session.current is not None:
                self.status_var.set(self.session.status)
            self._update_busy_indicators()
            self._refresh_playback_panel()
        finally:
            if not self._closed:
                self.window.after(self.POLL_MS, self._poll)

    def close(self) -> None:
        self._remember_current_search()
        self._closed = True
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
        try:
            self.window.destroy()
        except Exception:
            pass
