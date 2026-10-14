"""Vérification Windows des créations/arrêts de processus OCR sans Dofus."""
from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _events_until(workers, expected, deadline):
    received = {name: [] for name in workers}
    while time.monotonic() < deadline:
        for name, worker in workers.items():
            received[name].extend(worker.poll())
        if all(any(event.kind.value == expected for event in events)
               for events in received.values()):
            return received
        time.sleep(0.03)
    summary = {name: [(item.kind.value, item.message[:150]) for item in events]
               for name, events in received.items()}
    raise RuntimeError(f'Timeout OCR ({expected}): {summary}')


def main() -> int:
    import cv2

    from dofusic.vision.layout import HUDGeometry, extract_hud_inputs
    from dofusic.vision.workers import OCRWorkerClient

    image_file = ROOT.parent / 'tests/fixtures/hud/astrub_6_-23_level15.png'
    image = cv2.imread(str(image_file), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(image_file)
    hud = extract_hud_inputs(image, HUDGeometry())

    for cycle in range(2):
        workers = {
            'zone': OCRWorkerClient(channel='zone', cpu_threads=1),
            'position': OCRWorkerClient(channel='position', cpu_threads=1),
        }
        started = time.monotonic()
        try:
            for worker in workers.values():
                worker.start()
            _events_until(workers, 'ready', started + 30.0)
            for name, worker in workers.items():
                request = worker.submit(hud.zone if name == 'zone' else hud.position)
                if request is None:
                    raise RuntimeError(f'Impossible de soumettre OCR {name}')
            events = _events_until(workers, 'result', time.monotonic() + 30.0)
            for name, items in events.items():
                result = next(event for event in items if event.kind.value == 'result')
                if result.result is None or result.result.error:
                    raise RuntimeError(f'Résultat OCR {name} invalide : {result}')
        finally:
            for worker in workers.values():
                worker.close(timeout=2.0)
        if any(worker.alive for worker in workers.values()):
            raise RuntimeError('Processus OCR encore actif après fermeture')
        print(f'[OK] Cycle OCR {cycle + 1}: {time.monotonic() - started:.3f}s')

    remaining = [item for item in mp.active_children()
                 if item.name.startswith('DofusicOCR-')]
    if remaining:
        raise RuntimeError(f'Processus OCR orphelins : {remaining}')
    return 0


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
