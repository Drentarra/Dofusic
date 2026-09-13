from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class OCRChannelScheduler:
    """Cadence-only single-flight state for one OCR channel.

    The controller may observe arbitrarily many capture frames, but exactly one
    OCR request can own the channel at a time. If another cadence tick happens
    while that request is active, only a boolean refresh intent is remembered;
    the newest frame will be submitted by the controller after completion.
    """

    active_request_id: int | None = None
    last_submit_at: float = 0.0
    last_finished_at: float = 0.0
    refresh_pending: bool = False

    def should_submit(self, *, now: float, min_interval: float, force: bool = False) -> bool:
        now = float(now)
        interval = max(0.0, float(min_interval))
        cadence_due = self.last_submit_at <= 0.0 or now - self.last_submit_at >= interval
        if self.active_request_id is not None:
            if force or cadence_due:
                self.refresh_pending = True
            return False
        if force or self.refresh_pending or cadence_due:
            return True
        return False

    def submitted(self, *, request_id: int, now: float) -> None:
        if self.active_request_id is not None:
            raise RuntimeError('Une requête OCR est déjà active sur ce canal')
        self.active_request_id = int(request_id)
        self.last_submit_at = float(now)
        self.refresh_pending = False

    def completed(self, *, request_id: int, now: float) -> bool:
        if self.active_request_id is None or int(request_id) != self.active_request_id:
            return False
        self.active_request_id = None
        self.last_finished_at = float(now)
        return True

    def failed(self, *, request_id: int, now: float) -> bool:
        matched = self.completed(request_id=request_id, now=now)
        if matched:
            self.refresh_pending = True
        return matched

    def reset(self, *, request_refresh: bool = True) -> None:
        self.active_request_id = None
        self.last_submit_at = 0.0
        self.last_finished_at = 0.0
        self.refresh_pending = bool(request_refresh)
