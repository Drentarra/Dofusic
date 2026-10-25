"""Tests for the first-launch freeze fixed in 1.0.15 pre-main.

These tests exercise the actual serialized lifecycle with deterministic child
process stand-ins; native Win32 process launch still requires Windows QA.
"""
from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import dofusic.app as app_module
from dofusic.config import AppConfig
from dofusic.ui.main_window import MainWindow, detection_badge
from dofusic.vision.runtime import OCRRuntimeSupervisor


def eventually(predicate, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError('worker lifecycle did not converge')


class FakeOCR:
    def __init__(self, *, channel='zone', cpu_threads=1, wait=None, fail=False):
        self.channel = channel
        self.wait = wait
        self.fail = fail
        self.alive = False
        self.starts = 0
        self.closes = 0
        self.start_thread = None
        self.entered = threading.Event()

    def start(self):
        self.starts += 1
        self.start_thread = threading.get_ident()
        self.entered.set()
        if self.wait is not None:
            assert self.wait.wait(3.0), 'test did not unblock OCR start'
        if self.fail:
            raise RuntimeError('simulated spawn error')
        self.alive = True

    def close(self):
        self.closes += 1
        self.alive = False

    def restart(self):
        self.close()
        self.start()

    def poll(self):
        return []


def fake_supervisor(zone, position):
    return OCRRuntimeSupervisor({'zone': zone, 'position': position})


def tear_down(supervisor):
    thread = supervisor.shutdown()
    if thread is not None:
        thread.join(timeout=4.0)
        assert not thread.is_alive()


def test_ocr_creation_does_not_block_ui_caller():
    gate = threading.Event()
    zone, position = FakeOCR(wait=gate), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    parent = threading.get_ident()
    try:
        supervisor.start()
        assert zone.entered.wait(1.0)
        # The start call returned even though the native-process stand-in is blocked.
        assert zone.start_thread != parent
        assert supervisor.pending('position')
        assert not position.alive
        gate.set()
        eventually(lambda: zone.alive and position.alive)
    finally:
        gate.set()
        tear_down(supervisor)
    assert not zone.alive and not position.alive


def test_stop_during_incomplete_spawn_closes_partially_started_workers():
    gate = threading.Event()
    zone, position = FakeOCR(wait=gate), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    try:
        supervisor.start()
        assert zone.entered.wait(1.0)
        supervisor.stop()
        gate.set()
        eventually(lambda: zone.closes > 0)
        assert not zone.alive and not position.alive
        assert position.starts == 0
    finally:
        gate.set()
        tear_down(supervisor)


def test_shutdown_during_initialization_eventually_releases_resources():
    gate = threading.Event()
    zone, position = FakeOCR(wait=gate), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    supervisor.start()
    assert zone.entered.wait(1.0)
    thread = supervisor.shutdown()  # returns without waiting for the worker
    assert thread is not None and thread.is_alive()
    gate.set()
    thread.join(timeout=4.0)
    assert not thread.is_alive()
    assert zone.closes == 1
    assert position.starts == 0
    assert supervisor.phase == 'stopped'


def test_repeated_start_and_restart_are_serialized():
    zone, position = FakeOCR(), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    try:
        supervisor.start()
        supervisor.start()
        supervisor.start()
        eventually(lambda: zone.alive and position.alive)
        assert zone.starts == position.starts == 1
        supervisor.restart('zone')
        supervisor.restart('zone')
        eventually(lambda: zone.starts == 2)
        assert position.starts == 1
        assert zone.closes >= 1
    finally:
        tear_down(supervisor)


def test_start_error_is_recoverable_and_logged_to_ui_queue():
    zone, position = FakeOCR(fail=True), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    try:
        supervisor.start()
        eventually(lambda: position.alive and bool(zone.starts))
        errors = []
        eventually(lambda: bool(errors.extend(supervisor.poll_errors()) or errors))
        assert errors[0][0] == 'zone'
        assert 'simulated spawn error' in errors[0][1]
        zone.fail = False
        supervisor.restart('zone')
        eventually(lambda: zone.alive)
    finally:
        tear_down(supervisor)


def test_game_detected_badge_is_not_false_waiting_during_capture_warmup():
    state = SimpleNamespace(game_detected=True, window_found=False, worker_status='Démarrage OCR')
    assert detection_badge(state)[0] == 'Initialisation…'
    state.game_detected = False
    assert detection_badge(state)[0] == 'En attente de Dofus.exe'


def test_controller_queues_real_start_instead_of_calling_worker_inline(monkeypatch, tmp_path):
    """Capture=None follows production code, with injected blocking OCR stand-ins."""
    gate = threading.Event()
    zone = FakeOCR(wait=gate)
    position = FakeOCR(channel='position')
    monkeypatch.setattr(app_module, 'OCRWorkerClient', FakeOCR)

    class Library:
        def resolve(self, *_args, **_kwargs):
            return None

    class Player:
        muted = False
        def set_muted(self, value):
            self.muted = value
        def stop(self, **_kwargs):
            pass
        def close(self):
            pass

    class Capture:
        def __init__(self):
            self.starts = 0
            self.closes = 0
        def start(self):
            self.starts += 1
        def close(self):
            self.closes += 1

    controller = app_module.DofusicController(
        AppConfig(), player=Player(), music_library=Library(),
        zone_worker=zone, position_worker=position,
        process_probe=lambda _process: True,
    )
    controller.capture_service = Capture()
    try:
        controller.start()
        assert controller._sync_game_process(time.monotonic()) is True
        assert controller.state.game_detected is True
        assert zone.entered.wait(1.0)
        assert not position.alive
        gate.set()
        eventually(lambda: position.alive)
        controller._sync_game_process(time.monotonic())
        assert controller.capture_service.starts >= 1
    finally:
        gate.set()
        controller.close()
        thread = controller._ocr_supervisor._thread
        if thread is not None:
            thread.join(timeout=4.0)
        assert thread is None or not thread.is_alive()


def test_window_closes_before_capture_cleanup_finishes(monkeypatch, tmp_path):
    order = []
    class Controller:
        _ocr_supervisor = object()
        player = SimpleNamespace(volume=50, muted=False)
        logger = logging.getLogger('test-window-shutdown')
        def close_async(self):
            order.append('controller background shutdown')
            return None

    class Root:
        def destroy(self):
            order.append('destroy Tk')

    window = MainWindow.__new__(MainWindow)
    window.controller = Controller()
    window.root = Root()
    window.config = SimpleNamespace(volume=30, mute=False)
    window.config_file = tmp_path / 'config.json'
    window.music_session = None
    window.overlay = SimpleNamespace(close=lambda: order.append('overlay'))
    window._closed = False
    monkeypatch.setattr('dofusic.ui.main_window.save_config', lambda *_args: None)
    window.close()
    assert order == ['overlay', 'controller background shutdown', 'destroy Tk']


def test_supervisor_restarts_cleanly_after_game_process_reappears():
    zone, position = FakeOCR(), FakeOCR(channel='position')
    supervisor = fake_supervisor(zone, position)
    try:
        supervisor.start()
        eventually(lambda: zone.alive and position.alive)
        supervisor.stop()
        eventually(lambda: zone.closes == 1 and position.closes == 1)
        supervisor.start()
        eventually(lambda: zone.alive and position.alive and zone.starts == 2)
        assert position.starts == 2
    finally:
        tear_down(supervisor)


def test_window_is_destroyed_if_native_shutdown_raises(monkeypatch, tmp_path):
    order = []
    class Controller:
        _ocr_supervisor = object()
        player = SimpleNamespace(volume=80, muted=True)
        logger = logging.getLogger('test-window-error-shutdown')
        def close_async(self):
            raise RuntimeError('simulated native close failure')

    window = MainWindow.__new__(MainWindow)
    window.controller = Controller()
    window.root = SimpleNamespace(destroy=lambda: order.append('destroy'))
    window.config = SimpleNamespace(volume=30, mute=False)
    window.config_file = tmp_path / 'config.json'
    window.music_session = None
    window.overlay = SimpleNamespace(close=lambda: order.append('overlay'))
    window._closed = False
    monkeypatch.setattr('dofusic.ui.main_window.save_config', lambda *_args: None)
    window.close()
    assert order == ['overlay', 'destroy']
    assert window._closed is True
