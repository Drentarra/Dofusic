"""Conservative CI smoke guard for a current synthetic benchmark.

Absolute budgets are deliberately loose to avoid treating hosted-runner CPU noise
as a release regression. This guards logic and pathological slowness, not gaming FPS.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def check(results: dict) -> list[str]:
    problems: list[str] = []
    if results.get('scenario') != 'offline-fixture-astrub-fixed-coordinate':
        problems.append('Scénario de benchmark inattendu')
    if results.get('recognized_key') != 'area:18':
        problems.append('Résolution géographique incorrecte pour Astrub')
    if results.get('sqlite_coordinate_queries') != 0:
        problems.append('Requêtes SQLite répétées pour une position inchangée')
    for key, ceiling_ms in (('location', 25.0), ('hud_extract', 150.0)):
        record = results.get(key) or {}
        try:
            p95 = float(record['wall_p95_ms'])
            count = int(record['count'])
        except (KeyError, ValueError, TypeError):
            problems.append(f'{key}: données p95/count manquantes')
            continue
        if count < 5 or not math.isfinite(p95) or not (0.0 <= p95 < ceiling_ms):
            problems.append(f'{key}: p95={p95} ms, budget < {ceiling_ms} ms')
    ocr = results.get('ocr')
    if ocr is not None:
        for channel in ('zone', 'position'):
            if channel not in ocr or ocr[channel].get('last_error'):
                problems.append(f'OCR {channel}: non exécuté ou erreur')
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    problems = check(json.loads(args.results.read_text(encoding='utf-8')))
    for problem in problems:
        print(f'[ERREUR BENCHMARK] {problem}')
    if not problems:
        print('[OK] Benchmark courant: clé, cache SQL et budgets vérifiés')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
