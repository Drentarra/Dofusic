"""Compare identical CPU scenarios against a selected source tree, without OCR models.

Run this same script twice with --source-root pointing to each version. These
measurements exclude Windows capture, model inference and real audio playback.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import marshal
import platform
import statistics
import sys
import time
from pathlib import Path


def measure(call, *, iterations: int, repeats: int = 5) -> dict[str, object]:
    call()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(iterations):
            call()
        samples.append((time.perf_counter() - started) * 1000 / iterations)
    return {
        'median_ms_per_call': statistics.median(samples),
        'batch_ms_per_call': samples,
        'iterations_per_batch': iterations,
        'batches': repeats,
    }


def benchmark(root: Path, *, iterations: int) -> dict[str, object]:
    # Must precede application imports so both versions run the same scenarios.
    sys.path.insert(0, str(root / 'Data'))
    import numpy as np

    from dofusic.audio.spectrum import compute_spectrum_chunks
    from dofusic.location.repository import DofusRepository
    from dofusic.location.resolver import LocationResolver
    from dofusic.models import Coordinates
    from dofusic.version import __version__
    from dofusic.vision.layout import HUDGeometry, HUDTransform, extract_hud_inputs

    repository = DofusRepository(root / 'Data/dofus_data.sqlite')
    resolver = LocationResolver(repository)
    position = Coordinates(6, -23)
    def repeated():
        return resolver.resolve('Astrub', 0.99, position, timestamp=1000.0)
    repeated_metrics = measure(repeated, iterations=iterations)
    assert repeated().top1.location.canonical_key == 'area:18'

    names = ('Astrub', 'Amakna', 'Bonta', 'Brâkmar', 'Incarnam', 'Sufokia')
    index = 0

    def varying():
        nonlocal index
        result = resolver.resolve(names[index % len(names)], 0.99, position, timestamp=1000.0)
        index += 1
        return result

    varying_metrics = measure(varying, iterations=iterations)
    image = np.zeros((160, 512, 3), dtype=np.uint8)
    image[45:70, 8:160] = 220
    geometry, transform = HUDGeometry(), HUDTransform()
    hud_metrics = measure(
        lambda: extract_hud_inputs(image, geometry, transform=transform),
        iterations=iterations,
    )
    sample_rate = 22050
    samples = np.sin(np.arange(sample_rate * 3, dtype=np.float32) * (2 * np.pi * 440 / sample_rate))
    chunks = tuple(samples[start:start + 4096] for start in range(0, samples.size, 4096))
    spectrum_metrics = measure(
        lambda: compute_spectrum_chunks(chunks, sample_rate), iterations=10,
    )

    paths = sorted((root / 'Data/dofusic').rglob('*.py')) + [root / 'Data/main.py']
    source_bytes = bytecode_bytes = 0
    for path in paths:
        data = path.read_bytes()
        source_bytes += len(data)
        # Stable relative filenames; size is Python bytecode, not a Windows EXE.
        code = compile(data, path.relative_to(root).as_posix(), 'exec', optimize=2)
        bytecode_bytes += len(marshal.dumps(code))
    return {
        'version': __version__,
        'python': platform.python_version(),
        'platform': platform.platform(),
        'dependencies': {
            name: importlib.metadata.version(name)
            for name in ('numpy', 'opencv-python-headless', 'rapidfuzz')
        },
        'location_repeated': repeated_metrics,
        'location_varying': varying_metrics,
        'hud_explicit_transform': hud_metrics,
        'spectrum_3_seconds': spectrum_metrics,
        'runtime_source_files': len(paths),
        'runtime_source_bytes': source_bytes,
        'runtime_marshaled_bytecode_bytes': bytecode_bytes,
        'limitations': 'Synthetic Linux/Windows CPU scenarios; no live capture, OCR inference or compiled EXE measurement.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--iterations', type=int, default=200)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error('--iterations must be positive')
    result = benchmark(args.source_root.resolve(), iterations=args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
