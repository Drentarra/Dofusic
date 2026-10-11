import threading

import cv2
import pytest

from dofusic.capture.service import CaptureService
from dofusic.vision.combat import analyze_combat_toolbar
from dofusic.vision.layout import estimate_hud_transform, extract_hud_inputs
from test_combat_shapes import collapsed_toolbar, expanded_toolbar


@pytest.mark.parametrize('expanded', [False, True])
@pytest.mark.parametrize('scale', [0.75, 0.9, 1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize('boundary', [60, None])
def test_calibration_validates_control_shape_when_scene_resembles_panel(expanded, scale, boundary):
    image = expanded_toolbar(scenery=(83, 58, 55)) if expanded else collapsed_toolbar(scenery=(65, 60, 55))
    if boundary is not None:
        image[boundary:, :32] = (20, 150, 115)
    image = cv2.resize(image, None, fx=scale, fy=scale)
    transform = estimate_hud_transform(image)
    assert transform is not None
    assert transform.scale == pytest.approx(scale, abs=0.05)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


def test_bright_scene_cannot_hide_low_contrast_control_during_calibration():
    image = collapsed_toolbar(background=(65, 60, 55), foreground=(100, 100, 100), scenery=(65, 60, 55))
    image[60:] = 255
    assert analyze_combat_toolbar(image[:40, :320]).in_combat is True
    transform = estimate_hud_transform(image)
    assert transform.scale == pytest.approx(1.0, abs=0.05)
    assert analyze_combat_toolbar(extract_hud_inputs(image).combat).in_combat is True


def test_capture_restart_waits_for_blocked_backend_to_close():
    entered = threading.Event()
    release = threading.Event()
    restarted = threading.Event()
    instances = []

    class Backend:
        def __init__(self):
            self.index = len(instances)
            self.calls = 0
            self.closed = False
            instances.append(self)

        def capture(self):
            self.calls += 1
            if self.index == 0 and self.calls == 1:
                entered.set()
                release.wait(2)
            if self.index > 0:
                restarted.set()
            return None

        def close(self):
            self.closed = True

    service = CaptureService(Backend, fps=60)
    try:
        service.start()
        assert entered.wait(1)
        service.close(timeout=0.01)
        service.start()
        assert len(instances) == 1
        release.set()
        assert restarted.wait(1)
        assert instances[0].closed
        assert instances[0].calls == 1
    finally:
        release.set()
        service.close(timeout=2)
    assert not service.alive
    assert all(backend.closed for backend in instances)
