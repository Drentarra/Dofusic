from __future__ import annotations

import argparse
import importlib
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    from dofusic.version import __version__

    parser = argparse.ArgumentParser(description=f'Dofusic {__version__}')
    parser.add_argument('--version', action='version', version=f'Dofusic {__version__}')
    parser.add_argument('--debug', action='store_true', help='Affiche les détails de résolution')
    parser.add_argument('--self-test', action='store_true', help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run_self_test() -> int:
    """Validate the frozen/offline runtime without opening the UI."""
    from dofusic.config import database_path, models_dir

    required_modules = (
        'rapidocr', 'onnxruntime', 'numpy', 'cv2', 'rapidfuzz', 'mss',
        'PIL', 'pygame', 'yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg',
        'win32gui', 'win32ui', 'win32con', 'win32process', 'tkinter',
    )
    try:
        for name in required_modules:
            importlib.import_module(name)
        database = database_path()
        if not database.is_file():
            return 41
        models = models_dir()
        for filename in ('PP-OCRv6_rec_small.onnx',):
            if not (models / filename).is_file():
                return 42

        import imageio_ffmpeg
        if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():
            return 43

        from dofusic.online.retrieval import resolve_quickjs_executable
        qjs_bin = resolve_quickjs_executable()
        if not qjs_bin.is_file():
            return 45
        kwargs = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'check': False}
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        if subprocess.run([str(qjs_bin), '--version'], **kwargs).returncode != 0:
            return 46

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
    multiprocessing.freeze_support()
    args = _parse_args()
    if args.self_test:
        return _run_self_test()

    from dofusic.config import app_dir, cleanup_legacy_user_data, config_path, database_path, load_config, log_dir
    from dofusic.logging_setup import setup_logging
    from dofusic.version import __version__

    cfg_path = config_path()
    cleanup_legacy_user_data()
    cfg = load_config(cfg_path)
    if args.debug:
        cfg.debug = True

    logger = setup_logging(log_dir(), debug=cfg.debug)
    logger.info('=== Dofusic %s démarrage ===', __version__)
    logger.info('APP_DIR=%s DB=%s CONFIG=%s', app_dir(), database_path(), cfg_path)

    if os.name != 'nt':
        logger.error('Dofusic nécessite Windows')
        print('Dofusic nécessite Windows pour capturer Dofus.exe.', file=sys.stderr)
        return 2
    if not database_path().exists():
        logger.error('Base Dofus absente: %s', database_path())
        print(f'Base Dofus absente: {database_path()}', file=sys.stderr)
        return 3

    from dofusic.app import DofusicController
    from dofusic.ui.main_window import MainWindow

    try:
        controller = DofusicController(cfg)
        MainWindow(controller, cfg, config_file=cfg_path).run()
        return 0
    except Exception:
        logger.exception('Erreur fatale application')
        raise


if __name__ == '__main__':
    raise SystemExit(main())
