"""Benchmarks reproductibles, sans jeu actif ni métriques inventées.

Usage :
  PYTHONPATH=Data python Data/tools/benchmark_runtime.py --db Data/dofus_data.sqlite --output Audit/apres.json
  python Data/tools/benchmark_runtime.py --compare Audit/avant.json Audit/apres.json --output Audit/COMPARAISON.md
La collecte CPU/RAM ne représente que ce scénario synthétique.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path


def _rss_bytes() -> int | None:
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
    import cv2
    import numpy as np

    from dofusic.location.repository import DofusRepository
    from dofusic.location.resolver import LocationResolver
    from dofusic.models import Coordinates
    from dofusic.vision.layout import HUDGeometry, extract_hud_inputs

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

    source = cv2.imread(str(fixture), cv2.IMREAD_COLOR)
    if source is None or source.size == 0:
        raise FileNotFoundError(fixture)

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
            values = _timed(lambda: engine.read(crop), 5)
            ocr[name] = {
                'initialize_and_warmup_ms': round(ready_ms, 3),
                'read': values,
                'last_error': engine.read(crop).error,
            }
        result['ocr'] = ocr
        result['rss_bytes_after_ocr'] = _rss_bytes()
    return result


def compare(before: Path, after: Path) -> str:
    old = json.loads(before.read_text(encoding='utf-8'))
    new = json.loads(after.read_text(encoding='utf-8'))
    result = [
        '# Comparaison technique v1.0.11 / branche audit',
        '',
        'Même machine GitHub Actions, même base SQLite et même image de HUD.',
        'Les mesures sont synthétiques et ne représentent pas la consommation pendant Dofus.',
        '',
        '| Mesure | v1.0.11 | Branche audit |',
        '|---|---:|---:|',
    ]
    def row(label, before_value, after_value, unit=''):
        result.append(f'| {label} | {before_value}{unit} | {after_value}{unit} |')

    row('Initialisation resolver', old['startup_resolver_ms'], new['startup_resolver_ms'], ' ms')
    for key, name in (('location', 'Résolution géographique'), ('hud_extract', 'Extraction HUD')):
        row(f'{name} (moyenne)', old[key]['wall_mean_ms'], new[key]['wall_mean_ms'], ' ms')
        row(f'{name} (p95)', old[key]['wall_p95_ms'], new[key]['wall_p95_ms'], ' ms')
        row(f'{name} (CPU total)', old[key]['total_cpu_ms'], new[key]['total_cpu_ms'], ' ms')
    row('Requêtes SQLite sur position constante', old['sqlite_coordinate_queries'], new['sqlite_coordinate_queries'])
    for key in ('rss_bytes', 'rss_bytes_after_ocr'):
        row(key, old.get(key, 'non mesuré'), new.get(key, 'non mesuré'))
    for channel in ('zone', 'position'):
        if channel in old.get('ocr', {}) and channel in new.get('ocr', {}):
            row(f'OCR {channel}, warmup', old['ocr'][channel]['initialize_and_warmup_ms'],
                new['ocr'][channel]['initialize_and_warmup_ms'], ' ms')
            row(f'OCR {channel}, moyenne', old['ocr'][channel]['read']['wall_mean_ms'],
                new['ocr'][channel]['read']['wall_mean_ms'], ' ms')
    result.append('')
    result.append(f"Clé reconnue avant : `{old['recognized_key']}`; après : `{new['recognized_key']}`.")
    result.append('Les écarts de latence sont indicatifs : ordonnanceur, caches du système et charge de la machine peuvent varier.')
    return '\n'.join(result) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path)
    parser.add_argument('--fixture', type=Path, default=Path('tests/fixtures/hud/astrub_6_-23_level15.png'))
    parser.add_argument('--models', type=Path)
    parser.add_argument('--count', type=int, default=80)
    parser.add_argument('--compare', nargs=2, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.compare:
        payload = compare(*args.compare)
    else:
        if args.db is None:
            parser.error('--db est obligatoire pour un benchmark')
        payload = json.dumps(measure(args.db.resolve(), args.fixture.resolve(),
                                     args.models, max(5, args.count)), ensure_ascii=False, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding='utf-8')
    print(f'[AUDIT] Résultat : {args.output}')


if __name__ == '__main__':
    main()
