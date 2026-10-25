"""Régressions de cycle de vie et de réduction des accès SQLite."""
from __future__ import annotations

import threading
import time

from dofusic.capture.service import CaptureService
from dofusic.location.resolver import LocationResolver
from dofusic.models import Coordinates, LocationKind, LocationRecord


def _await(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_capture_recovers_after_backend_initialization_failure():
    created = []
    attempts = []

    class Backend:
        def capture(self):
            return None

        def close(self):
            created.append('closed')

    def factory():
        attempts.append('attempt')
        if len(attempts) == 1:
            raise OSError('capture indisponible')
        return Backend()

    service = CaptureService(factory, fps=30)
    service.start()
    try:
        assert _await(lambda: len(attempts) >= 2)
        assert _await(lambda: service.latest() is not None and service.latest().sequence >= 2)
    finally:
        service.close()
    assert len(attempts) == 2
    assert created == ['closed']


def test_capture_does_not_start_second_thread_after_close_timeout():
    entered = threading.Event()
    release = threading.Event()
    creations = []

    class Backend:
        def capture(self):
            entered.set()
            release.wait(2)
            return None

        def close(self):
            pass

    def factory():
        creations.append(1)
        return Backend()

    service = CaptureService(factory, fps=30)
    service.start()
    try:
        assert entered.wait(1)
        service.close(timeout=0)
        assert service.alive
        service.start()
        assert len(creations) == 1
    finally:
        release.set()
        service.close(timeout=2)
    assert not service.alive


def test_location_coordinate_context_reuses_identical_queries():
    calls = []
    location = LocationRecord(42, 'Astrub', LocationKind.SUBAREA, 2, 'Amakna')

    class Repository:
        def all_locations(self):
            return (location,)

        def place_aliases(self):
            return ()

        def coordinate_candidates(self, coordinates, tolerance=0):
            calls.append((coordinates, tolerance))
            return (location,)

    resolver = LocationResolver(Repository())
    pos = Coordinates(4, -12)
    first = resolver._coordinate_context(pos)
    second = resolver._coordinate_context(Coordinates(4, -12))
    assert first is second
    assert len(calls) == 2
    resolver._coordinate_context(Coordinates(5, -12))
    assert len(calls) == 4
