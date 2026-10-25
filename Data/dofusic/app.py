from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from dofusic.audio.dungeons import DungeonCatalog
from dofusic.audio.library import MusicLibrary
from dofusic.audio.player import MusicPlayer
from dofusic.audio_policy import ControllerAudioPolicyMixin
from dofusic.capture.dofus_window import DofusCapture
from dofusic.capture.pointer import screen_cursor_position
from dofusic.capture.process_presence import is_process_running
from dofusic.capture.service import CaptureService, DirectCaptureService
from dofusic.config import AppConfig, app_dir, database_path, default_music_dir
from dofusic.location.coordinates import contextual_coordinates
from dofusic.location.decision import DecisionEngine
from dofusic.location.repository import DofusRepository
from dofusic.location.resolver import LocationResolver
from dofusic.models import (
    Coordinates,
    DecisionResult,
    DecisionState,
    LocationEvidence,
    LocationRecord,
    PositionOCRResult,
    ZoneOCRResult,
)
from dofusic.ocr_dispatch import OCREventDispatchMixin
from dofusic.text import clean_text
from dofusic.vision.change_gate import OCRChangeGate
from dofusic.vision.combat import CombatObservation, CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.interaction_guard import ToolbarInteractionGuard
from dofusic.vision.layout import (
    HUDGeometry,
    HUDInputs,
    ZoneCropStabilizer,
    ZoneOverlayWidthTracker,
    extract_hud_inputs,
)
from dofusic.vision.runtime import OCRRuntimeSupervisor
from dofusic.vision.scheduler import OCRChannelScheduler
from dofusic.vision.workers import OCRWorkerClient
from dofusic.vision.zone_stability import ZoneTextContinuity


@dataclass(slots=True)
class _OCRRuntimeState:
    scheduler: OCRChannelScheduler = field(default_factory=OCRChannelScheduler)
    fatal: bool = False
    last_restart_at: float = 0.0
    ready: bool = False


@dataclass(slots=True)
class ControllerState:
    # `location` is the canonical internal place. UI uses display_theme/place.
    location: str = 'En attente'
    location_key: str = ''
    display_theme: str = 'En attente'
    place: str = '—'
    ocr_text: str = ''
    position_text: str = ''
    position_x: int | None = None
    position_y: int | None = None
    position_display: str = '—'
    confidence: float = 0.0
    music: str = 'Aucune musique'
    status: str = 'Initialisation'
    worker_status: str = 'Arrêté'
    zone_worker_status: str = 'Arrêté'
    position_worker_status: str = 'Arrêté'
    decision_state: str = ''
    window_found: bool = False
    game_detected: bool = False
    fullscreen_required: bool = False
    overlay_hwnd: int | None = None
    combat_overlay_rect: tuple[int, int, int, int] | None = None
    zone_overlay_rect: tuple[int, int, int, int] | None = None
    position_overlay_rect: tuple[int, int, int, int] | None = None
    zone_elapsed_ms: float = 0.0
    position_elapsed_ms: float = 0.0
    in_combat: bool = False
    combat_confidence: float = 0.0
    automatic_detection_unlocked: bool = False


class DofusicController(ControllerAudioPolicyMixin, OCREventDispatchMixin):
    """Production controller: two independent OCR channels, one-way audio decision."""

    def __init__(
        self,
        config: AppConfig,
        *,
        repository: DofusRepository | None = None,
        music_library: MusicLibrary | None = None,
        player=None,
        zone_worker=None,
        position_worker=None,
        capture=None,
        decision_engine: DecisionEngine | None = None,
        process_probe: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config.sanitized()
        self.repository = repository or DofusRepository(database_path())
        self.resolver = LocationResolver(self.repository)

        music_folder = Path(self.config.music_dir) if self.config.music_dir else default_music_dir()
        self.music_library = music_library or MusicLibrary(
            self.repository, music_folder, self.config.fallback_stem,
            dungeon_catalog=DungeonCatalog(app_dir() / 'dungeons.json'),
        )
        self.player = player or MusicPlayer(
            volume=self.config.volume,
            fade_ms=self.config.fade_ms,
            on_status=self._audio_status,
            spectrum_enabled=self.config.show_visualizer,
            normalize_loudness=self.config.normalize_loudness,
        )
        try:
            self.player.set_muted(self.config.mute)
        except Exception:
            pass

        self.zone_worker = zone_worker or OCRWorkerClient(channel='zone', cpu_threads=self.config.cpu_threads)
        self.position_worker = position_worker or OCRWorkerClient(channel='position', cpu_threads=1)
        # Only production/native OCR processes need this asynchronous supervisor.
        # Injected test backends retain deterministic synchronous semantics.
        self._ocr_supervisor = (
            OCRRuntimeSupervisor({'zone': self.zone_worker, 'position': self.position_worker})
            if capture is None and isinstance(self.zone_worker, OCRWorkerClient)
            and isinstance(self.position_worker, OCRWorkerClient) else None
        )
        self.hud_geometry = HUDGeometry()
        # Production capture is created and executed inside its own thread. Tests
        # may inject a deterministic backend, which is wrapped synchronously.
        if capture is None:
            self.capture = None
            self.capture_service = CaptureService(
                lambda: DofusCapture(
                    process_name=self.config.process_name,
                    geometry=self.hud_geometry,
                ),
                fps=self.config.capture_fps,
            )
        else:
            self.capture = capture
            self.capture_service = DirectCaptureService(capture)
        # Production waits for the real Dofus.exe process before starting capture,
        # OCR workers or automatic audio. Deterministic tests that inject a capture
        # backend keep the old always-present behavior unless they also inject a
        # process probe explicitly.
        if process_probe is not None:
            self._process_probe = process_probe
        elif capture is None:
            self._process_probe = is_process_running
        else:
            self._process_probe = lambda _name: True
        self._process_probe_interval_sec = 0.75
        self._last_process_probe_at = float('-inf')
        # A lightweight window watcher runs even before Dofus.exe exists.
        # It also provides a second presence signal when Toolhelp32 misses a
        # late-created game process (e.g. after a launcher handoff).
        self._capture_watch_enabled = capture is None
        self._game_process_running = False
        self._automatic_runtime_active = False
        # A positive windowed observation must be continuous. Unknown frames
        # must never count toward the windowed grace period.
        self._positive_windowed_since: float | None = None
        self._process_unavailable_since: float | None = None
        self._last_capture_sequence = 0
        self.decision = decision_engine or DecisionEngine(
            min_confidence=self.config.min_confidence,
            min_fuzzy_score=self.config.min_fuzzy_score,
            min_margin=self.config.min_margin,
            exact_single_confidence=self.config.exact_single_confidence,
            required_confirmations=self.config.required_confirmations,
            text_stability_min=self.config.text_stability_min,
        )

        self.state = ControllerState(music='Aucune musique')
        self.started = False
        self.next_confirmation_at = 0.0
        self.last_capture_seen_at = 0.0
        self.unknown_since: float | None = None
        self.current_location: LocationRecord | None = None
        # Audio resolution must never reuse the raw, potentially masked OCR
        # sample displayed during a combat. Keep its last confirmed label.
        self._confirmed_zone_raw_text = ''
        self.last_evidence: LocationEvidence | None = None
        self.last_position_at = 0.0
        self.last_zone_observed_at = float('-inf')
        self.last_reliable_position: Coordinates | None = None
        self.context_position: Coordinates | None = None
        self._accepted_position_result: PositionOCRResult | None = None
        self._zone_crop_stabilizer = ZoneCropStabilizer()
        self._zone_overlay_width = ZoneOverlayWidthTracker()
        self._pending_zone_width: tuple[object, int, int, float] | None = None
        self._zone_text_continuity = ZoneTextContinuity()
        # Per-Dofus-process startup latch: only position OCR is allowed until valid
        # map coordinates have been read once. Closing Dofus re-arms this guard so a
        # later relaunch cannot interpret the login/character menus as a game map.
        self._automatic_detection_unlocked = False
        self.zone_slot = _OCRRuntimeState()
        self.position_slot = _OCRRuntimeState()
        self.zone_change_gate = OCRChangeGate(
            change_threshold=self.config.ocr_change_threshold,
            idle_refresh_sec=self.config.zone_ocr_idle_refresh_sec,
        )
        self.position_change_gate = OCRChangeGate(
            change_threshold=self.config.ocr_change_threshold,
            idle_refresh_sec=self.config.position_ocr_idle_refresh_sec,
        )
        self.combat_tracker = CombatStateTracker(
            required_confirmations=3, initial_state=False,
            min_inferred_transition_seconds=0.40,
        )
        self._toolbar_interaction_guard = ToolbarInteractionGuard()
        # A completed combat invalidates zone OCR frames captured during the fight.
        self._zone_min_observed_at = float('-inf')
        # Manual local playback temporarily overrides automatic location music.
        # OCR and dungeon decisions continue updating the desired track.
        self._audio_override_active = False
        # Backward-compatible mirror used by a few internal tests/consumers.
        self._desired_local_track: Path | None = None
        self._last_audio_retry_at = float('-inf')


    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.state.status = 'En attente de Dofus.exe'
        if getattr(self, '_capture_watch_enabled', False):
            try:
                self.capture_service.start()
            except Exception:
                pass
        self.state.zone_worker_status = 'Arrêté'
        self.state.position_worker_status = 'Arrêté'
        self._refresh_worker_status()

    @property
    def game_process_running(self) -> bool:
        # __new__-based legacy tests/consumers have no process gate fields and keep
        # their historical always-running semantics. Production initializes them.
        return bool(getattr(self, '_game_process_running', True))

    def _start_automatic_runtime(self) -> None:
        if bool(getattr(self, '_automatic_runtime_active', False)):
            return
        self._automatic_runtime_active = True
        try:
            self.capture_service.start()
        except Exception:
            pass
        supervisor = getattr(self, '_ocr_supervisor', None)
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            slot.fatal = False
            slot.ready = False
            slot.scheduler.reset(request_refresh=True)
            slot.last_restart_at = time.monotonic()
            if supervisor is None:
                try:
                    worker.start()
                except Exception as exc:
                    slot.fatal = True
                    self.state.status = f'Impossible de démarrer OCR {channel}: {exc}'
            if channel == 'zone':
                self.state.zone_worker_status = 'Erreur OCR zone' if slot.fatal else 'Démarrage OCR zone'
            else:
                self.state.position_worker_status = 'Erreur OCR position' if slot.fatal else 'Démarrage OCR position'
        if supervisor is not None:
            self.state.status = 'Dofus détecté - initialisation OCR'
            supervisor.start()  # never calls multiprocessing.Process.start() on Tk
        self._refresh_worker_status()
        # While the coordinate latch is still locked, only the neutral
        # Musique/Musique1/... pool may play automatically.
        self._play_startup_generic(restart=False)

    def _reset_detection_context(self) -> None:
        self._automatic_detection_unlocked = False
        self.state.automatic_detection_unlocked = False
        self.next_confirmation_at = 0.0
        self.unknown_since = None
        self.current_location = None
        self._confirmed_zone_raw_text = ''
        self.last_evidence = None
        self.last_position_at = 0.0
        self.last_zone_observed_at = float('-inf')
        self.last_reliable_position = None
        self.context_position = None
        self._accepted_position_result = None
        self._zone_crop_stabilizer.reset()
        self._zone_overlay_width.reset()
        self._pending_zone_width = None
        self._zone_text_continuity.reset()
        self.state.location = 'En attente'
        self.state.location_key = ''
        self.state.place = '—'
        self.state.ocr_text = ''
        self.state.position_text = ''
        self.state.position_x = None
        self.state.position_y = None
        self.state.position_display = '—'
        self.state.confidence = 0.0
        self.state.decision_state = ''
        self.state.in_combat = False
        self.state.combat_confidence = 0.0
        self.zone_change_gate.reset()
        self.position_change_gate.reset()
        try:
            self.decision.history.clear()
            self.decision.confirmed_key = ''
        except Exception:
            pass
        self.combat_tracker.reset()
        self._toolbar_interaction_guard.reset()
        self._zone_min_observed_at = float('-inf')

    def _stop_automatic_runtime(self) -> None:
        was_active = bool(getattr(self, '_automatic_runtime_active', False))
        self._automatic_runtime_active = False
        if was_active:
            supervisor = getattr(self, '_ocr_supervisor', None)
            if supervisor is not None:
                supervisor.stop()
            components = [] if supervisor is not None else [self.zone_worker, self.position_worker]
            if not getattr(self, '_capture_watch_enabled', False):
                components.append(self.capture_service)
            for component in components:
                try:
                    component.close()
                except Exception:
                    pass
        for slot in (self.zone_slot, self.position_slot):
            slot.fatal = False
            slot.ready = False
            slot.scheduler.reset(request_refresh=True)
        self.state.zone_worker_status = 'Arrêté'
        self.state.position_worker_status = 'Arrêté'
        self.state.worker_status = 'Arrêté'
        self.state.window_found = False
        self.state.game_detected = False
        self.state.overlay_hwnd = None
        self.state.combat_overlay_rect = None
        self.state.zone_overlay_rect = None
        self.state.position_overlay_rect = None
        self.last_capture_seen_at = 0.0
        self._reset_detection_context()
        if not self.audio_override_active:
            try:
                self.player.stop(fade_ms=self.config.fade_ms)
            except Exception:
                pass
            self._desired_local_track = None
            self.state.music = 'Aucune musique'
            self.state.display_theme = 'En attente'
            self.state.status = 'Dofus.exe fermé - automatisme en pause'

    def _visible_game_frame(self, now: float) -> bool:
        """Accept a *fresh* captured Dofus window as an alternative to Toolhelp32.

        Do not reuse cached frames after the process exits. The capture backend
        validates the owning executable before producing a CapturedFrame.
        """
        if not getattr(self, '_capture_watch_enabled', False):
            return False
        try:
            snapshot = self.capture_service.latest(0, now=now)
            return bool(
                snapshot is not None and snapshot.frame is not None
                and snapshot.frame.hwnd
                and 0.0 <= now - snapshot.captured_at <= 1.5
            )
        except Exception:
            return False

    def _hide_capture_overlay(self) -> None:
        """Hide geometry immediately while keeping confirmed OCR/audio context."""
        self.state.window_found = False
        self.state.overlay_hwnd = None
        self.state.combat_overlay_rect = None
        self.state.zone_overlay_rect = None
        self.state.position_overlay_rect = None

    def _capture_unavailable(self, now: float, *, mode: str) -> bool:
        """Debounce missing/uncertain fullscreen readings before resetting OCR.

        Win32 and MSS can report an unknown mode for a single captured frame.
        That is not evidence that the game left fullscreen. A sustained,
        *positive* windowed reading has a shorter grace period than errors.
        """
        self._hide_capture_overlay()
        self.state.fullscreen_required = mode == 'windowed'
        if mode == 'windowed':
            self.state.status = 'Passez Dofus en plein écran pour activer la détection'
        else:
            self.state.status = 'Capture Dofus temporairement indisponible'

        # An unknown mode is not a positive windowed observation. Keep the
        # OCR session, confirmed coordinates and currently playing track while
        # the capture thread repairs its backend; otherwise a long Win32
        # failure clears the startup latch and switches to generic music until
        # the user manually toggles fullscreen.
        # Only truly consecutive 'windowed' samples may stop the
        # OCR runtime. Without this separate timer, 10 seconds of 'unknown'
        # followed by one spurious 'windowed' probe immediately resets the
        # confirmed location, music and OCR latch.
        windowed_since = getattr(self, '_positive_windowed_since', None)
        if mode == 'windowed':
            if windowed_since is None:
                windowed_since = now
                self._positive_windowed_since = now
            windowed_elapsed = max(0.0, now - windowed_since)
        else:
            self._positive_windowed_since = None
            windowed_elapsed = 0.0
        if (mode == 'windowed'
                and bool(getattr(self, '_automatic_runtime_active', False))
                and windowed_elapsed >= 0.65):
            self._stop_automatic_runtime()
        # stop() clears these fields for a truly closed game. The process is
        # present here, merely out of fullscreen or temporarily unreadable.
        self.state.game_detected = True
        self.state.fullscreen_required = mode == 'windowed'
        self.state.status = (
            'Passez Dofus en plein écran pour activer la détection'
            if mode == 'windowed' else 'Capture Dofus temporairement indisponible'
        )
        return False

    def _sync_game_process(self, now: float) -> bool:
        probe = getattr(self, '_process_probe', None)
        if not callable(probe):
            return True
        interval = max(0.05, float(getattr(self, '_process_probe_interval_sec', 0.75)))
        last_probe = float(getattr(self, '_last_process_probe_at', float('-inf')))
        # Window capture continues at low cost while the process is absent.
        # A verified live game frame can unlock startup independently of the
        # process snapshot, including when the launcher creates Dofus later.
        visible = self._visible_game_frame(now)
        probe_executed = now - last_probe >= interval
        if not probe_executed:
            running = bool(getattr(self, '_last_process_result', False)) or visible
        else:
            self._last_process_probe_at = float(now)
            try:
                self._last_process_result = bool(probe(self.config.process_name))
            except Exception:
                self._last_process_result = False
            running = self._last_process_result or visible
        # ``queried`` denotes an actual Toolhelp32 enumeration on THIS tick;
        # ``process_present`` is the cached result. Keeping both makes it
        # possible to diagnose brief Alt+Tab / process-snapshot misses.
        previous = self.game_process_running
        if (getattr(self, '_capture_watch_enabled', False)
                and isinstance(self.capture_service, CaptureService)
                and not self.capture_service.alive):
            # The capture watcher is required to detect returning fullscreen.
            # Restore it even if its thread exited unexpectedly.
            self.capture_service.start()
        # The process snapshot and the window watcher are independent. Either
        # can briefly be unavailable under Win32. Avoid resetting the startup
        # position latch and replacing a confirmed track on one missed check.
        if not running:
            if bool(getattr(self, '_automatic_runtime_active', False)):
                first = getattr(self, '_process_unavailable_since', None)
                if first is None:
                    first = now
                    self._process_unavailable_since = first
                self._hide_capture_overlay()
                if now - first < 2.5:
                    self.state.status = 'Vérification temporaire du processus Dofus'
                    return False
            if previous or bool(getattr(self, '_automatic_runtime_active', False)):
                self._stop_automatic_runtime()
            self._game_process_running = False
            self.state.game_detected = False
            self._positive_windowed_since = None
            return False

        self._process_unavailable_since = None
        self._game_process_running = True
        self.state.game_detected = True
        # A process existing is not proof of fullscreen. A fresh *positive*
        # fullscreen window snapshot is required before starting recognition.
        if (getattr(self, '_capture_watch_enabled', False)
                and isinstance(self.capture_service, CaptureService)):
            snapshot = self.capture_service.latest(0, now=now)
            mode = 'unknown'
            if (snapshot is not None
                    and 0.0 <= now - snapshot.captured_at <= 1.5):
                mode = snapshot.window_mode
            if mode != 'fullscreen':
                return self._capture_unavailable(now, mode=mode)

        self._positive_windowed_since = None
        self.state.fullscreen_required = False
        if not previous or not bool(getattr(self, '_automatic_runtime_active', False)):
            self._start_automatic_runtime()
        return True

    def restart_worker(self) -> None:
        if bool(getattr(self.state, 'fullscreen_required', False)):
            self.state.status = 'Passez Dofus en plein écran pour activer la détection'
            return
        if not self.game_process_running:
            self.state.status = 'En attente de Dofus.exe'
            return
        now = time.monotonic()
        self.zone_change_gate.reset()
        self.position_change_gate.reset()
        supervisor = getattr(self, '_ocr_supervisor', None)
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            slot.fatal = False
            slot.ready = False
            slot.scheduler.reset(request_refresh=True)
            slot.last_restart_at = now
            try:
                if supervisor is not None:
                    supervisor.restart(channel)
                else:
                    worker.restart()
            except Exception:
                slot.fatal = True
        self._refresh_worker_status()


    @staticmethod
    def _position_display(coordinates: Coordinates | None) -> str:
        return '—' if coordinates is None else f'X={coordinates.x} | Y={coordinates.y}'

    def _set_position(self, raw_text: str, coordinates: Coordinates | None) -> None:
        self.state.position_text = raw_text
        self.state.position_x = coordinates.x if coordinates else None
        self.state.position_y = coordinates.y if coordinates else None
        self.state.position_display = self._position_display(coordinates)


    def _stabilize_zone_capture(self, hud: HUDInputs, frame) -> HUDInputs:
        """One shared ROI for recognition and its on-screen frame."""
        stabilizer = getattr(self, '_zone_crop_stabilizer', None)
        if stabilizer is None or hud.zone.size == 0:
            return hud
        image = frame.image
        x, y = hud.transform.point(self.hud_geometry.zone_x, self.hud_geometry.zone_y)
        x = max(0, min(image.shape[1] - 1, x))
        y = max(0, min(image.shape[0] - 1, y))
        h = hud.zone.shape[0]
        max_width = image.shape[1] - x
        width = stabilizer.observe(
            hud.zone.shape[1], scale=hud.transform.scale,
            window_id=int(frame.hwnd), maximum=max_width,
        )
        if width == hud.zone.shape[1]:
            return hud
        return HUDInputs(
            zone=np.ascontiguousarray(image[y:y + h, x:x + width]),
            position=hud.position, combat=hud.combat, transform=hud.transform,
            toolbar_width=hud.toolbar_width,
        )


    def _toolbar_pointer_interference(self, frame, hud, *, now: float) -> bool:
        """Ignore transient UI states while the cursor can affect HUD buttons.

        The footprint is computed from the existing resolution transform. A
        genuine combat change will still be recognized once interaction ends.
        """
        guard = getattr(self, '_toolbar_interaction_guard', None)
        if guard is None:
            guard = self._toolbar_interaction_guard = ToolbarInteractionGuard()
        pointer = screen_cursor_position()
        transform = getattr(hud, 'transform', None)
        rect = getattr(frame, 'screen_rect', None)
        if pointer is not None and transform is not None and rect is not None:
            x, y = transform.point(self.hud_geometry.combat_x, self.hud_geometry.combat_y)
            width, height = transform.size(self.hud_geometry.combat_width,
                                           self.hud_geometry.combat_height)
            # Screen origin, not client origin: GetCursorPos uses screen pixels.
            bounds = (rect[0] + x, rect[1] + y, width, height)
        else:
            bounds = (0, 0, 0, 0)
        disturbed = guard.observe(cursor=pointer, bounds=bounds, now=now)
        return disturbed

    def _update_combat_from_toolbar(
        self, toolbar: np.ndarray | None, *, disturbed: bool = False,
        now: float | None = None,
    ) -> None:
        # One recognition result serves the overlay and audio decisions.
        # Dungeon music always retains priority over generic combat music.
        if disturbed:
            # Do not even classify a hovered/animating toolbar. This protects
            # the visual overlay and the audio from the same transient input.
            self.combat_tracker.update(CombatObservation(None, 0.),
                                       disturbed=True)
            return
        observation = analyze_combat_toolbar(toolbar, self.hud_geometry)
        self.state.combat_confidence = float(observation.confidence)
        transition = self.combat_tracker.update(observation, now=now)
        if transition is None:
            return

        self.state.in_combat = bool(transition)
        in_dungeon = self._is_dungeon_context()

        if not transition:
            # A fight can hide the zone and coordinates. Resume both OCR channels
            # on their next fresh frames (the combat HUD is still watched every
            # capture frame), without retaining their old visual signatures.
            position_gate = getattr(self, 'position_change_gate', None)
            if position_gate is not None:
                position_gate.reset()
            # Restore the last genuine zone label before audio resolution and
            # before the next fresh post-combat OCR has completed.
            self.state.ocr_text = (
                getattr(self, '_confirmed_zone_raw_text', '')
                or (self.current_location.name if self.current_location else '')
            )
            # In-flight OCR captured during the fight must not become a new
            # location immediately after combat. The next fresh frame will
            # be scanned even when the HUD image has not changed.
            self._zone_min_observed_at = time.monotonic()
            gate = getattr(self, 'zone_change_gate', None)
            if gate is not None:
                gate.reset()
        if in_dungeon:
            # The dungeon already owns the soundtrack. Continue recognizing
            # and displaying real combat, but never restart the dungeon music.
            return

        # Continue tracking the desired soundtrack while manual local playback
        # temporarily owns the audio channel.
        if self.current_location is not None:
            # Entering combat starts a fresh generic-combat cycle. Leaving
            # combat intentionally does not reroll exploration: it restores the
            # exploration choice that was active before combat.
            self._play_location(
                self.current_location,
                getattr(self, '_confirmed_zone_raw_text', '') or self.current_location.name,
                new_generic_cycle=bool(self.state.in_combat),
            )
            return
        track = self.music_library.resolve(
            None,
            combat=self.state.in_combat,
            new_generic_cycle=bool(self.state.in_combat),
        )
        status = 'Combat détecté' if self.state.in_combat else 'Fin du combat'
        self._play_track(track, status=status)

    def _mark_unknown(self, now: float, reason: str) -> None:
        self.state.decision_state = DecisionState.UNKNOWN.value
        self.state.status = 'Zone non reconnue'
        # An unchanged, unknown (or unreadable) label does not need accelerated
        # confirmations: OCRChangeGate retries on a real image change and at
        # its normal idle interval. Only PENDING/AMBIGUOUS need fast retries.
        self.next_confirmation_at = 0.0

        # A confirmed context is positive evidence. A single unreadable/unknown HUD
        # must never erase it or force generic audio; wait for a positive new location.
        if self.current_location is not None:
            self.unknown_since = None
            self.state.location = self.current_location.name
            if not self.state.place or self.state.place == '—':
                self.state.place = self.current_location.name
            self.state.status = 'Contexte précédent conservé'
            return

        self.state.location_key = ''
        if self.unknown_since is None:
            self.unknown_since = now
        if now - self.unknown_since >= self.config.fallback_unknown_delay_sec:
            fallback = self.music_library.resolve(None, combat=self.state.in_combat)
            if fallback is not None:
                label = 'musique de combat générique' if self.state.in_combat else 'musique générique'
                self._play_track(fallback, status=f'Aucun lieu fiable : {label}')

    def handle_position_result(
        self,
        result: PositionOCRResult,
        *,
        now: float | None = None,
        observed_at: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else float(now)
        observation_time = now if observed_at is None else float(observed_at)
        # OCR is asynchronous: never allow an older screenshot to overwrite
        # coordinates confirmed by a more recent screenshot.
        if observation_time < self.last_position_at:
            return
        reference = self.context_position if self.current_location is not None else self.last_reliable_position
        coordinates = contextual_coordinates(
            result.raw_text,
            result.coordinates,
            reference,
            tolerance=self.config.context_position_tolerance,
            known_coordinate=lambda c: bool(self.repository.coordinate_candidates(c, tolerance=0)),
        )
        self.state.position_elapsed_ms = float(result.elapsed_ms)
        if coordinates is not None:
            self._set_position(result.raw_text, coordinates)
        else:
            # OCR can be temporarily unreadable during movement. Keep the last
            # verified coordinates on screen for the bounded context window;
            # never turn an invalid OCR sample into a new confirmed position.
            max_age = float(getattr(self.config, "position_context_max_age_sec", 4.0))
            if (self.last_reliable_position is not None
                    and 0.0 <= observation_time - self.last_position_at <= max_age):
                self.state.position_text = result.raw_text
            else:
                self._set_position(result.raw_text, None)
        if coordinates is not None:
            self._accepted_position_result = PositionOCRResult(
                result.raw_text, coordinates.x, coordinates.y, result.confidence,
                result.elapsed_ms, result.engine, result.variant, result.error,
            )
            self.last_position_at = observation_time
            self.last_reliable_position = coordinates
            if not self.automatic_detection_unlocked:
                self._automatic_detection_unlocked = True
                self.state.automatic_detection_unlocked = True

    def handle_zone_result(
        self,
        result: ZoneOCRResult,
        *,
        now: float | None = None,
        observed_at: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else float(now)
        observation_time = now if observed_at is None else float(observed_at)
        # An OCR error or truncated older image is no more entitled to change
        # the active context than a successful but obsolete OCR result.
        if observation_time < getattr(self, 'last_zone_observed_at', float('-inf')):
            return
        # A queued result produced before the last combat ended is obsolete
        # for audio/location decisions, even if consumed after that transition.
        if observation_time < getattr(self, '_zone_min_observed_at', float('-inf')):
            return
        self.last_zone_observed_at = observation_time
        self.state.zone_elapsed_ms = float(result.elapsed_ms)
        if bool(self.state.in_combat):
            # Never let a masked/false HUD name (including a false dungeon)
            # change the confirmed location, DecisionEngine history or music.
            # Preserve the raw OCR observation for display without confirming a location.
            self.state.ocr_text = result.text
            self.state.confidence = float(result.confidence) if not result.error else 0.0
            self.state.decision_state = DecisionState.UNKNOWN.value
            return
        if result.error:
            self._mark_unknown(now, f'OCR zone: {result.error}')
            return
        if not result.text.strip():
            if self.current_location is None:
                self.state.ocr_text = ''
            self.state.confidence = 0.0
            self._mark_unknown(now, 'Aucun nom de zone lisible')
            return

        # Reject one-off truncated OCR when a fuller confirmed name is known.
        # Two coherent observations can still confirm a real move to a shorter
        # zone; a single frame must not silently change the music.
        continuity = getattr(self, '_zone_text_continuity', None)
        if continuity is not None and continuity.hold(result.text):
            self.state.status = 'Confirmation du nom de zone incomplet'
            self.next_confirmation_at = now + self.config.pending_confirmation_interval_sec
            return

        coordinates = None
        if (
            self.state.position_x is not None
            and self.state.position_y is not None
            and self.last_position_at > 0.0
            and 0.0 <= observation_time - self.last_position_at <= self.config.position_context_max_age_sec
        ):
            coordinates = Coordinates(self.state.position_x, self.state.position_y)

        # Zone is primary and never waits for the position worker.
        evidence = self.resolver.resolve(result.text, result.confidence, coordinates, timestamp=observation_time)
        self.last_evidence = evidence
        decision = self.decision.evaluate(evidence)


        self.state.ocr_text = result.text
        self.state.confidence = result.confidence
        self.state.decision_state = decision.state.value

        if decision.state is DecisionState.CONFIRMED and decision.location is not None:
            self._accept_confirmed_zone(result, decision, evidence, coordinates, continuity)
            return
        if decision.state in (DecisionState.PENDING, DecisionState.AMBIGUOUS):
            self._defer_zone(result, decision, now)
            return
        self._mark_unknown(now, decision.reason)

    def _accept_confirmed_zone(
        self, result: ZoneOCRResult, decision: DecisionResult,
        evidence: LocationEvidence, coordinates: Coordinates | None,
        continuity: ZoneTextContinuity | None,
    ) -> None:
        """Only positively confirmed places can request a new soundtrack."""
        self.unknown_since = None
        self.next_confirmation_at = 0.0
        previous_location = self.current_location
        same_place = (previous_location is not None and
                      previous_location.canonical_key == decision.location.canonical_key)
        if continuity is not None and same_place:
            original_label = continuity.confirmed
            if original_label and original_label.casefold().startswith(clean_text(result.text).casefold()):
                if len(clean_text(result.text)) < len(original_label):
                    self.state.ocr_text = original_label
        self.current_location = decision.location
        self._confirmed_zone_raw_text = self.state.ocr_text
        if continuity is not None:
            continuity.confirm(result.text, same_place=same_place)
        self.state.location = decision.location.name
        self.state.location_key = decision.location.canonical_key
        if coordinates is not None:
            self.context_position = coordinates
        elif previous_location is None or previous_location.canonical_key != decision.location.canonical_key:
            self.context_position = None
        location_changed = (
            previous_location is None
            or previous_location.canonical_key != decision.location.canonical_key
        )
        if location_changed:
            self._play_location(
                decision.location,
                result.text,
                # A new exploration context gets a fresh generic draw. Combat
                # has its own cycle, started only on the combat transition, so
                # OCR/location refreshes during a fight cannot shuffle music.
                new_generic_cycle=bool(not self.state.in_combat),
            )
        else:
            # Re-confirming the same place updates confidence/context only.
            # It must not resolve the library or touch the audio pipeline again.
            self.state.place = decision.location.name

    def _defer_zone(self, result: ZoneOCRResult, decision: DecisionResult, now: float) -> None:
        """Pending/ambiguous places must not replace confirmed music."""
        # Keep the last confirmed audio/context while the new textual candidate
        # is being confirmed, but never promote the old place to a fresh
        # confirmation merely because coordinates are nearby.
        self.unknown_since = None
        self.next_confirmation_at = now + self.config.pending_confirmation_interval_sec
        if self.current_location is not None:
            self.state.location = self.current_location.name
            self.state.location_key = self.current_location.canonical_key
        elif decision.location is not None:
            self.state.location = decision.location.name
        self.state.place = clean_text(result.text) or self.state.place
        self.state.status = 'Vérification de la zone'


    def _submit_channel(
        self,
        worker,
        slot: _OCRRuntimeState,
        image: np.ndarray,
        now: float,
        captured_at: float,
    ) -> bool:
        if slot.fatal or slot.scheduler.active_request_id is not None:
            return False
        try:
            request_id = worker.submit(image, captured_at=captured_at)
        except Exception:
            return False
        if request_id is None:
            return False
        slot.scheduler.submitted(request_id=request_id, now=now)
        return True

    def tick(self, *, now: float | None = None) -> ControllerState:
        now = time.monotonic() if now is None else float(now)
        if not self.started:
            self.start()
        audio_tick = getattr(getattr(self, 'player', None), 'tick', None)
        if callable(audio_tick):
            audio_tick(now=now)
        if not self._sync_game_process(now):
            return self.state
        self._poll_workers(now)
        self._retry_desired_audio(now)

        snapshot = self.capture_service.latest(self._last_capture_sequence, now=now)
        if snapshot is None:
            if (
                self.last_capture_seen_at > 0.0
                and now - self.last_capture_seen_at >= self.config.capture_missing_grace_sec
            ):
                self._update_combat_from_toolbar(None)
                self.state.window_found = False
                self.state.overlay_hwnd = None
                self.state.combat_overlay_rect = None
                self.state.zone_overlay_rect = None
                self.state.position_overlay_rect = None
            return self.state
        self._last_capture_sequence = snapshot.sequence
        frame = snapshot.frame
        captured_at = float(snapshot.captured_at)

        if frame is None:
            # A missing capture interrupts consecutive visual confirmation,
            # while leaving the last confirmed combat/audio state untouched.
            self._update_combat_from_toolbar(None)
            # Overlay geometry is never kept through the capture grace period: if
            # Dofus is covered/minimized, no Dofusic frame may float over another app.
            self.state.overlay_hwnd = None
            self.state.combat_overlay_rect = None
            self.state.zone_overlay_rect = None
            self.state.position_overlay_rect = None
            self.state.window_found = (
                self.last_capture_seen_at > 0.0
                and now - self.last_capture_seen_at < self.config.capture_missing_grace_sec
            )
            return self.state

        self.last_capture_seen_at = captured_at
        self.state.window_found = True
        # The capture thread already calibrated this very frame. Reusing its
        # transform avoids a second image-wide HUD geometry estimate per tick.
        hud = extract_hud_inputs(
            frame.image, self.hud_geometry,
            transform=getattr(frame, 'hud_transform', None),
        )
        hud = self._stabilize_zone_capture(hud, frame)
        if self.automatic_detection_unlocked:
            disturbed = self._toolbar_pointer_interference(frame, hud, now=now)
            self._update_combat_from_toolbar(hud.combat, disturbed=disturbed, now=now)

        from dofusic.vision.overlay_projection import project_overlays
        self._current_hud_window_id = int(frame.hwnd)
        if not hasattr(self, '_zone_overlay_width'):
            # Lightweight injected controllers used by tests may bypass __init__.
            self._zone_overlay_width = ZoneOverlayWidthTracker()
        zone_display_width = None
        if frame.source == 'visible':
            zone_display_width = self._zone_overlay_width.observe(
                int(hud.zone.shape[1]), window_id=int(frame.hwnd),
                scale=float(getattr(getattr(hud, 'transform', None), 'scale', 1.0)),
            )
        overlay = project_overlays(
            source=frame.source, hwnd=frame.hwnd, screen_rect=frame.screen_rect,
            image_shape=frame.image.shape, hud=hud, geometry=self.hud_geometry,
            toolbar_buttons=getattr(getattr(self, 'combat_tracker', None), 'toolbar_buttons', None),
            position_result=getattr(self, '_accepted_position_result', None),
            zone_display_width=zone_display_width,
        )
        self.state.overlay_hwnd = overlay.hwnd
        self.state.combat_overlay_rect = overlay.combat
        self.state.zone_overlay_rect = overlay.zone
        self.state.position_overlay_rect = overlay.position

        self._schedule_ocr_for_hud(hud, now=now, captured_at=captured_at, source=frame.source)
        return self.state

    def _schedule_ocr_for_hud(
        self, hud: HUDInputs, *, now: float, captured_at: float, source: str,
    ) -> None:
        """Single-flight OCR scheduling, independent of overlay publication."""
        # The last confirmed location is the immutable musical context of a
        # fight. Zone/position OCR cannot affect its soundtrack while combat is
        # confirmed; continuing those ONNX scans wastes CPU and may introduce
        # misleading observations from spell overlays. Combat recognition itself
        # continues in tick() on EVERY captured toolbar frame. On combat exit
        # _update_combat_from_toolbar resets both gates to force fresh OCR.
        if bool(getattr(self.state, 'in_combat', False)):
            return
        zone_confirmation_due = bool(self.next_confirmation_at and now >= self.next_confirmation_at)
        zone_force = zone_confirmation_due
        if (
            self.automatic_detection_unlocked
            and getattr(self.zone_worker, 'alive', False)
            and not self.zone_slot.fatal
        ):
            zone_changed = self.zone_change_gate.should_scan(hud.zone, now=now, force=zone_force)
            if zone_changed and self.zone_slot.scheduler.should_submit(
                now=now,
                min_interval=self.config.zone_ocr_min_interval_sec,
                # Force the visual gate for pending confirmations, not the
                # single-flight rate limiter: even ambiguous OCR must obey
                # the configured minimum interval between ONNX submissions.
                force=False,
            ):
                if self._submit_channel(self.zone_worker, self.zone_slot, hud.zone, now, captured_at):
                    self._pending_zone_width = (
                        self.zone_slot.scheduler.active_request_id,
                        int(hud.zone.shape[1]), int(getattr(self, '_current_hud_window_id', 0)),
                        float(getattr(getattr(hud, 'transform', None), 'scale', 1.0)),
                    ) if source == 'visible' else None
                    self.zone_change_gate.mark_scanned(now=now)
                    self.state.status = f'OCR zone en cours ({source})'

        if getattr(self.position_worker, 'alive', False) and not self.position_slot.fatal:
            position_force = self.last_reliable_position is None
            position_changed = self.position_change_gate.should_scan(hud.position, now=now, force=position_force)
            if position_changed and self.position_slot.scheduler.should_submit(
                now=now,
                min_interval=self.config.position_ocr_min_interval_sec,
            ):
                if self._submit_channel(self.position_worker, self.position_slot, hud.position, now, captured_at):
                    self.position_change_gate.mark_scanned(now=now)

    def close_async(self) -> threading.Thread | None:
        """Release Tk-owned audio now, close capture/child processes off the UI.

        Called only after the music UI has released its session. The returned
        capture cleanup thread can be joined *after* destroying the Tk window.
        """
        supervisor = getattr(self, '_ocr_supervisor', None)
        if supervisor is None:
            self.close()
            return None
        self.started = False
        supervisor.shutdown()
        try:
            self.player.close()  # pygame mixer belongs to the Tk/main thread
        except Exception:
            pass
        capture_close = threading.Thread(
            target=self._close_capture_safely,
            name='DofusicCaptureShutdown', daemon=False,
        )
        capture_close.start()
        return capture_close

    def _close_capture_safely(self) -> None:
        try:
            self.capture_service.close()
        except Exception:
            pass

    def close(self) -> None:
        supervisor = getattr(self, '_ocr_supervisor', None)
        if supervisor is not None:
            supervisor.shutdown()
            components = (self.capture_service, self.player)
        else:
            components = (self.zone_worker, self.position_worker, self.capture_service, self.player)
        for component in components:
            try:
                component.close()
            except Exception:
                pass
