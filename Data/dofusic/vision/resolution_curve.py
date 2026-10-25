"""Deterministic, monotone HUD scale curve fitted from user screenshots.

All samples refer to one Dofus UI-scale setting.  They are measured OFFLINE,
never detected during game play.  Runtime only reads Win32 client size.

Anchor measurements (toolbar physical height, ±1 pixel):
  1280x960: 23; 1600x900: 30; 1680x1050: 31;
  1920x1080: 37; 2560x1440: ~36-37 (after undoing 0.8 upload scale).
The last two measurements are treated as one 37px plateau.
"""
from __future__ import annotations

from bisect import bisect_right

REFERENCE_TOOLBAR_HEIGHT_PX = 37.0
# Dimensionless limiting resolution: min(width/1920, height/1080, 1).
# These anchors are a small empirical dataset, not a general Dofus UI guarantee.
_KNOTS = (2.0 / 3.0, 5.0 / 6.0, 7.0 / 8.0, 1.0)
_HEIGHTS = (23.0, 30.0, 31.0, 37.0)


def _monotone_slopes(xs: tuple[float, ...], ys: tuple[float, ...]) -> tuple[float, ...]:
    """PCHIP/Fritsch-Butland slopes; avoids spline overshoot and oscillation."""
    n = len(xs)
    delta = tuple((ys[j + 1] - ys[j]) / (xs[j + 1] - xs[j]) for j in range(n - 1))
    slopes = [delta[0]] + [0.0] * (n - 2) + [0.0]  # plateau at full UI size
    for i in range(1, n - 1):
        a, b = delta[i - 1], delta[i]
        if a <= 0.0 or b <= 0.0:
            continue
        h0, h1 = xs[i] - xs[i - 1], xs[i + 1] - xs[i]
        w0, w1 = 2.0 * h1 + h0, h1 + 2.0 * h0
        slopes[i] = (w0 + w1) / (w0 / a + w1 / b)
    return tuple(slopes)


_SLOPES = _monotone_slopes(_KNOTS, _HEIGHTS)


def reference_hud_scale(
    client_width: int, client_height: int, *,
    reference_width: int = 1920, reference_height: int = 1080,
) -> float:
    """Return frozen HUD scale for a Win32 client size, with no image analysis.

    Smooth monotone interpolation between screenshot anchors. Beyond the
    highest tested limiting resolution it is conservatively capped at 1x.
    Below the smallest reference point it extrapolates proportionally.
    This models one interface-scale setting; custom in-game UI zoom is not
    inferable from resolution alone.
    """
    if min(client_width, client_height, reference_width, reference_height) <= 0:
        raise ValueError('Client and reference dimensions must be positive')
    t = min(float(client_width) / reference_width, float(client_height) / reference_height, 1.0)
    if t >= 1.0:
        return 1.0
    if t <= _KNOTS[0]:
        return (_HEIGHTS[0] / REFERENCE_TOOLBAR_HEIGHT_PX) * (t / _KNOTS[0])
    i = bisect_right(_KNOTS, t) - 1
    h = _KNOTS[i + 1] - _KNOTS[i]
    z = (t - _KNOTS[i]) / h
    z2, z3 = z*z, z*z*z
    # Cubic Hermite interpolation in toolbar pixels, then normalize to 37px.
    pixels = ((2*z3 - 3*z2 + 1) * _HEIGHTS[i]
              + (z3 - 2*z2 + z) * h * _SLOPES[i]
              + (-2*z3 + 3*z2) * _HEIGHTS[i + 1]
              + (z3 - z2) * h * _SLOPES[i + 1])
    return min(1.0, max(0.0, pixels / REFERENCE_TOOLBAR_HEIGHT_PX))
