from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

from dofusic.config import models_dir
from dofusic.location.coordinates import parse_coordinates, positions_compatible
from dofusic.models import PositionOCRResult, ZoneOCRResult
from dofusic.text import clean_text
from dofusic.vision.position import PositionCandidate, select_position_candidate


Backend = Callable[..., object]


def onnx_runtime_params(cpu_threads: int) -> dict[str, object]:
    """Authoritative RapidOCR/ONNX CPU policy for both OCR channels."""
    return {
        'EngineConfig.onnxruntime.intra_op_num_threads': max(1, int(cpu_threads)),
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'EngineConfig.onnxruntime.enable_cpu_mem_arena': False,
    }


def _output_items(output: object) -> tuple[tuple[str, float], ...]:
    texts = getattr(output, 'txts', None) or ()
    scores = getattr(output, 'scores', None) or ()
    result: list[tuple[str, float]] = []
    for text, score in zip(texts, scores):
        cleaned = clean_text(text)
        if not cleaned:
            continue
        try:
            confidence = max(0.0, min(1.0, float(score)))
        except (TypeError, ValueError):
            continue
        result.append((cleaned, confidence))
    return tuple(result)


def _normalize_position_ocr(text: str) -> str:
    """Normalize common glyph confusions only inside the numeric position slot."""
    value = clean_text(text)
    table = str.maketrans({
        '−': '-', '–': '-', '—': '-',
        'O': '0', 'o': '0',
        'I': '1', 'l': '1', '|': '1',
    })
    return value.translate(table)


class ZoneOCREngine:
    """Low-latency zone OCR for Dofus' fixed single-line HUD slot.

    Production uses PP-OCRv6 Small recognition only on the fixed zone band.
    Keeping one recognition model reduces startup cost and portable size.
    """

    def __init__(
        self,
        *,
        cpu_threads: int = 2,
        fast_accept_confidence: float = 0.76,
        backend: Backend | None = None,
        fast_backend: Backend | None = None,
    ) -> None:
        self.cpu_threads = max(1, int(cpu_threads))
        self.fast_accept_confidence = max(0.0, min(1.0, float(fast_accept_confidence)))
        # ``backend`` remains an alias for tests/older callers.
        self._fast_backend = fast_backend or backend
        self.engine_name = 'RapidOCR/ONNX PP-OCRv6 Small Rec (live)'

    @staticmethod
    def _make_recognizer(*, model_type, model_path: Path, cpu_threads: int):
        from rapidocr import EngineType, OCRVersion, RapidOCR

        params = {
            'Global.width_height_ratio': -1,
            'Global.min_height': 1,
            'Rec.engine_type': EngineType.ONNXRUNTIME,
            'Rec.model_type': model_type,
            'Rec.ocr_version': OCRVersion.PPOCRV6,
            'Rec.model_path': str(model_path),
        }
        params.update(onnx_runtime_params(cpu_threads))
        return RapidOCR(params=params)

    def load(self) -> None:
        if self._fast_backend is not None:
            return
        from rapidocr import ModelType

        model_path = models_dir() / 'PP-OCRv6_rec_small.onnx'
        if not model_path.is_file():
            raise FileNotFoundError(f'Modèle OCR Dofusic absent: {model_path}')
        self._fast_backend = self._make_recognizer(
            model_type=ModelType.SMALL, model_path=model_path, cpu_threads=self.cpu_threads
        )

    @staticmethod
    def _best_text(output: object) -> tuple[str, float]:
        items = _output_items(output)
        usable = [(text, score) for text, score in items if parse_coordinates(text) is None]
        if not usable:
            return '', 0.0
        return max(usable, key=lambda item: (len(item[0]), item[1]))

    @staticmethod
    def _run_recognizer(backend: Backend, crop: np.ndarray) -> tuple[str, float]:
        output = backend(crop, use_det=False, use_cls=False, use_rec=True)
        return ZoneOCREngine._best_text(output)

    def warmup(self) -> None:
        """Load and exercise the single live recognition model."""
        self.load()
        if self._fast_backend is None:
            return
        dummy = np.zeros((34, 512, 3), dtype=np.uint8)
        try:
            self._run_recognizer(self._fast_backend, dummy)
        except Exception:
            # Warm-up failure alone should not prevent the worker from starting.
            pass

    def read(self, image: np.ndarray) -> ZoneOCRResult:
        """Live low-latency read: Small recognition only, exactly one inference."""
        started = time.perf_counter()
        try:
            self.load()
            if self._fast_backend is None or image is None or getattr(image, 'size', 0) == 0:
                raise RuntimeError('Image HUD vide')
            crop = np.ascontiguousarray(image)
            text, confidence = self._run_recognizer(self._fast_backend, crop)
            label = self.engine_name + (' [small-live]' if text else ' [small-live-empty]')
            return ZoneOCRResult(
                text,
                confidence,
                (time.perf_counter() - started) * 1000.0,
                label,
            )
        except Exception as exc:
            return ZoneOCRResult('', 0.0, (time.perf_counter() - started) * 1000.0, self.engine_name, str(exc))



class PositionOCREngine:
    """Fast coordinate OCR isolated from zone recognition.

    Uses PP-OCRv6 Small Rec only (no detector) on the calibrated fixed position
    ROI and combines independent image variants. A bad position can never delay
    or veto zone audio.
    """

    def __init__(
        self,
        *,
        cpu_threads: int = 1,
        variants: tuple[str, ...] = ('color', 'contrast', 'tophat'),
        fast_accept_confidence: float = 0.84,
        backend: Backend | None = None,
    ) -> None:
        self.cpu_threads = max(1, int(cpu_threads))
        self.variants = tuple(variants)
        self.fast_accept_confidence = max(0.0, min(1.0, float(fast_accept_confidence)))
        self._backend = backend
        self._last_coordinates = None
        self.engine_name = 'RapidOCR/ONNX PP-OCRv6 Small Rec (position)'

    def load(self) -> None:
        if self._backend is not None:
            return
        from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

        model_path = models_dir() / 'PP-OCRv6_rec_small.onnx'
        if not model_path.is_file():
            raise FileNotFoundError(f'Modèle OCR Dofusic absent: {model_path}')
        params = {
            'Global.width_height_ratio': -1,
            'Global.min_height': 1,
            'Rec.engine_type': EngineType.ONNXRUNTIME,
            'Rec.model_type': ModelType.SMALL,
            'Rec.ocr_version': OCRVersion.PPOCRV6,
            'Rec.model_path': str(model_path),
        }
        params.update(onnx_runtime_params(self.cpu_threads))
        self._backend = RapidOCR(params=params)

    def warmup(self) -> None:
        self.load()
        if self._backend is None:
            return
        try:
            dummy = np.zeros((29, 78, 3), dtype=np.uint8)
            prepared = self._variant_image(dummy, 'color')
            self._backend(prepared, use_det=False, use_cls=False, use_rec=True)
        except Exception:
            pass

    @staticmethod
    def _variant_image(crop: np.ndarray, variant: str) -> np.ndarray:
        if variant == 'color':
            result = crop
        else:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
            if variant == 'contrast':
                clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(4, 4))
                enhanced = clahe.apply(gray)
                result = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            elif variant == 'tophat':
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
                opened = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel)
                top = cv2.subtract(gray, opened)
                top = cv2.normalize(top, None, 0, 255, cv2.NORM_MINMAX)
                _thr, binary = cv2.threshold(top, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                # OCR tends to be more stable with dark glyphs on a light field.
                result = cv2.cvtColor(255 - binary, cv2.COLOR_GRAY2BGR)
            else:
                result = crop
        # Tiny HUD text benefits from modest enlargement; it remains cheap because
        # the crop is only 180x29 pixels at the reference resolution.
        return cv2.resize(result, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    def read(self, image: np.ndarray) -> PositionOCRResult:
        started = time.perf_counter()
        try:
            self.load()
            if self._backend is None or image is None or getattr(image, 'size', 0) == 0:
                raise RuntimeError('Image HUD vide')
            crop = np.ascontiguousarray(image)
            if crop.size == 0:
                raise RuntimeError('Crop position vide')

            candidates: list[PositionCandidate] = []
            raw_seen = ''
            context_fallback_triggered = False
            for index, variant in enumerate(self.variants):
                prepared = self._variant_image(crop, variant)
                output = self._backend(prepared, use_det=False, use_cls=False, use_rec=True)
                current: list[PositionCandidate] = []
                for raw_text, confidence in _output_items(output):
                    raw_seen = raw_seen or raw_text
                    normalized = _normalize_position_ocr(raw_text)
                    coordinates = parse_coordinates(normalized)
                    if coordinates is not None:
                        candidate = PositionCandidate(raw_text, coordinates, confidence, variant)
                        candidates.append(candidate)
                        current.append(candidate)

                # Normal path is a single recognition call. Fallback image variants
                # are paid only when the first read is missing/implausible/weak.
                if index == 0 and current:
                    best_fast = max(current, key=lambda c: c.confidence)
                    context_suspicious = (
                        self._last_coordinates is not None
                        and not positions_compatible(best_fast.coordinates, self._last_coordinates, tolerance=2)
                    )
                    context_fallback_triggered = bool(context_suspicious)
                    if best_fast.confidence >= self.fast_accept_confidence and not context_suspicious:
                        self._last_coordinates = best_fast.coordinates
                        elapsed = (time.perf_counter() - started) * 1000.0
                        return PositionOCRResult(
                            raw_text=best_fast.raw_text,
                            x=best_fast.coordinates.x,
                            y=best_fast.coordinates.y,
                            confidence=best_fast.confidence,
                            elapsed_ms=elapsed,
                            engine=self.engine_name + ' [fast]',
                            variant=best_fast.variant,
                        )

            chosen = select_position_candidate(candidates)
            elapsed = (time.perf_counter() - started) * 1000.0
            if chosen is None:
                return PositionOCRResult(raw_seen, None, None, 0.0, elapsed, self.engine_name)
            used_context_fallback = context_fallback_triggered
            # Consensus is authoritative even for a real teleport/jump; remember it so
            # subsequent normal reads return to the one-call fast path immediately.
            self._last_coordinates = chosen.coordinates
            return PositionOCRResult(
                raw_text=chosen.raw_text,
                x=chosen.coordinates.x,
                y=chosen.coordinates.y,
                confidence=chosen.confidence,
                elapsed_ms=elapsed,
                engine=self.engine_name + (' [context fallback]' if used_context_fallback else ''),
                variant=chosen.variant,
            )
        except Exception as exc:
            return PositionOCRResult('', None, None, 0.0, (time.perf_counter() - started) * 1000.0, self.engine_name, error=str(exc))
