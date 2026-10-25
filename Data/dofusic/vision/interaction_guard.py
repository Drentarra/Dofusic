"""Reject input-induced HUD changes before interpreting them as game state.

This is independent of icon templates, the window resolution and the audio
policy. Only the toolbar footprint is watched; no pixels or frames are scanned.
"""
from __future__ import annotations


class ToolbarInteractionGuard:
    """Treat pointer contact and a short post-hover animation as untrusted.

    ``bounds`` and ``cursor`` are in physical *screen* pixels. The caller
    builds bounds from the cached HUD transform, not from visual calibration.
    A missing cursor reading does not mean the cursor has been verified clear.
    """

    def __init__(self, *, release_grace_s: float = 0.35, margin_px: int = 5) -> None:
        self.release_grace_s = max(0.0, float(release_grace_s))
        self.margin_px = max(0, int(margin_px))
        self.reset()

    def reset(self) -> None:
        self._hold_until = float('-inf')
        self.active = False

    def observe(
        self, *, cursor: tuple[int, int] | None,
        bounds: tuple[int, int, int, int], now: float,
    ) -> bool:
        x, y, width, height = bounds
        margin = self.margin_px
        on_toolbar = (
            cursor is not None and width > 0 and height > 0
            and x - margin <= cursor[0] < x + width + margin
            and y - margin <= cursor[1] < y + height + margin
        )
        if on_toolbar:
            self._hold_until = float(now) + self.release_grace_s
        self.active = bool(on_toolbar or float(now) < self._hold_until)
        return self.active
