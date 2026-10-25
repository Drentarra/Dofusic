"""OCR event dispatch and worker lifecycle coordination.

The controller retains the game/audio decisions.  This mixin handles only
status, generation-guarded replies and worker restart orchestration.
"""
from __future__ import annotations

from dofusic.models import DecisionState
from dofusic.vision.workers import OCREventKind


class OCREventDispatchMixin:
    """Shared OCR channel event handling for the production controller."""

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
                return
            observed_at = float(getattr(event, 'captured_at', 0.0) or now)
            if channel == 'zone':
                self.handle_zone_result(event.result, now=now, observed_at=observed_at)
                pending = getattr(self, '_pending_zone_width', None)
                if pending is not None and pending[0] == event.request_id:
                    _, width, window_id, scale = pending
                    self._pending_zone_width = None
                    # Do not promote an obsolete, truncated or unaccepted text
                    # to an overlay width merely because the OCR worker replied.
                    if (not self.state.in_combat
                            and observed_at >= getattr(self, '_zone_min_observed_at', float('-inf'))
                            and self.state.ocr_text == event.result.text
                            and self.state.decision_state == DecisionState.CONFIRMED.value
                            and observed_at >= self.last_zone_observed_at):
                        self._zone_overlay_width.confirm(
                            event.result.text, width, window_id=window_id,
                            scale=scale, confidence=event.result.confidence,
                        )
            else:
                self.handle_position_result(event.result, now=now, observed_at=observed_at)
            return
        if event.kind is OCREventKind.ERROR:
            if event.request_id:
                if scheduler.active_request_id is not None and event.request_id != scheduler.active_request_id:
                    return
                scheduler.failed(request_id=event.request_id, now=now)
            else:
                slot.fatal = True
            if channel == 'zone':
                self.state.zone_worker_status = 'Erreur OCR zone'
                self.state.status = 'Erreur OCR zone'
            else:
                self.state.position_worker_status = 'Erreur OCR position'
            self._refresh_worker_status()
            return
        if event.kind is OCREventKind.STOPPED:
            if channel == 'zone':
                self.state.zone_worker_status = 'OCR zone arrêté'
            else:
                self.state.position_worker_status = 'OCR position arrêté'
            self._refresh_worker_status()

    def _poll_workers(self, now: float) -> None:
        supervisor = getattr(self, '_ocr_supervisor', None)
        if supervisor is not None:
            for channel, message in supervisor.poll_errors():
                slot = self.zone_slot if channel == 'zone' else self.position_slot
                slot.fatal = True
                slot.ready = False
                slot.scheduler.reset(request_refresh=True)
                if channel == 'zone':
                    self.state.zone_worker_status = 'Erreur OCR zone'
                else:
                    self.state.position_worker_status = 'Erreur OCR position'
        for channel, worker, slot in (
            ('zone', self.zone_worker, self.zone_slot),
            ('position', self.position_worker, self.position_slot),
        ):
            try:
                for event in worker.poll():
                    self._handle_event(channel, event, now)
            except Exception:
                pass

            # A living process may stall in ONNX or the IPC queue without ever
            # returning a RESULT/ERROR event. Its single-flight request would
            # otherwise block this channel forever. Restart only that channel;
            # preserve confirmed position, zone and soundtrack.
            request_id = slot.scheduler.active_request_id
            request_age = now - slot.scheduler.last_submit_at if request_id is not None else 0.0
            stalled = bool(
                request_id is not None and request_age >= 15.0
                and getattr(worker, 'alive', False)
            )
            dead = not getattr(worker, 'alive', False)
            if (self.started and (dead or stalled)
                    and (supervisor is None or not supervisor.pending(channel))):
                backoff = max(2.0, float(self.config.worker_restart_backoff_sec))
                if now - slot.last_restart_at >= backoff:
                    slot.last_restart_at = now
                    # Reset before restart, so an obsolete reply cannot reclaim
                    # this channel; fresh OCR will use the newest capture.
                    slot.scheduler.reset(request_refresh=True)
                    slot.ready = False
                    slot.fatal = False
                    try:
                        if supervisor is not None:
                            supervisor.restart(channel)
                        else:
                            worker.restart()
                    except Exception:
                        slot.fatal = True
        self._refresh_worker_status()
