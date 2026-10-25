"""Benchmark synthétique courant des algorithmes, sans dépendance à une ancienne release.

Exemple: PYTHONPATH=Data python Data/tools/benchmark_runtime.py \
    --db Data/dofus_data.sqlite --output Audit/benchmark_current.json
Les mesures CPU/RAM ne représentent pas la charge réelle pendant une partie Dofus.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import statistics
import sys
import threading
import time
from pathlib import Path


def _rss_bytes() -> int | None:
    if sys.platform.startswith('linux'):
        # Current resident pages, not the process' cumulative high-water mark.
        # This is an offline-process metric, not the Windows game workload.
        try:
            with open('/proc/self/statm', encoding='ascii') as handle:
                resident_pages = int(handle.readline().split()[1])
            return resident_pages * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
        ]

    counts = Counters()
    counts.cb = ctypes.sizeof(counts)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counts), counts.cb,
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), 'GetProcessMemoryInfo a échoué')
    return int(counts.WorkingSetSize)


def _timed(fn, count: int) -> dict[str, object]:
    samples = []
    cpu_start = time.process_time()
    for _ in range(count):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    ordered = sorted(samples)
    return {
        'count': count,
        'wall_mean_ms': round(statistics.mean(samples), 4),
        'wall_p50_ms': round(statistics.median(samples), 4),
        'wall_p95_ms': round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 4),
        'total_cpu_ms': round((time.process_time() - cpu_start) * 1000.0, 3),
    }


def measure(db: Path, fixture: Path, models: Path | None, count: int) -> dict[str, object]:
    from dofusic.location.repository import DofusRepository
    from dofusic.location.resolver import LocationResolver
    from dofusic.models import Coordinates
    from dofusic.vision.layout import HUDGeometry
    from tools.hud_reference import extract_reference_hud_inputs as extract_hud_inputs
    from tools.image_io import read_image

    started = time.perf_counter()
    repository = DofusRepository(db)
    resolver = LocationResolver(repository)
    startup_ms = round((time.perf_counter() - started) * 1000.0, 4)

    coordinates = Coordinates(6, -23)
    sql_queries = 0
    original_candidates = repository.coordinate_candidates

    def counted_candidates(*args, **kwargs):
        nonlocal sql_queries
        sql_queries += 1
        return original_candidates(*args, **kwargs)

    repository.coordinate_candidates = counted_candidates
    evidence = None

    def resolve():
        nonlocal evidence
        evidence = resolver.resolve('Astrub', 0.99, coordinates, timestamp=1000.0)

    resolve()
    sql_queries = 0
    location_metrics = _timed(resolve, count)

    source = read_image(fixture)

    hud = extract_hud_inputs(source, HUDGeometry())
    visual_metrics = _timed(lambda: extract_hud_inputs(source, HUDGeometry()), max(12, count // 4))
    result = {
        'python': platform.python_version(),
        'platform': platform.platform(),
        'scenario': 'offline-fixture-astrub-fixed-coordinate',
        'startup_resolver_ms': startup_ms,
        'location': location_metrics,
        'sqlite_coordinate_queries': sql_queries,
        'hud_extract': visual_metrics,
        'recognized_key': evidence.top1.location.canonical_key if evidence and evidence.top1 else None,
        'threads': threading.active_count(),
        'rss_bytes': _rss_bytes(),
    }

    if models is not None:
        from dofusic.vision import engines
        engines.models_dir = lambda: models.resolve()
        zone = engines.ZoneOCREngine(cpu_threads=1)
        position = engines.PositionOCREngine(cpu_threads=1)
        ocr = {}
        for name, engine, crop in (
            ('zone', zone, hud.zone),
            ('position', position, hud.position),
        ):
            init_started = time.perf_counter()
            engine.load()
            engine.warmup()
            ready_ms = (time.perf_counter() - init_started) * 1000.0
            output = engine.read(crop)
            values = _timed(lambda engine=engine, crop=crop: engine.read(crop), 5)
            ocr[name] = {
                'initialize_and_warmup_ms': round(ready_ms, 3),
                'read': values,
                'last_error': output.error,
            }
        result['ocr'] = ocr
        result['rss_bytes_after_ocr'] = _rss_bytes()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path)
    parser.add_argument('--fixture', type=Path, default=Path('tests/fixtures/hud/astrub_6_-23_level15.png'))
    parser.add_argument('--models', type=Path)
    parser.add_argument('--count', type=int, default=80)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.db is None:
        parser.error('--db est obligatoire pour un benchmark')
    payload = json.dumps(measure(args.db.resolve(), args.fixture.resolve(),
                                 args.models, max(5, args.count)), ensure_ascii=False, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding='utf-8')
    print(f'[AUDIT] Résultat : {args.output}')


if __name__ == '__main__':
    main()
