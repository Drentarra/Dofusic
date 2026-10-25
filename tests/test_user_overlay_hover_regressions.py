"""Régressions pour la dérive des rectangles blancs signalée sur Dofus Windows.

Tous les appels Win32/Tk sont simulés de manière déterministe ici; seul le
retour sur le PC du joueur pourra confirmer l'absence de dérive physique.
"""

import pytest

from dofusic.ui import overlay as module
from dofusic.vision.layout import HUDTransform
from tools.hud_reference import HUDTransformSmoother


class Canvas:
    def configure(self, **kwargs):
        pass

    def delete(self, *_):
        pass


class Top:
    def __init__(self):
        self.geometries = []
        self.deiconified = 0
        self.withdrawn = 0

    def geometry(self, request):
        self.geometries.append(request)

    def deiconify(self):
        self.deiconified += 1

    def withdraw(self):
        self.withdrawn += 1


def scenario(monkeypatch):
    top = Top()
    window = module._OverlayWindow(top=top, canvas=Canvas(), native_hwnd=41)
    obj = module.TrackingOverlay.__new__(module.TrackingOverlay)
    obj.window = window
    obj._draw_tracking_outline = lambda *a, **k: object()
    location = {'rect': None}
    calls = []
    permitted = {'ok': True}

    def move(hwnd, rect):
        calls.append((hwnd, rect))
        if permitted['ok']:
            location['rect'] = rect
            return True
        return False

    monkeypatch.setattr(module, 'position_overlay_window', move)
    monkeypatch.setattr(module, 'native_overlay_position_valid', lambda hwnd, rect: location['rect'] == rect)
    return obj, window, location, calls, permitted, top


def test_native_window_moved_by_tk_is_repaired_without_new_geometry(monkeypatch):
    overlay, window, physical, calls, permitted, top = scenario(monkeypatch)
    rects = ((0, 0, 38, 37), (0, 40, 250, 34), (0, 69, 90, 29))
    overlay._place(*rects)
    assert len(calls) == 1
    requested = window.geometry
    physical['rect'] = (200, 160, 200, 120)  # an async Tk configure event
    overlay._place(*rects)  # incoming HUD geometry is strictly unchanged
    assert len(calls) == 2
    assert physical['rect'] == requested
    assert window.geometry == requested


def test_failed_native_reposition_is_not_cached_and_recovers(monkeypatch):
    overlay, window, physical, calls, permitted, top = scenario(monkeypatch)
    rects = ((0, 0, 38, 37), (0, 40, 200, 34), (0, 69, 90, 29))
    permitted['ok'] = False
    overlay._place(*rects)
    assert not window.visible
    assert window.geometry is None  # must retry, not incorrectly mark as placed
    permitted['ok'] = True
    overlay._place(*rects)
    assert window.visible
    assert window.geometry == physical['rect']
    assert len(calls) == 2


def test_negative_desktop_origin_uses_native_position_not_tk_relative_offsets(monkeypatch):
    overlay, window, physical, calls, permitted, top = scenario(monkeypatch)
    rects = ((-580, 0, 38, 37), (-580, 40, 250, 34), (-580, 69, 90, 29))
    overlay._place(*rects)
    assert calls[0][1][0] < 0
    assert top.geometries[0].endswith('+0+0')
    assert window.geometry == physical['rect']


def test_native_geometry_validator_can_read_actual_win32_rect():
    def read(hwnd, ptr):
        assert hwnd == 41
        # Explicitly populate ctypes' RECT through the pointer passed by reference.
        from ctypes import POINTER, cast
        from ctypes.wintypes import RECT
        rect = cast(ptr, POINTER(RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = -4, 40, 258, 102
        return 1

    assert module.native_overlay_bounds(41, get_window_rect=read) == (-4, 40, 262, 62)
    assert module.native_overlay_position_valid(41, (-4, 40, 262, 62), get_window_rect=read)
    assert not module.native_overlay_position_valid(41, (-4, 41, 262, 65), get_window_rect=read)


def test_hud_scale_survives_prolonged_hover_then_accepts_actual_resize():
    smoother = HUDTransformSmoother(confirmations=2, miss_limit=2)
    start = HUDTransform(scale=1.25, origin_x=10, origin_y=4)
    assert smoother.update(start) == start
    for _ in range(30):
        assert smoother.update(None) == start
    new = HUDTransform(scale=.8, origin_x=20, origin_y=3)
    assert smoother.update(new) == start
    assert smoother.update(new) == new
    smoother.reset()
    assert smoother.update(HUDTransform()) == HUDTransform()


def test_failed_reposition_never_shows_wrong_overlay_as_topmost(monkeypatch):
    overlay, window, physical, calls, permitted, top = scenario(monkeypatch)
    permitted['ok'] = False
    overlay.enabled = True
    overlay._foreground_role = lambda hwnd: 'dofus'
    overlay._ensure_capture_excluded = lambda: True
    raised = []
    overlay._set_topmost = lambda state: raised.append(state)
    rects = ((0, 0, 38, 37), (0, 40, 200, 34), (0, 69, 90, 29))
    overlay.update(41, *rects)
    assert not window.visible
    assert raised == []


@pytest.mark.parametrize('index', range(6))
def test_six_real_user_hud_samples_have_coherent_screen_projection(index):
    """Captures réelles d'octobre 2026, sans le chat/personnage du joueur.

    Ce test vérifie la chaîne géométrique avec des images enregistrées, et non
    l'interaction de souris ni le pilote Win32 en direct.
    """
    import json
    from pathlib import Path

    import cv2
    import numpy as np

    from dofusic.vision.layout import extract_hud_inputs, tracking_screen_rects
    from tools.hud_reference import estimate_hud_transform

    folder = Path(__file__).parent / 'fixtures' / 'hud_live_october_2026'
    sample = json.loads((folder / 'labels.json').read_text(encoding='utf-8'))[index]
    image = cv2.imdecode(np.fromfile(str(folder / sample['fixture']), dtype=np.uint8), cv2.IMREAD_COLOR)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert transform.scale == pytest.approx(1., abs=.01)
    assert (transform.origin_x, transform.origin_y) == (0, 0)
    hud = extract_hud_inputs(image, transform=transform)
    assert hud.zone.shape == (34, sample['zone_roi_width'], 3)
    assert hud.position.shape == (29, 180, 3)
    screen = (-320, 200, image.shape[1], image.shape[0])
    rects = tracking_screen_rects(capture_screen_rect=screen, capture_image_shape=image.shape, hud=hud)
    assert rects.zone == (-320, 240, sample['zone_roi_width'], 34)
    assert rects.position == (-320, 269, 180, 29)
    assert rects.combat[0] == -320
    assert rects.combat[1] == 200
