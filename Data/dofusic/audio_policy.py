"""Controller policy for automatic, dungeon and manually selected local audio.

Separated from OCR/capture scheduling to keep music transitions testable while
leaving the previous priority, fallback and restart behavior unchanged.
"""
from __future__ import annotations

from pathlib import Path

from dofusic.models import LocationRecord


class ControllerAudioPolicyMixin:
    """Soundtrack decisions invoked only by the production controller."""

    def _audio_status(self, message: str) -> None:
        self.state.status = message

    def set_volume(self, value: float) -> int:
        volume = int(self.player.set_volume(value))
        self.config.volume = volume
        return volume

    def set_muted(self, muted: bool) -> None:
        self.player.set_muted(muted)
        self.config.mute = bool(muted)

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
        if changed:
            self.state.music = target.stem
            self.state.display_theme = target.stem
            self.state.status = 'Musique générique - attente position Dofus'
            return True
        self.state.music = 'Aucune musique'
        self.state.status = 'Impossible de lire la musique générique'
        return False

    def _play_track(self, track: Path | None, *, status: str) -> None:
        if track is None:
            self._desired_local_track = None
            if not self.audio_override_active:
                try:
                    self.player.stop(fade_ms=self.config.fade_ms)
                except Exception:
                    pass
                self.state.music = 'Aucune musique'
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
            # Keep updating the desired soundtrack during local manual playback.
            self.state.status = status
            return
        try:
            changed = self.player.play(track)
        except Exception:
            changed = False
        if changed:
            self.state.music = track.stem
            self.state.display_theme = track.stem
            self.state.status = status
        else:
            self.state.music = 'Aucune musique'
            self.state.status = f'Lecture audio impossible : {track.name}'

    @property
    def audio_override_active(self) -> bool:
        return bool(getattr(self, '_audio_override_active', False))

    @property
    def automatic_detection_unlocked(self) -> bool:
        # Default True keeps lightweight __new__-based internal consumers backward
        # compatible; production instances always initialize the latch explicitly.
        return bool(getattr(self, '_automatic_detection_unlocked', True))

    def play_manual_local_track(
        self,
        track: Path | str,
        title: str | None = None,
        *,
        loop: bool = False,
    ) -> bool:
        target = Path(track)
        self._audio_override_active = True
        try:
            changed = bool(self.player.play(target, loop=bool(loop), restart=True))
        except Exception:
            changed = False
        if changed:
            self.state.music = (title or target.stem).strip() or target.stem
            self.state.status = 'Lecture locale manuelle'
            return True
        self._audio_override_active = False
        self.state.status = 'Impossible de lire la musique locale'
        return False

    def resume_local_audio(self) -> bool:
        self._audio_override_active = False
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
            try:
                self.player.stop(fade_ms=self.config.fade_ms)
            except Exception:
                pass
            self.state.music = 'Aucune musique'
            self.state.status = 'Aucune musique automatique disponible'
            return False
        try:
            changed = bool(self.player.play(track, loop=True, restart=True))
        except Exception:
            changed = False
        if changed:
            self.state.music = Path(track).stem
            self.state.display_theme = Path(track).stem
            self.state.status = 'Retour à la musique Dofus'
        else:
            self.state.music = 'Aucune musique'
            self.state.status = f'Lecture automatique impossible : {Path(track).name}'
        return changed

    def _retry_desired_audio(self, now: float) -> None:
        """Recover a failed/stopped automatic playback without rerolling a track.

        OCR changes should determine *which* music is desired, not be the only
        occasion on which the audio backend can recover from a transient error.
        One bounded retry every five seconds prevents a damaged file from
        generating a tight exception/decoder loop.
        """
        target = getattr(self, '_desired_local_track', None)
        if target is None or self.audio_override_active or not self.game_process_running:
            return
        if now - getattr(self, '_last_audio_retry_at', float('-inf')) < 5.0:
            return
        playing = getattr(self.player, 'is_playing', None)
        if not callable(playing):
            return
        try:
            preparing = getattr(self.player, 'is_preparing', None)
            if callable(preparing) and preparing(target):
                return
            transition_target = getattr(self.player, 'transition_target', None)
            if transition_target == target:
                return
            if bool(playing()) and getattr(self.player, 'current', None) == target:
                return
        except Exception:
            pass
        self._last_audio_retry_at = now
        if not self.automatic_detection_unlocked:
            self._play_startup_generic(restart=True)
        else:
            self._play_track(target, status='Reprise de la lecture automatique')

    def _is_dungeon_context(self, location: LocationRecord | None = None) -> bool:
        """Classifier uniquement le lieu confirmé; garder le dernier contexte pendant une occultation."""
        target = getattr(self, 'current_location', None) if location is None else location
        catalog = getattr(getattr(self, 'music_library', None), 'dungeon_catalog', None)
        return bool(target is not None and catalog is not None and catalog.is_dungeon(target))

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
        in_dungeon = self._is_dungeon_context(location)
        # UI reports the real confirmed combat state, even inside a dungeon.
        # Only the audio policy ignores combat when a dungeon is confirmed.
        combat = bool(self.state.in_combat and not in_dungeon)
        track = self.music_library.resolve(
            location,
            raw_text=raw_text,
            combat=combat,
            new_generic_cycle=new_generic_cycle and not in_dungeon,
        )
        mode = 'combat' if combat else 'exploration'
        self._play_track(track, status=f'Lieu confirmé : {location.name} ({mode})')
        if track is None:
            # Even with no audio file, the UI still has a stable user-facing context.
            self.state.display_theme = location.parent_area_name or location.name
