"""Streaming FFT and loudness functions, independent of playback and FFmpeg."""
from __future__ import annotations

import numpy as np

SPECTRUM_BANDS = 24
SPECTRUM_FPS = 15
SPECTRUM_SAMPLE_RATE = 16000
SPECTRUM_MIN_HZ = 60.0
SPECTRUM_MAX_HZ = 16000.0


LOUDNESS_TARGET_DBFS = -16.0
LOUDNESS_MIN_GAIN = 0.25
LOUDNESS_MAX_GAIN = 4.0


def loudness_gain_from_dbfs(
    dbfs: float | None,
    *,
    target_dbfs: float = LOUDNESS_TARGET_DBFS,
    min_gain: float = LOUDNESS_MIN_GAIN,
    max_gain: float = LOUDNESS_MAX_GAIN,
) -> float:
    """Return a bounded linear gain bringing a track near one common loudness.

    The estimate is computed from the same mono PCM stream already decoded for
    the visualizer, so normalization does not launch a second decoder.
    """
    if dbfs is None:
        return 1.0
    try:
        value = float(dbfs)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(value):
        return 1.0
    lower = max(0.01, float(min_gain))
    upper = max(lower, float(max_gain))
    gain = 10.0 ** ((float(target_dbfs) - value) / 20.0)
    return float(max(lower, min(upper, gain)))


def _spectrum_layout(sample_rate: int, bands: int, fps: int):
    bands = max(1, int(bands))
    fps = max(1, int(fps))
    sample_rate = max(1, int(sample_rate))
    frame_size = 2048 if sample_rate >= 22050 else 1024
    hop = max(1, int(round(sample_rate / float(fps))))
    window = np.hanning(frame_size).astype(np.float32)
    frequencies = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    high_hz = min(SPECTRUM_MAX_HZ, sample_rate * 0.49)
    low_hz = min(SPECTRUM_MIN_HZ, max(10.0, high_hz * 0.5))
    if high_hz <= low_hz:
        high_hz = max(low_hz + 1.0, sample_rate * 0.49)
    edges = np.geomspace(low_hz, high_hz, bands + 1)
    band_slices: list[tuple[int, int]] = []
    for index in range(bands):
        left = int(np.searchsorted(frequencies, edges[index], side='left'))
        right = int(np.searchsorted(frequencies, edges[index + 1], side='right'))
        left = max(1, min(left, len(frequencies) - 1))
        right = max(left + 1, min(right, len(frequencies)))
        band_slices.append((left, right))
    # Resolve band boundaries once per track, not for every FFT frame.
    bounds = np.asarray(band_slices, dtype=np.intp)
    return frame_size, hop, window, bounds


def _spectrum_row(frame: np.ndarray, window: np.ndarray, band_slices: np.ndarray) -> np.ndarray:
    """Band RMS with a single prefix sum instead of 24 Python reductions.

    Bands are allowed to overlap at their boundaries, so simple reduceat is
    not equivalent. The prefix sum preserves those overlapping bins exactly.
    """
    magnitude = np.abs(np.fft.rfft(frame * window)).astype(np.float32, copy=False)
    power = magnitude * magnitude
    prefix = np.empty(power.size + 1, dtype=np.float64)
    prefix[0] = 0.0
    np.cumsum(power, dtype=np.float64, out=prefix[1:])
    left = band_slices[:, 0]
    right = band_slices[:, 1]
    mean_power = np.maximum(0.0, (prefix[right] - prefix[left]) / (right - left))
    return np.sqrt(mean_power).astype(np.float32)


def _normalize_spectrum(raw: np.ndarray) -> np.ndarray:
    reference = float(np.percentile(raw, 98.5)) if raw.size else 0.0
    if reference <= 1e-9:
        return np.zeros_like(raw, dtype=np.float32)
    normalized = np.clip(raw / reference, 0.0, 1.25)
    normalized = np.log1p(normalized * 6.0) / np.log(7.0)
    return np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)


def compute_spectrum_chunks(
    chunks,
    sample_rate: int,
    *,
    bands: int = SPECTRUM_BANDS,
    fps: int = SPECTRUM_FPS,
) -> tuple[np.ndarray, int]:
    """Compute a spectrum timeline while keeping only a small rolling PCM buffer.

    ``chunks`` may yield mono or multi-channel numpy arrays. Only compact spectrum
    rows are retained; the decoded track itself is never accumulated in RAM.
    """
    bands = max(1, int(bands))
    sample_rate = max(1, int(sample_rate))
    frame_size, hop, window, band_slices = _spectrum_layout(sample_rate, bands, fps)
    pending = np.empty(0, dtype=np.float32)
    rows: list[np.ndarray] = []
    total_samples = 0
    skip_samples = 0

    for chunk in chunks:
        work = np.asarray(chunk)
        if work.size == 0:
            continue
        if work.ndim > 1:
            work = np.mean(work.astype(np.float32, copy=False), axis=1)
        else:
            work = work.astype(np.float32, copy=False).reshape(-1)
        peak = float(np.max(np.abs(work))) if work.size else 0.0
        if not np.isfinite(peak):
            # A broken decoder can yield NaN/Inf. Do not let one invalid PCM
            # sample poison every FFT band and the whole rendered timeline.
            # copy=True ensures borrowed input arrays are never modified.
            work = np.nan_to_num(work, copy=True, nan=0.0, posinf=0.0, neginf=0.0)
            peak = float(np.max(np.abs(work)))
        if peak > 1.5:
            work = work / peak
        total_samples += int(work.size)
        if skip_samples:
            skipped = min(skip_samples, work.size)
            work = work[skipped:]
            skip_samples -= skipped
            if not work.size:
                continue
        if pending.size:
            pending = np.concatenate((pending, work))
        else:
            pending = np.ascontiguousarray(work)

        while pending.size >= frame_size:
            rows.append(_spectrum_row(pending[:frame_size], window, band_slices))
            # At low analysis FPS hop can exceed the FFT window. Preserve the
            # gap across decoder chunks instead of restarting at the next chunk.
            skip_samples = max(0, hop - pending.size)
            pending = pending[hop:]

    if pending.size or not rows:
        frame = np.zeros(frame_size, dtype=np.float32)
        take = min(frame_size, pending.size)
        if take:
            frame[:take] = pending[:take]
        rows.append(_spectrum_row(frame, window, band_slices))

    raw = np.vstack(rows).astype(np.float32, copy=False)
    return _normalize_spectrum(raw), total_samples
