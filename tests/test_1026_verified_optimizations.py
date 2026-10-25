"""Verified numerical equivalence, audio hardening and bounded temp storage."""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

from dofusic.audio.decode import AudioDecodeMixin
from dofusic.audio.spectrum import compute_spectrum_chunks
from dofusic.location.repository import DofusRepository
from dofusic.models import LocationKind


def _reference_spectrum(chunks, sample_rate: int, bands: int, fps: int):
    """Slow reference band RMS, retained in tests to detect math drift."""
    from dofusic.audio.spectrum import _normalize_spectrum, _spectrum_layout

    size, hop, window, bounds = _spectrum_layout(sample_rate, bands, fps)
    # Deliberately offline reference: concatenate samples, then use absolute
    # hop positions. Sharing the streaming buffer logic hid a chunk-gap bug.
    mono = []
    for chunk in chunks:
        work = np.asarray(chunk, dtype=np.float32)
        if not work.size:
            continue
        work = work.mean(axis=1) if work.ndim > 1 else work.reshape(-1)
        peak = float(np.max(np.abs(work)))
        mono.append(work / peak if peak > 1.5 else work)
    samples = np.concatenate(mono) if mono else np.empty(0, dtype=np.float32)
    starts = list(range(0, max(0, len(samples) - size + 1), hop))
    next_start = starts[-1] + hop if starts else 0
    if next_start < len(samples) or not starts:
        starts.append(next_start)
    rows = []
    for start in starts:
        frame = np.zeros(size, dtype=np.float32)
        segment = samples[start:start + size]
        frame[:len(segment)] = segment
        magnitude = np.abs(np.fft.rfft(frame * window)).astype(np.float32)
        rows.append(np.array([
            float(np.sqrt(np.mean(magnitude[left:right] * magnitude[left:right])))
            for left, right in bounds
        ], dtype=np.float32))
    return _normalize_spectrum(np.vstack(rows)), len(samples)


@pytest.mark.parametrize('channels', [1, 2])
@pytest.mark.parametrize('sample_rate', [8000, 16000, 32000, 44100])
def test_spectrum_optimized_equivalent_to_independent_reference(sample_rate, channels):
    rng = np.random.default_rng(sample_rate + channels)
    signal = rng.uniform(-0.9, 0.9, size=(sample_rate // 2, channels)).astype(np.float32)
    if channels == 1:
        signal = signal.reshape(-1)
    chunks = [signal[start:start + 977] for start in range(0, len(signal), 977)]
    expected, expected_count = _reference_spectrum(chunks, sample_rate, 24, 15)
    actual, count = compute_spectrum_chunks(chunks, sample_rate, bands=24, fps=15)
    assert count == expected_count
    assert actual.shape == expected.shape
    np.testing.assert_allclose(actual, expected, atol=2e-6, rtol=1e-6)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_invalid_pcm_value_does_not_contaminate_timeline_or_mutate_input(value):
    samples = np.sin(np.linspace(0.0, 100.0, 16000)).astype(np.float32)
    samples[120] = value
    original = samples.copy()
    levels, count = compute_spectrum_chunks((samples,), 16000)
    assert count == len(samples)
    assert np.isfinite(levels).all()
    assert levels.max() > 0.05
    np.testing.assert_array_equal(original, samples)


def test_stale_fallback_cleanup_removes_only_owned_old_regular_files(tmp_path):
    old = tmp_path / 'dofusic_orphan.ogg'
    recent = tmp_path / 'dofusic_active.ogg'
    unrelated = tmp_path / 'my_music.ogg'
    other_ext = tmp_path / 'dofusic_not_audio.txt'
    for path in (old, recent, unrelated, other_ext):
        path.write_bytes(b'content')
    now = time.time()
    os.utime(old, (now - 9 * 86400, now - 9 * 86400))
    os.utime(unrelated, (now - 9 * 86400, now - 9 * 86400))
    os.utime(other_ext, (now - 9 * 86400, now - 9 * 86400))
    link = tmp_path / 'dofusic_link.ogg'
    try:
        link.symlink_to(unrelated)
    except (OSError, NotImplementedError):
        link = None
    assert AudioDecodeMixin._cleanup_stale_fallbacks(tmp_path, now=now) == 1
    assert not old.exists()
    assert recent.exists() and unrelated.exists() and other_ext.exists()
    if link is not None:
        assert link.is_symlink()


def test_aliases_resolved_in_one_database_query_without_changing_records(tmp_path, monkeypatch):
    from dofusic.location import repository as repo_module

    path = tmp_path / 'db.sqlite'
    shutil.copy2(Path(__file__).resolve().parents[1] / 'Data' / 'dofus_data.sqlite', path)
    conn = sqlite3.connect(path)
    try:
        area_id = conn.execute('SELECT id FROM areas ORDER BY id LIMIT 1').fetchone()[0]
        subarea_id = conn.execute('SELECT id FROM subareas ORDER BY id LIMIT 1').fetchone()[0]
        entries = [('Alias area', 'area', area_id, 10),
                   ('Alias subarea', 'subarea', subarea_id, 20),
                   ('Orphan', 'area', -100000, 30)]
        conn.executemany('INSERT INTO place_aliases(alias,target_kind,target_id,priority) VALUES (?,?,?,?)', entries)
        conn.commit()
    finally:
        conn.close()

    original = repo_module._connect
    seen = []

    def counted(path):
        seen.append(path)
        return original(path)

    monkeypatch.setattr(repo_module, '_connect', counted)
    result = DofusRepository(path).place_aliases()
    assert len(seen) == 1
    assert [item.alias for item in result] == ['Alias subarea', 'Alias area']
    assert result[0].location.kind is LocationKind.SUBAREA
    assert result[1].location.kind is LocationKind.AREA
    assert result[0].priority == 20 and result[1].priority == 10


def test_direct_area_and_subarea_inventories_preserve_all_locations():
    repo = DofusRepository(Path(__file__).resolve().parents[1] / 'Data' / 'dofus_data.sqlite')
    whole = repo.all_locations()
    assert repo.areas() == tuple(item for item in whole if item.kind is LocationKind.AREA)
    assert repo.subareas() == tuple(item for item in whole if item.kind is LocationKind.SUBAREA)
