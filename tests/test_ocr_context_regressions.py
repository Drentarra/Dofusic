"""Régressions OCR : fraîcheur des coordonnées et géométrie des overlays."""
from types import SimpleNamespace

import numpy as np

from dofusic.app import ControllerState, DofusicController
from dofusic.models import Coordinates, PositionOCRResult, ZoneOCRResult
from dofusic.vision.layout import HUDGeometry, HUDInputs, HUDTransform
from dofusic.vision.overlay_projection import project_overlays, project_position_overlay


class _Logger:
    def debug(self, *args, **kwargs):
        pass
    def info(self, *args, **kwargs):
        pass
    def warning(self, *args, **kwargs):
        pass


def _context():
    c = DofusicController.__new__(DofusicController)
    c.current_location = None
    c.context_position = None
    c.last_reliable_position = None
    c.last_position_at = 0.0
    c._automatic_detection_unlocked = True
    c.repository = SimpleNamespace(coordinate_candidates=lambda *_a, **_k: [1])
    c.config = SimpleNamespace(context_position_tolerance=2, position_context_max_age_sec=4)
    c.state = ControllerState()
    c.logger = _Logger()
    return c


def test_invalid_ocr_preserves_recent_valid_coordinates_then_expires():
    c = _context()
    c.handle_position_result(PositionOCRResult('5, -19', 5, -19, .97, 1, 'fake'), now=10, observed_at=10)
    c.handle_position_result(PositionOCRResult('illisible', None, None, .1, 1, 'fake'), now=11, observed_at=11)
    assert (c.state.position_x, c.state.position_y) == (5, -19)
    assert c.last_position_at == 10
    assert c.state.position_text == 'illisible'  # visible error, no fake confirmation
    c.handle_position_result(PositionOCRResult('', None, None, 0, 1, 'fake'), now=15, observed_at=15)
    assert c.state.position_display == '—'
    assert c.last_reliable_position == Coordinates(5, -19)  # retained as evidence, not displayed


def test_stale_zone_error_cannot_erase_or_modify_newer_context():
    c = DofusicController.__new__(DofusicController)
    c.last_zone_observed_at = 42.0
    c.state = ControllerState(location='Astrub', status='Lieu confirmé', zone_elapsed_ms=2)
    c.logger = _Logger()
    c.handle_zone_result(ZoneOCRResult('', 0, 5, 'fake', 'worker failed'), now=60, observed_at=41)
    assert c.state.status == 'Lieu confirmé'
    assert c.state.zone_elapsed_ms == 2
    assert c.last_zone_observed_at == 42.0


def test_hud_projection_visible_vs_printwindow_and_invalid_position():
    hud = HUDInputs(
        zone=np.zeros((27, 130, 3), dtype=np.uint8),
        position=np.zeros((29, 180, 3), dtype=np.uint8),
        combat=np.zeros((40, 250, 3), dtype=np.uint8),
        transform=HUDTransform(),
    )
    args = dict(hwnd=147, screen_rect=(-700, 300, 450, 150),
                image_shape=(150, 450, 3), hud=hud,
                geometry=HUDGeometry(), toolbar_buttons=2, position_result=None)
    visible = project_overlays(source='visible', **args)
    assert visible.hwnd == 147
    assert visible.combat and visible.combat[0] == -700
    assert visible.position is None
    printwindow = project_overlays(source='printwindow', **args)
    assert printwindow.hwnd is None
    assert printwindow.zone is None
    assert project_position_overlay((0, 0, 0, 12), PositionOCRResult('1,2', 1, 2, .9, 1, 'test'), 29, 180) is None
