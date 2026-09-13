from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class VolumeGesture:
    """Owns the slider drag gesture so off-slider mouse drags are ignored."""

    x1: int
    x2: int
    y: int
    hit_half_height: int = 10
    _dragging: bool = False

    def _inside(self, x: int, y: int) -> bool:
        return (
            self.x1 - 8 <= int(x) <= self.x2 + 8
            and self.y - self.hit_half_height <= int(y) <= self.y + self.hit_half_height
        )

    def value_from_x(self, x: int) -> int:
        ratio = (float(x) - self.x1) / max(1.0, self.x2 - self.x1)
        return max(0, min(100, int(round(ratio * 100.0))))

    def press(self, x: int, y: int) -> bool:
        self._dragging = self._inside(x, y)
        return self._dragging

    def drag(self, x: int, y: int) -> int | None:
        if not self._dragging:
            return None
        return self.value_from_x(x)

    def release(self) -> None:
        self._dragging = False


def mute_visual_active(*, muted: bool, volume: int | float) -> bool:
    return bool(muted) or float(volume) <= 0.0
