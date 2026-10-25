"""Reproductions déterministes des régressions de chronologie et d'état audio."""
from pathlib import Path
from types import SimpleNamespace

from dofusic.app import DofusicController
from dofusic.models import PositionOCRResult
from dofusic.vision.scheduler import OCRChannelScheduler


def test_position_ocr_older_capture_cannot_replace_newer_coordinates(monkeypatch):
    controller = DofusicController.__new__(DofusicController)
    controller.current_location = None
    controller.context_position = None
    controller.last_reliable_position = None
    controller.last_position_at = 0.0
    controller._automatic_detection_unlocked = True
    controller.repository = SimpleNamespace(coordinate_candidates=lambda *_a, **_k: [1])
    controller.config = SimpleNamespace(context_position_tolerance=2)
    controller.state = SimpleNamespace(position_elapsed_ms=0, automatic_detection_unlocked=True)
    controller._set_position = lambda raw, coordinates: setattr(controller.state, 'position', coordinates)
    controller.logger = SimpleNamespace(debug=lambda *_a, **_k: None, info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)
    recent = PositionOCRResult('10, 20', 10, 20, 0.99, 1.0, 'test')
    stale = PositionOCRResult('2, 3', 2, 3, 0.99, 1.0, 'test')
    controller.handle_position_result(recent, now=12, observed_at=11)
    controller.handle_position_result(stale, now=13, observed_at=10)
    assert controller.state.position.x == 10
    assert controller.last_reliable_position.x == 10
    assert controller.last_position_at == 11


def test_scheduler_zero_timestamp_does_not_bypass_interval():
    scheduler = OCRChannelScheduler()
    assert scheduler.should_submit(now=0.0, min_interval=5)
    scheduler.submitted(request_id=1, now=0.0)
    assert scheduler.completed(request_id=1, now=0.1)
    assert not scheduler.should_submit(now=0.2, min_interval=5)
    assert scheduler.should_submit(now=5.0, min_interval=5)


def test_failed_automatic_resume_clears_stale_track_label():
    controller = DofusicController.__new__(DofusicController)
    controller._audio_override_active = True
    controller._automatic_detection_unlocked = True
    controller._game_process_running = True
    controller._desired_local_track = Path('exploration.mp3')
    controller.player = SimpleNamespace(play=lambda *_a, **_k: False)
    controller.logger = SimpleNamespace(exception=lambda *_a, **_k: None)
    controller.state = SimpleNamespace(music='Morceau manuel', status='', display_theme='')
    assert controller.resume_local_audio() is False
    assert controller.state.music == 'Aucune musique'
    assert 'impossible' in controller.state.status.lower()
