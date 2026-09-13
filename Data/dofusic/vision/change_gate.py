from __future__ import annotations

import cv2
import numpy as np


class OCRChangeGate:
    """Cheap visual gate that avoids OCR when a HUD crop is unchanged.

    A tiny grayscale fingerprint is compared with the last frame that actually
    went through OCR.  A periodic refresh remains mandatory so an animation,
    capture artifact or borderline threshold can never freeze recognition.
    """

    def __init__(self, *, change_threshold: float = 4.0, idle_refresh_sec: float = 2.0) -> None:
        self.change_threshold = max(0.0, float(change_threshold))
        self.idle_refresh_sec = max(0.1, float(idle_refresh_sec))
        self._last_scanned_signature: np.ndarray | None = None
        self._pending_signature: np.ndarray | None = None
        self._last_scanned_at = 0.0

    @staticmethod
    def _signature(image: np.ndarray) -> np.ndarray:
        work = np.asarray(image)
        if work.size == 0:
            return np.zeros((8, 32), dtype=np.uint8)
        if work.ndim == 3:
            work = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        # 256 bytes per comparison: much cheaper than one ONNX inference.
        return cv2.resize(work, (32, 8), interpolation=cv2.INTER_AREA).astype(np.uint8, copy=False)

    def should_scan(self, image: np.ndarray, *, now: float, force: bool = False) -> bool:
        now = float(now)
        signature = self._signature(image)
        self._pending_signature = signature
        if force or self._last_scanned_signature is None or self._last_scanned_at <= 0.0:
            return True
        if now - self._last_scanned_at >= self.idle_refresh_sec:
            return True
        delta = np.abs(signature.astype(np.int16) - self._last_scanned_signature.astype(np.int16))
        return float(np.mean(delta)) >= self.change_threshold

    def mark_scanned(self, *, now: float) -> None:
        if self._pending_signature is not None:
            self._last_scanned_signature = self._pending_signature.copy()
        self._last_scanned_at = float(now)

    def reset(self) -> None:
        self._last_scanned_signature = None
        self._pending_signature = None
        self._last_scanned_at = 0.0
