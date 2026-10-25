from __future__ import annotations

import argparse
import importlib
import logging
import multiprocessing
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from dofusic.version import __version__

    parser = argparse.ArgumentParser(description=f'Dofusic {__version__}')
    parser.add_argument('--version', action='version', version=f'Dofusic {__version__}')
    parser.add_argument('--self-test', action='store_true', help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run_self_test() -> int:
    """Verify the offline distribution without starting the UI."""
    from dofusic.config import database_path, models_dir

    required_modules = (
        'rapidocr', 'onnxruntime', 'numpy', 'cv2', 'rapidfuzz', 'mss',
        'PIL', 'pygame', 'imageio_ffmpeg',
        'win32gui', 'win32ui', 'win32con', 'win32process', 'tkinter',
    )
    try:
        for name in required_modules:
            importlib.import_module(name)
        database = database_path()
        if not database.is_file():
            return 41
        models = models_dir()
        if not (models / 'PP-OCRv6_rec_small.onnx').is_file():
            return 42

        import imageio_ffmpeg
        if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():
            return 43

        from dofusic.location.repository import DofusRepository
        repository = DofusRepository(database)
        if repository.integrity_problems():
            return 44

        from dofusic.vision.engines import PositionOCREngine, ZoneOCREngine
        ZoneOCREngine(cpu_threads=1).warmup()
        PositionOCREngine(cpu_threads=1).warmup()
        return 0
    except Exception:
        return 49


def main() -> int:
    # Neither this application nor its Python dependencies may emit diagnostic logs.
    logging.disable(logging.CRITICAL)
    sys.dont_write_bytecode = True
    multiprocessing.freeze_support()
    args = _parse_args()
    if args.self_test:
        return _run_self_test()

    from dofusic.config import config_path, database_path, load_config

    cfg_path = config_path()
    cfg = load_config(cfg_path)
    if os.name != 'nt':
        print('Dofusic nécessite Windows pour capturer Dofus.exe.', file=sys.stderr)
        return 2
    if not database_path().is_file():
        print('Base Dofus absente.', file=sys.stderr)
        return 3

    from dofusic.capture.dofus_window import enable_dpi_awareness
    enable_dpi_awareness()

    from dofusic.app import DofusicController
    from dofusic.ui.main_window import MainWindow

    controller = DofusicController(cfg)
    MainWindow(controller, cfg, config_file=cfg_path).run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
