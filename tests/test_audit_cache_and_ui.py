"""Contrôles de sécurité des médias et d'inactivité de l'interface."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dofusic.ui.main_window import MainWindow
from dofusic.vision.workers import OCRWorkerClient


def test_visualizer_does_not_repeat_identical_tk_canvas_commands():
    class Canvas:
        def __init__(self):
            self.calls = []

        def coords(self, bar, *coordinates):
            self.calls.append(('coords', bar, coordinates))
            return (bar * 10, 0, bar * 10 + 5, 20)

        def itemconfigure(self, bar, **options):
            self.calls.append(('itemconfigure', bar, options))

    window = MainWindow.__new__(MainWindow)
    window.canvas = Canvas()
    window.palette = SimpleNamespace(track_active='green', visualizer_idle='gray')
    window._visualizer_bars = (1, 2)
    window._visualizer_max_height = 40
    window._visualizer_baseline = 60

    window._redraw_visualizer((0.0, 0.0))
    first_count = len(window.canvas.calls)
    assert first_count == 6
    window._redraw_visualizer((0.0, 0.0))
    assert len(window.canvas.calls) == first_count
    window._visualizer_last_drawn = {}
    window._redraw_visualizer((0.0, 0.0))
    assert len(window.canvas.calls) == first_count * 2


def test_ocr_worker_close_does_not_join_blocked_queue_feeders():
    class DeadProcess:
        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    class Queue:
        def __init__(self):
            self.calls = []

        def cancel_join_thread(self):
            self.calls.append('cancel')

        def close(self):
            self.calls.append('close')

        def join_thread(self):
            raise AssertionError('Blocking queue join must not be used')

    client = OCRWorkerClient(channel='position')
    requests = Queue()
    events = Queue()
    client._process = DeadProcess()
    client._requests = requests
    client._events = events
    client.close()
    assert requests.calls == ['cancel', 'close']
    assert events.calls == ['cancel', 'close']
    assert client._process is None


