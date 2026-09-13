from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from dofusic.audio.library import MusicLibrary
from dofusic.audio.dungeons import DungeonCatalog
from dofusic.audio.player import MusicPlayer
from dofusic.capture.dofus_window import DofusCapture
from dofusic.capture.process_presence import is_process_running
from dofusic.capture.service import CaptureService, DirectCaptureService
from dofusic.config import AppConfig, app_dir, database_path, default_music_dir
from dofusic.location.coordinates import contextual_coordinates
from dofusic.location.decision import DecisionEngine
from dofusic.location.repository import DofusRepository
from dofusic.location.resolver import LocationResolver
from dofusic.models import Coordinates, DecisionState, LocationEvidence, LocationRecord, PositionOCRResult, ZoneOCRResult
from dofusic.text import clean_text
from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDGeometry, extract_hud_inputs, tracking_screen_rects, zone_crop_has_text
from dofusic.vision.position_overlay import coordinate_overlay_width
from dofusic.vision.workers import OCREventKind, OCRWorkerClient
from dofusic.vision.scheduler import OCRChannelScheduler
from dofusic.vision.change_gate import OCRChangeGate


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
    debug_text: str = ''
    window_found: bool = False
    overlay_hwnd: int | None = None
    combat_overlay_rect: tuple[int, int, int, int] | None = None
    zone_overlay_rect: tuple[int, int, int, int] | None = None
    position_overlay_rect: tuple[int, int, int, int] | None = None
    zone_elapsed_ms: float = 0.0
    position_elapsed_ms: float = 0.0
    audio_levels: tuple[float, ...] = ()
    in_combat: bool = False
    combat_confidence: float = 0.0
    automatic_detection_unlocked: bool = False


class DofusicController:
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
        logger: logging.Logger | None = None,
        process_probe: Callable[[str], bool] | None = None,
    ) -> None:
        self.config = config.sanitized()
        self.logger = logger or logging.getLogger('dofusic')
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
        self._game_process_running = False
        self._automatic_runtime_active = False
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
        self.last_evidence: LocationEvidence | None = None
        self.last_position_at = 0.0
        self.last_reliable_position: Coordinates | None = None
        self.context_position: Coordinates | None = None
        self._accepted_position_result: PositionOCRResult | None = None
        self._zone_overlay_width_px: int | None = None
        self._zone_overlay_refresh_requested = False
        self._zone_overlay_text_key = ''
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
        self.combat_tracker = CombatStateTracker(required_confirmations=3, initial_state=False)
        # Online music is a temporary audio override only. OCR/location continue
        # normally and keep this desired local track up to date underneath it.
        self._audio_override_active = False
        self._audio_override_source: str | None = None
        # Backward-compatible mirror used by a few internal tests/consumers.
        self._desired_local_track: Path | None = None

    def _audio_status(self, message: str) -> None:
        self.state.status = message
        self.logger.warning(message)

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.state.status = 'En attente de Dofus.exe'
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
            self.logger.exception('Démarrage capture impossible')
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            slot.fatal = False
            slot.ready = False
            slot.scheduler.reset(request_refresh=True)
            try:
                worker.start()
                if channel == 'zone':
                    self.state.zone_worker_status = 'Démarrage OCR zone'
                else:
                    self.state.position_worker_status = 'Démarrage OCR position'
            except Exception as exc:
                slot.fatal = True
                self.logger.exception('Démarrage worker %s impossible', channel)
                if channel == 'zone':
                    self.state.zone_worker_status = 'Erreur OCR zone'
                    self.state.status = f'Impossible de démarrer OCR zone: {exc}'
                else:
                    self.state.position_worker_status = 'Erreur OCR position'
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
        self.last_evidence = None
        self.last_position_at = 0.0
        self.last_reliable_position = None
        self.context_position = None
        self._accepted_position_result = None
        self._zone_overlay_width_px = None
        self._zone_overlay_refresh_requested = False
        self._zone_overlay_text_key = ''
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
        self.combat_tracker = CombatStateTracker(required_confirmations=3, initial_state=False)

    def _stop_automatic_runtime(self) -> None:
        was_active = bool(getattr(self, '_automatic_runtime_active', False))
        self._automatic_runtime_active = False
        if was_active:
            for component in (self.zone_worker, self.position_worker, self.capture_service):
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

    def _sync_game_process(self, now: float) -> bool:
        probe = getattr(self, '_process_probe', None)
        if not callable(probe):
            return True
        interval = max(0.05, float(getattr(self, '_process_probe_interval_sec', 0.75)))
        last_probe = float(getattr(self, '_last_process_probe_at', float('-inf')))
        if now - last_probe < interval:
            return self.game_process_running
        self._last_process_probe_at = float(now)
        try:
            running = bool(probe(self.config.process_name))
        except Exception:
            running = False
            self.logger.exception('Vérification du processus Dofus impossible')
        previous = self.game_process_running
        self._game_process_running = running
        if running and (not previous or not bool(getattr(self, '_automatic_runtime_active', False))):
            self._start_automatic_runtime()
        elif not running and (previous or bool(getattr(self, '_automatic_runtime_active', False))):
            self._stop_automatic_runtime()
        return running

    def restart_worker(self) -> None:
        if not self.game_process_running:
            self.state.status = 'En attente de Dofus.exe'
            return
        now = time.monotonic()
        self.zone_change_gate.reset()
        self.position_change_gate.reset()
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            slot.fatal = False
            slot.ready = False
            slot.scheduler.reset(request_refresh=True)
            slot.last_restart_at = now
            try:
                worker.restart()
            except Exception:
                slot.fatal = True
                self.logger.exception('Redémarrage worker %s impossible', channel)
        self._refresh_worker_status()

    def _refresh_worker_status(self) -> None:
        if self.zone_slot.fatal and self.position_slot.fatal:
            self.state.worker_status = 'Erreur OCR zone + position'
        elif self.zone_slot.fatal:
            self.state.worker_status = 'Erreur OCR zone'
        elif self.position_slot.fatal:
            self.state.worker_status = 'Erreur OCR position'
        elif self.zone_slot.ready and self.position_slot.ready:
            self.state.worker_status = 'OCR prêt'
        elif self.zone_slot.ready:
            self.state.worker_status = 'Zone prête / position en cours'
        elif self.position_slot.ready:
            self.state.worker_status = 'Position prête / zone en cours'
        else:
            self.state.worker_status = f'{self.state.zone_worker_status} | {self.state.position_worker_status}'

    def set_volume(self, value: float) -> int:
        volume = int(self.player.set_volume(value))
        self.config.volume = volume
        return volume

    def set_muted(self, muted: bool) -> None:
        self.player.set_muted(muted)
        self.config.mute = bool(muted)

    @staticmethod
    def _position_display(coordinates: Coordinates | None) -> str:
        return '—' if coordinates is None else f'X={coordinates.x} | Y={coordinates.y}'

    @staticmethod
    def _position_overlay_screen_rect(
        *,
        full_rect: tuple[int, int, int, int],
        result: PositionOCRResult | None,
        roi_height: int,
        roi_width: int,
    ) -> tuple[int, int, int, int] | None:
        if result is None or result.coordinates is None or roi_height <= 0 or roi_width <= 0:
            return None
        left, top, full_width, full_height = (int(v) for v in full_rect)
        roi_overlay_width = coordinate_overlay_width(result, roi_height)
        if roi_overlay_width <= 0:
            return None
        width = int(round(float(full_width) * min(roi_width, roi_overlay_width) / float(roi_width)))
        return left, top, max(1, min(full_width, width)), full_height

    def _set_position(self, raw_text: str, coordinates: Coordinates | None) -> None:
        self.state.position_text = raw_text
        self.state.position_x = coordinates.x if coordinates else None
        self.state.position_y = coordinates.y if coordinates else None
        self.state.position_display = self._position_display(coordinates)

    def _stable_zone_overlay_rect(
        self,
        candidate: tuple[int, int, int, int],
        *,
        text_present: bool,
    ) -> tuple[int, int, int, int]:
        """Keep zone-outline width stable until OCR confirms a new text label.

        Dynamic pixel trimming may temporarily see transition scenery as glyphs and
        produce a very wide crop.  The displayed outline therefore accepts a new
        width only after zone OCR has produced a non-empty *changed* label. X/Y and
        height still follow the live Dofus window every frame.
        """
        x, y, candidate_width, height = (int(v) for v in candidate)
        stable_width = getattr(self, '_zone_overlay_width_px', None)
        if stable_width is None:
            # Before the first OCR-confirmed zone, use the calibrated empty-preview
            # width rather than trusting transition pixels across the whole client.
            preview_width = max(32, int(round(max(1, height) * (96.0 / 34.0))))
            stable_width = max(1, min(candidate_width, preview_width))
            self._zone_overlay_width_px = stable_width

        refresh = bool(getattr(self, '_zone_overlay_refresh_requested', False))
        if refresh and text_present:
            stable_width = max(1, candidate_width)
            self._zone_overlay_width_px = stable_width
            self._zone_overlay_refresh_requested = False

        return x, y, max(1, int(stable_width)), max(1, height)

    def _play_startup_generic(self, *, restart: bool) -> bool:
        if not self.game_process_running:
            self.state.status = 'En attente de Dofus.exe'
            return False
        if self.automatic_detection_unlocked or self.audio_override_active:
            return False
        try:
            track = self.music_library.resolve(None, combat=False)
        except Exception:
            track = None
            self.logger.exception('Résolution musique générique de démarrage impossible')
        if track is None:
            self._desired_local_track = None
            self.state.music = 'Aucune musique'
            self.state.status = 'En attente de la position Dofus'
            return False

        target = Path(track)
        self._desired_local_track = target
        try:
            changed = bool(self.player.play(target, loop=True, restart=bool(restart)))
        except Exception:
            changed = False
            self.logger.exception('Lecture musique générique de démarrage impossible: %s', target)
        if changed or getattr(self.player, 'current', None) == target:
            self.state.music = target.stem
            self.state.display_theme = target.stem
            self.state.status = 'Musique générique - attente position Dofus'
            return True
        self.state.status = 'Impossible de lire la musique générique'
        return False

    def _play_track(self, track: Path | None, *, status: str) -> None:
        if track is None:
            self._desired_local_track = None
            self.state.status = 'Aucune musique associée'
            return
        self._desired_local_track = Path(track)
        if not self.game_process_running:
            self.state.status = 'En attente de Dofus.exe'
            return
        if not self.automatic_detection_unlocked:
            self.state.status = 'En attente de la position Dofus'
            return
        if self.audio_override_active:
            # Do not interrupt a manual/online override, but remember what OCR wants now.
            self.state.status = status
            return
        try:
            changed = self.player.play(track)
        except Exception:
            changed = False
            self.logger.exception('Lecture audio impossible: %s', track)
        if changed or getattr(self.player, 'current', None) == track:
            self.state.music = track.stem
            self.state.display_theme = track.stem
        self.state.status = status

    @property
    def audio_override_active(self) -> bool:
        return bool(getattr(self, '_audio_override_active', False))

    @property
    def audio_override_source(self) -> str | None:
        if not getattr(self, '_audio_override_active', False):
            return None
        return getattr(self, '_audio_override_source', None)

    @property
    def online_audio_active(self) -> bool:
        return self.audio_override_source == 'online'

    @property
    def automatic_detection_unlocked(self) -> bool:
        # Default True keeps lightweight __new__-based internal consumers backward
        # compatible; production instances always initialize the latch explicitly.
        return bool(getattr(self, '_automatic_detection_unlocked', True))

    def _play_audio_override(
        self,
        track: Path | str,
        title: str,
        *,
        source: str,
        loop: bool,
    ) -> bool:
        target = Path(track)
        self._audio_override_active = True
        self._audio_override_source = str(source)
        try:
            changed = bool(self.player.play(target, loop=bool(loop), restart=True))
        except Exception:
            changed = False
            self.logger.exception('Lecture %s impossible: %s', source, target)
        if changed:
            self.state.music = (title or target.stem).strip() or target.stem
            self.state.status = 'Lecture en ligne' if source == 'online' else 'Lecture locale manuelle'
            return True
        self._audio_override_active = False
        self._audio_override_source = None
        self.state.status = (
            'Impossible de lire la musique en ligne'
            if source == 'online'
            else 'Impossible de lire la musique locale'
        )
        return False


    def play_online_track(self, track: Path | str, title: str) -> bool:
        return self._play_audio_override(track, title, source='online', loop=False)

    def play_manual_local_track(
        self,
        track: Path | str,
        title: str | None = None,
        *,
        loop: bool = False,
    ) -> bool:
        target = Path(track)
        return self._play_audio_override(
            target,
            title or target.stem,
            source='local',
            loop=bool(loop),
        )

    def resume_local_audio(self) -> bool:
        self._audio_override_active = False
        self._audio_override_source = None
        if not self.game_process_running:
            try:
                self.player.stop(fade_ms=self.config.fade_ms)
            except Exception:
                pass
            self._desired_local_track = None
            self.state.music = 'Aucune musique'
            self.state.status = 'En attente de Dofus.exe'
            return False
        if not self.automatic_detection_unlocked:
            return self._play_startup_generic(restart=True)
        track = getattr(self, '_desired_local_track', None)
        if track is None:
            try:
                track = self.music_library.resolve(None, combat=self.state.in_combat)
            except Exception:
                track = None
        if track is None:
            self.state.music = 'Aucune musique'
            self.state.status = 'Retour à la musique locale'
            return False
        try:
            changed = bool(self.player.play(track, loop=True, restart=True))
        except Exception:
            changed = False
            self.logger.exception('Reprise audio locale impossible: %s', track)
        if changed:
            self.state.music = Path(track).stem
            self.state.display_theme = Path(track).stem
            self.state.status = 'Retour à la musique Dofus'
        return changed

    def _play_location(
        self,
        location: LocationRecord,
        raw_text: str,
        *,
        new_generic_cycle: bool = False,
    ) -> None:
        # One-way boundary: only an already-confirmed location reaches the music library.
        # Combat is an audio mode of that same confirmed context, not another resolver.
        self.state.place = location.name
        track = self.music_library.resolve(
            location,
            raw_text=raw_text,
            combat=self.state.in_combat,
            new_generic_cycle=new_generic_cycle,
        )
        mode = 'combat' if self.state.in_combat else 'exploration'
        self._play_track(track, status=f'Lieu confirmé : {location.name} ({mode})')
        if track is None:
            # Even with no audio file, the UI still has a stable user-facing context.
            self.state.display_theme = location.parent_area_name or location.name

    def _update_combat_from_toolbar(self, toolbar: np.ndarray) -> None:
        observation = analyze_combat_toolbar(toolbar, self.hud_geometry)
        self.state.combat_confidence = float(observation.confidence)
        transition = self.combat_tracker.update(observation)
        if transition is None:
            return

        self.state.in_combat = bool(transition)
        label = 'combat' if transition else 'hors combat'
        self.logger.info(
            'COMBAT state=%s confidence=%.3f ratio=%.3f',
            label, observation.confidence, observation.luminance_ratio,
        )

        # Recompute only the desired local audio. _play_track already guarantees
        # that an active online/API track is never interrupted.
        if self.current_location is not None:
            # Entering combat starts a fresh generic-combat cycle. Leaving
            # combat intentionally does not reroll exploration: it restores the
            # exploration choice that was active before combat.
            self._play_location(
                self.current_location,
                self.state.ocr_text,
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
        self.logger.debug('Zone non confirmée: %s', reason)
        self.state.status = 'Zone non reconnue'
        self.next_confirmation_at = now + self.config.pending_confirmation_interval_sec

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

    def _debug_evidence(self, evidence: LocationEvidence | None) -> str:
        if not evidence:
            return ''
        top1 = evidence.top1
        top2 = evidence.top2
        return (
            f"Top1={top1.location.name if top1 else '-'}/{top1.rank_score if top1 else 0:.1f} | "
            f"Top2={top2.location.name if top2 else '-'}/{top2.rank_score if top2 else 0:.1f} | "
            f"marge={evidence.margin:.1f} | position={self.state.position_display} | "
            f"coord_bonus={evidence.coordinate_consistency:.2f}"
        )

    def handle_position_result(
        self,
        result: PositionOCRResult,
        *,
        now: float | None = None,
        observed_at: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else float(now)
        observation_time = now if observed_at is None else float(observed_at)
        reference = self.context_position if self.current_location is not None else self.last_reliable_position
        coordinates = contextual_coordinates(
            result.raw_text,
            result.coordinates,
            reference,
            tolerance=self.config.context_position_tolerance,
            known_coordinate=lambda c: bool(self.repository.coordinate_candidates(c, tolerance=0)),
        )
        self.state.position_elapsed_ms = float(result.elapsed_ms)
        self._set_position(result.raw_text, coordinates)
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
                self.logger.info(
                    'AUTOMATION unlocked first_position=%s,%s',
                    coordinates.x, coordinates.y,
                )
        if result.error:
            self.logger.warning('Position OCR: %s', result.error)

    def handle_zone_result(
        self,
        result: ZoneOCRResult,
        *,
        now: float | None = None,
        observed_at: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else float(now)
        observation_time = now if observed_at is None else float(observed_at)
        self.state.zone_elapsed_ms = float(result.elapsed_ms)
        if result.error:
            self._mark_unknown(now, f'OCR zone: {result.error}')
            return
        if not result.text.strip():
            self.state.ocr_text = ''
            self.state.confidence = 0.0
            self._mark_unknown(now, 'Aucun nom de zone lisible')
            return

        zone_text_key = clean_text(result.text).casefold()

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
        if (
            decision.state is not DecisionState.UNKNOWN
            and zone_text_key
            and zone_text_key != getattr(self, '_zone_overlay_text_key', '')
        ):
            self._zone_overlay_text_key = zone_text_key
            self._zone_overlay_refresh_requested = True
        self.state.ocr_text = result.text
        self.state.confidence = result.confidence
        self.state.decision_state = decision.state.value
        self.state.debug_text = self._debug_evidence(evidence) if self.config.debug else ''

        if decision.state is DecisionState.CONFIRMED and decision.location is not None:
            self.unknown_since = None
            self.next_confirmation_at = 0.0
            previous_location = self.current_location
            self.current_location = decision.location
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
                self.logger.info(
                    'LOCATION changed location=%s raw=%r conf=%.3f score=%.1f margin=%.1f coord=%s',
                    decision.location.canonical_key,
                    result.text,
                    result.confidence,
                    evidence.top1.rank_score if evidence.top1 else 0.0,
                    evidence.margin,
                    coordinates,
                )
            else:
                # Re-confirming the same place updates confidence/context only.
                # It must not resolve the library or touch the audio pipeline again.
                self.state.place = decision.location.name
                self.logger.debug(
                    'LOCATION reconfirmed location=%s raw=%r conf=%.3f coord=%s',
                    decision.location.canonical_key, result.text, result.confidence, coordinates,
                )
            return
        if decision.state in (DecisionState.PENDING, DecisionState.AMBIGUOUS):
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
            return
        self._mark_unknown(now, decision.reason)

    def _handle_event(self, channel: str, event, now: float) -> None:
        slot = self.zone_slot if channel == 'zone' else self.position_slot
        scheduler = slot.scheduler
        if event.kind is OCREventKind.STARTING:
            if channel == 'zone':
                self.state.zone_worker_status = 'Chargement OCR zone'
            else:
                self.state.position_worker_status = 'Chargement OCR position'
            self._refresh_worker_status()
            return
        if event.kind is OCREventKind.READY:
            slot.ready = True
            slot.fatal = False
            if channel == 'zone':
                self.state.zone_worker_status = 'OCR zone prêt'
            else:
                self.state.position_worker_status = 'OCR position prêt'
            self._refresh_worker_status()
            return
        if event.kind is OCREventKind.RESULT and event.result is not None:
            if event.request_id and not scheduler.completed(request_id=event.request_id, now=now):
                self.logger.debug(
                    'Résultat %s orphelin request=%s active=%s',
                    channel, event.request_id, scheduler.active_request_id,
                )
                return
            observed_at = float(getattr(event, 'captured_at', 0.0) or now)
            submitted_at = float(getattr(event, 'submitted_at', 0.0) or observed_at)
            finished_at = float(getattr(event, 'finished_at', 0.0) or now)
            worker_ms = max(0.0, (finished_at - submitted_at) * 1000.0)
            total_ms = max(0.0, (now - observed_at) * 1000.0)
            log = self.logger.info if channel == 'zone' else self.logger.debug
            log(
                'OCR finished channel=%s request=%s worker_ms=%.0f total_ms=%.0f',
                channel, event.request_id, worker_ms, total_ms,
            )
            if channel == 'zone':
                self.handle_zone_result(event.result, now=now, observed_at=observed_at)
            else:
                self.handle_position_result(event.result, now=now, observed_at=observed_at)
            return
        if event.kind is OCREventKind.ERROR:
            if event.request_id:
                if scheduler.active_request_id is not None and event.request_id != scheduler.active_request_id:
                    self.logger.debug(
                        'Erreur %s orpheline request=%s active=%s',
                        channel, event.request_id, scheduler.active_request_id,
                    )
                    return
                scheduler.failed(request_id=event.request_id, now=now)
            else:
                slot.fatal = True
            if channel == 'zone':
                self.state.zone_worker_status = 'Erreur OCR zone'
                self.state.status = 'Erreur OCR zone'
            else:
                self.state.position_worker_status = 'Erreur OCR position'
            self.logger.error('Worker %s: %s', channel, event.message)
            self._refresh_worker_status()
            return
        if event.kind is OCREventKind.STOPPED:
            if channel == 'zone':
                self.state.zone_worker_status = 'OCR zone arrêté'
            else:
                self.state.position_worker_status = 'OCR position arrêté'
            self._refresh_worker_status()

    def _poll_workers(self, now: float) -> None:
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            try:
                for event in worker.poll():
                    self._handle_event(channel, event, now)
            except Exception:
                self.logger.exception('Lecture worker %s impossible', channel)

            if self.started and not slot.fatal and not getattr(worker, 'alive', False):
                if now - slot.last_restart_at >= self.config.worker_restart_backoff_sec:
                    slot.last_restart_at = now
                    try:
                        # A dead worker can never return the in-flight result that
                        # belonged to its old queues. Reset single-flight state before
                        # restarting, otherwise the channel remains blocked forever.
                        slot.scheduler.reset(request_refresh=True)
                        slot.ready = False
                        worker.restart()
                    except Exception:
                        slot.fatal = True
                        self.logger.exception('Auto-redémarrage worker %s impossible', channel)
        self._refresh_worker_status()

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
            submit = getattr(worker, 'submit', None)
            if callable(submit):
                request_id = submit(image, captured_at=captured_at)
            else:
                try:
                    request_id = worker.submit_latest(image, captured_at=captured_at)
                except TypeError as exc:
                    if 'captured_at' not in str(exc):
                        raise
                    request_id = worker.submit_latest(image)
        except Exception:
            self.logger.exception('Envoi OCR impossible')
            return False
        if request_id is None:
            return False
        slot.scheduler.submitted(request_id=request_id, now=now)
        channel = 'zone' if slot is self.zone_slot else 'position'
        log = self.logger.info if channel == 'zone' else self.logger.debug
        log(
            'OCR submitted channel=%s request=%s capture_age_ms=%.0f',
            channel, request_id, max(0.0, (now - captured_at) * 1000.0),
        )
        return True

    def tick(self, *, now: float | None = None) -> ControllerState:
        now = time.monotonic() if now is None else float(now)
        if not self.started:
            self.start()
        if not self._sync_game_process(now):
            return self.state
        self._poll_workers(now)

        snapshot = self.capture_service.latest(self._last_capture_sequence, now=now)
        if snapshot is None:
            if (
                self.last_capture_seen_at > 0.0
                and now - self.last_capture_seen_at >= self.config.capture_missing_grace_sec
            ):
                self.state.window_found = False
                self.state.overlay_hwnd = None
                self.state.combat_overlay_rect = None
                self.state.zone_overlay_rect = None
                self.state.position_overlay_rect = None
            return self.state
        self._last_capture_sequence = snapshot.sequence
        if snapshot.error:
            self.logger.warning('Capture Dofus: %s', snapshot.error)
        frame = snapshot.frame
        captured_at = float(snapshot.captured_at)

        if frame is None:
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
        hud = extract_hud_inputs(frame.image, self.hud_geometry)
        if self.automatic_detection_unlocked:
            self._update_combat_from_toolbar(hud.combat)

        # Only a genuinely visible capture is allowed to drive the screen overlay.
        # PrintWindow is useful for OCR recovery but would let an overlay appear on
        # top of a different foreground application, which is explicitly forbidden.
        if frame.source == 'visible' and frame.screen_rect is not None:
            rects = tracking_screen_rects(
                capture_screen_rect=frame.screen_rect,
                capture_image_shape=frame.image.shape,
                hud=hud,
                geometry=self.hud_geometry,
            )
            self.state.overlay_hwnd = int(frame.hwnd)
            self.state.combat_overlay_rect = rects.combat
            self.state.zone_overlay_rect = self._stable_zone_overlay_rect(
                rects.zone,
                text_present=zone_crop_has_text(hud.zone),
            )
            self.state.position_overlay_rect = self._position_overlay_screen_rect(
                full_rect=rects.position,
                result=self._accepted_position_result,
                roi_height=int(hud.position.shape[0]) if hud.position.ndim >= 2 else 0,
                roi_width=int(hud.position.shape[1]) if hud.position.ndim >= 2 else 0,
            )
        else:
            self.state.overlay_hwnd = None
            self.state.combat_overlay_rect = None
            self.state.zone_overlay_rect = None
            self.state.position_overlay_rect = None

        zone_confirmation_due = bool(self.next_confirmation_at and now >= self.next_confirmation_at)
        zone_force = zone_confirmation_due or self.current_location is None
        if (
            self.automatic_detection_unlocked
            and getattr(self.zone_worker, 'alive', False)
            and not self.zone_slot.fatal
        ):
            zone_changed = self.zone_change_gate.should_scan(hud.zone, now=now, force=zone_force)
            if zone_changed and self.zone_slot.scheduler.should_submit(
                now=now,
                min_interval=self.config.zone_ocr_min_interval_sec,
                force=zone_confirmation_due,
            ):
                if self._submit_channel(self.zone_worker, self.zone_slot, hud.zone, now, captured_at):
                    self.zone_change_gate.mark_scanned(now=now)
                    self.state.status = f'OCR zone en cours ({frame.source})'

        if getattr(self.position_worker, 'alive', False) and not self.position_slot.fatal:
            position_force = self.last_reliable_position is None
            position_changed = self.position_change_gate.should_scan(hud.position, now=now, force=position_force)
            if position_changed and self.position_slot.scheduler.should_submit(
                now=now,
                min_interval=self.config.position_ocr_min_interval_sec,
            ):
                if self._submit_channel(self.position_worker, self.position_slot, hud.position, now, captured_at):
                    self.position_change_gate.mark_scanned(now=now)

        return self.state

    def close(self) -> None:
        for component in (self.zone_worker, self.position_worker, self.capture_service, self.player):
            try:
                component.close()
            except Exception:
                pass
