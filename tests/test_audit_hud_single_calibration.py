"""La calibration visuelle ne doit pas être recalculée par le contrôleur."""
from types import SimpleNamespace

import numpy as np

from dofusic.app import DofusicController
from dofusic.capture.service import CaptureSnapshot
from dofusic.vision import layout


def test_controller_reuses_transform_provided_by_capture_thread(monkeypatch):
    controller = DofusicController.__new__(DofusicController)
    controller.started = True
    controller._automatic_detection_unlocked = False
    controller._last_capture_sequence = 0
    controller.last_capture_seen_at = 0.0
    controller.state = SimpleNamespace()
    controller.hud_geometry = layout.HUDGeometry()
    controller.zone_worker = SimpleNamespace(alive=False)
    controller.position_worker = SimpleNamespace(alive=False)
    controller._sync_game_process = lambda now: True
    controller._poll_workers = lambda now: None

    transform = layout.HUDTransform()
    image = np.zeros((110, 240, 3), dtype=np.uint8)
    frame = SimpleNamespace(image=image, source='printwindow',
                            screen_rect=None, hwnd=12, hud_transform=transform)
    snapshot = CaptureSnapshot(1, frame, 100.0)
    controller.capture_service = SimpleNamespace(latest=lambda *a, **kw: snapshot)

    def unexpected_calibration(*_args, **_kwargs):
        raise AssertionError('Le frame possède déjà sa transformation HUD')

    monkeypatch.setattr(layout, 'estimate_hud_transform', unexpected_calibration)
    state = controller.tick(now=100.0)
    assert state.window_found
    assert controller._last_capture_sequence == 1
