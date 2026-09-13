from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

import dofusic.app as app_module
from dofusic.app import DofusicController, _OCRRuntimeState
from dofusic.capture.dofus_window import CapturedFrame
from dofusic.capture.service import CaptureSnapshot
from dofusic.models import PositionOCRResult


class _Logger:
    def debug(self, *_a, **_k): pass
    def info(self, *_a, **_k): pass
    def warning(self, *_a, **_k): pass
    def error(self, *_a, **_k): pass
    def exception(self, *_a, **_k): pass


class _Gate:
    def __init__(self):
        self.scans = 0
        self.marked = 0

    def should_scan(self, _image, *, now, force=False):
        self.scans += 1
        return True

    def mark_scanned(self, *, now):
        self.marked += 1


class _Worker:
    alive = True

    def __init__(self):
        self.calls = []

    def submit(self, image, *, captured_at):
        self.calls.append((image, captured_at))
        return len(self.calls)


class _CaptureService:
    def __init__(self):
        self.sequence = 0

    def latest(self, _after_sequence, *, now):
        self.sequence += 1
        frame = CapturedFrame(
            image=np.zeros((40, 40, 3), dtype=np.uint8),
            source='printwindow',
            hwnd=123,
            screen_rect=None,
        )
        return CaptureSnapshot(self.sequence, frame, float(now))


def _tick_controller(monkeypatch):
    controller = DofusicController.__new__(DofusicController)
    controller.started = True
    controller._automatic_detection_unlocked = False
    controller._poll_workers = lambda _now: None
    controller.capture_service = _CaptureService()
    controller._last_capture_sequence = 0
    controller.last_capture_seen_at = 0.0
    controller.logger = _Logger()
    controller.hud_geometry = object()
    controller.state = SimpleNamespace(
        window_found=False,
        overlay_hwnd=None,
        combat_overlay_rect=None,
        zone_overlay_rect=None,
        position_overlay_rect=None,
        status='',
    )
    controller.zone_worker = _Worker()
    controller.position_worker = _Worker()
    controller.zone_slot = _OCRRuntimeState()
    controller.position_slot = _OCRRuntimeState()
    controller.zone_change_gate = _Gate()
    controller.position_change_gate = _Gate()
    controller.next_confirmation_at = 0.0
    controller.current_location = None
    controller.last_reliable_position = None
    controller._accepted_position_result = None
    controller.config = SimpleNamespace(
        capture_missing_grace_sec=1.0,
        zone_ocr_min_interval_sec=0.0,
        position_ocr_min_interval_sec=0.0,
    )
    combat_calls = []
    controller._update_combat_from_toolbar = lambda image: combat_calls.append(image)

    hud = SimpleNamespace(
        combat=np.zeros((4, 4, 3), dtype=np.uint8),
        zone=np.zeros((4, 4, 3), dtype=np.uint8),
        position=np.zeros((4, 4, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(app_module, 'extract_hud_inputs', lambda *_a, **_k: hud)
    return controller, combat_calls


def test_startup_gate_runs_only_position_ocr_until_first_valid_coordinates(monkeypatch):
    controller, combat_calls = _tick_controller(monkeypatch)

    controller.tick(now=10.0)

    assert len(controller.position_worker.calls) == 1
    assert controller.zone_worker.calls == []
    assert combat_calls == []

    controller._automatic_detection_unlocked = True
    controller.tick(now=11.0)

    assert len(controller.zone_worker.calls) == 1
    assert len(combat_calls) == 1


def test_first_valid_position_unlocks_automatic_detection_permanently():
    controller = DofusicController.__new__(DofusicController)
    controller._automatic_detection_unlocked = False
    controller.current_location = None
    controller.context_position = None
    controller.last_reliable_position = None
    controller.last_position_at = 0.0
    controller._accepted_position_result = None
    controller.config = SimpleNamespace(context_position_tolerance=2)
    controller.repository = SimpleNamespace(coordinate_candidates=lambda *_a, **_k: ())
    controller.state = SimpleNamespace(
        position_elapsed_ms=0.0,
        position_text='',
        position_x=None,
        position_y=None,
        position_display='—',
    )
    controller.logger = _Logger()

    controller.handle_position_result(
        PositionOCRResult('', None, None, 0.0, 2.0, 'test'),
        now=10.0,
        observed_at=10.0,
    )
    assert controller.automatic_detection_unlocked is False

    controller.handle_position_result(
        PositionOCRResult('1, -36', 1, -36, 0.99, 2.0, 'test'),
        now=11.0,
        observed_at=11.0,
    )
    assert controller.automatic_detection_unlocked is True

    controller.handle_position_result(
        PositionOCRResult('', None, None, 0.0, 2.0, 'test'),
        now=12.0,
        observed_at=12.0,
    )
    assert controller.automatic_detection_unlocked is True


def test_return_from_manual_audio_resumes_generic_music_before_startup_gate(tmp_path):
    class _Player:
        def __init__(self):
            self.play_calls = []
            self.stop_calls = 0

        def play(self, path, *, loop=True, restart=False):
            self.play_calls.append((Path(path), loop, restart))
            return True

        def stop(self, *, fade_ms=0):
            self.stop_calls += 1

    fallback = tmp_path / 'Musique.mp3'
    controller = DofusicController.__new__(DofusicController)
    controller._automatic_detection_unlocked = False
    controller._audio_override_active = True
    controller._audio_override_source = 'online'
    controller._desired_local_track = fallback
    controller.player = _Player()
    controller.music_library = SimpleNamespace(resolve=lambda *_a, **_k: fallback)
    controller.state = SimpleNamespace(in_combat=False, music='Online', status='', display_theme='')
    controller.logger = _Logger()

    assert controller.resume_local_audio() is True
    assert controller.player.stop_calls == 0
    assert controller.player.play_calls == [(fallback, True, True)]
    assert controller.audio_override_active is False
    assert controller.state.music == 'Musique'



def test_startup_gate_waits_for_dofus_then_plays_only_normal_generic_music(tmp_path):
    class _Component:
        alive = True
        def __init__(self): self.started = 0
        def start(self): self.started += 1

    class _Player:
        def __init__(self): self.calls = []
        def play(self, path, **kwargs):
            self.calls.append((Path(path), kwargs))
            return True

    normal = tmp_path / 'Musique2.mp3'
    controller = DofusicController.__new__(DofusicController)
    controller.started = False
    controller._automatic_detection_unlocked = False
    controller._audio_override_active = False
    controller._audio_override_source = None
    controller._desired_local_track = None
    controller.capture_service = _Component()
    controller.zone_worker = _Component()
    controller.position_worker = _Component()
    controller.zone_slot = _OCRRuntimeState()
    controller.position_slot = _OCRRuntimeState()
    controller.player = _Player()
    controller.music_library = SimpleNamespace(resolve=lambda location, **kwargs: normal if location is None and kwargs.get('combat') is False else None)
    controller.state = SimpleNamespace(
        zone_worker_status='Arrêté', position_worker_status='Arrêté', worker_status='Arrêté',
        music='Aucune musique', status='', display_theme='', in_combat=False,
    )
    controller.logger = _Logger()

    controller.start()

    # App startup alone is silent: automatic components and generic music begin
    # only after the Dofus process gate opens.
    assert controller.player.calls == []
    assert controller.capture_service.started == 0
    assert controller.zone_worker.started == 0
    assert controller.position_worker.started == 0

    controller._automatic_runtime_active = False
    controller._game_process_running = True
    controller._start_automatic_runtime()

    assert controller.player.calls == [(normal, {'loop': True, 'restart': False})]
    assert controller.state.music == 'Musique2'
    assert controller.automatic_detection_unlocked is False


def test_automatic_track_cannot_play_before_first_valid_position(tmp_path):
    class _Player:
        def __init__(self): self.calls = []
        def play(self, path, **kwargs):
            self.calls.append((Path(path), kwargs))
            return True

    track = tmp_path / 'Musique combat.mp3'
    controller = DofusicController.__new__(DofusicController)
    controller._automatic_detection_unlocked = False
    controller._audio_override_active = False
    controller._audio_override_source = None
    controller._desired_local_track = None
    controller.player = _Player()
    controller.state = SimpleNamespace(status='', music='Aucune musique', display_theme='')
    controller.logger = _Logger()

    controller._play_track(track, status='Combat détecté')

    assert controller.player.calls == []
    assert controller._desired_local_track == track


def test_combat_badge_does_not_claim_out_of_combat_before_startup_unlock():
    from dofusic.ui.main_window import combat_badge

    label, role = combat_badge(SimpleNamespace(
        window_found=True,
        automatic_detection_unlocked=False,
        in_combat=False,
    ))

    assert role == 'waiting'
    assert 'attente' in label.casefold()
