from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print('[V25] Préparation du moteur PP-OCRv6 Small...')
    try:
        import cv2
        import numpy as np
        from dofusic.vision.engines import PositionOCREngine, ZoneOCREngine
        from dofusic.vision.layout import HUDGeometry, extract_hud_inputs

        zone = ZoneOCREngine(cpu_threads=2)
        position = PositionOCREngine(cpu_threads=1)
        zone.warmup()
        position.warmup()

        geometry = HUDGeometry()
        probe = np.zeros((geometry.capture_height, geometry.capture_width, 3), dtype=np.uint8)
        cv2.putText(probe, 'Bonta (Coeur immacule)', (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2, cv2.LINE_AA)
        cv2.putText(probe, '-31,-56', (4, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 1, cv2.LINE_AA)
        hud = extract_hud_inputs(probe, geometry)
        zr = zone.read(hud.zone)
        pr = position.read(hud.position)
        if zr.error or pr.error:
            raise RuntimeError(f'zone={zr.error!r} position={pr.error!r}')
        print(f'[OK] Zone engine: {zr.engine}')
        print(f'[OK] Position engine: {pr.engine}')
        return 0
    except Exception as exc:
        print(f'[ERREUR] Préparation modèles: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
