"""Verrous indexés à durée de vie bornée pour les opérations de cache."""
from __future__ import annotations

from contextlib import contextmanager
import threading


class KeyedLockPool:
    """Serialize one key without retaining locks once all users have left."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, tuple[threading.Lock, int]] = {}

    @property
    def active_count(self) -> int:
        with self._guard:
            return len(self._entries)

    @property
    def active_keys(self) -> frozenset[str]:
        with self._guard:
            return frozenset(self._entries)

    @contextmanager
    def hold(self, key: str):
        key = str(key)
        with self._guard:
            current = self._entries.get(key)
            lock, users = current if current is not None else (threading.Lock(), 0)
            self._entries[key] = (lock, users + 1)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._guard:
                current = self._entries.get(key)
                if current is not None and current[0] is lock:
                    remaining = current[1] - 1
                    if remaining <= 0:
                        self._entries.pop(key, None)
                    else:
                        self._entries[key] = (lock, remaining)
