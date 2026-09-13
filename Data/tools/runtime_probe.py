from __future__ import annotations

import importlib
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dofusic.config import database_path, default_music_dir
from dofusic.location.repository import DofusRepository
REQUIRED_RUNTIME_MODULES = (
    'rapidocr', 'onnxruntime', 'numpy', 'cv2', 'rapidfuzz', 'mss',
    'PIL', 'pygame', 'yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg',
    'win32gui', 'win32ui', 'win32con', 'win32process', 'tkinter',
)


def _module_version(module) -> str:
    return str(getattr(module, '__version__', getattr(module, 'VERSION', '?')))


def main() -> int:
    failures = 0
    print(f'[INFO] Windows: {platform.platform()}')
    print(f'[INFO] Python: {sys.version.split()[0]} ({64 if sys.maxsize > 2**32 else 32} bits)')

    for name in REQUIRED_RUNTIME_MODULES:
        try:
            module = importlib.import_module(name)
            print(f'[OK] {name}: {_module_version(module)}')
        except Exception as exc:
            failures += 1
            print(f'[ERREUR] Module {name}: {exc}')


    try:
        from dofusic.online.retrieval import resolve_quickjs_executable
        qjs_bin = resolve_quickjs_executable()
        if not qjs_bin.is_file():
            raise FileNotFoundError(qjs_bin)
        kwargs = {'capture_output': True, 'text': True, 'check': False}
        if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([str(qjs_bin), '--version'], **kwargs)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or '').strip() or f'code {result.returncode}')
        version_line = (result.stdout or '').strip().splitlines()[0] if (result.stdout or '').strip() else '?'
        print(f'[OK] QuickJS: {version_line} ({qjs_bin})')
    except Exception as exc:
        failures += 1
        print(f'[ERREUR] Runtime QuickJS: {exc}')

    db = database_path()
    if not db.is_file():
        failures += 1
        print(f'[ERREUR] Base Dofus absente: {db}')
    else:
        try:
            repo = DofusRepository(db)
            counts = repo.counts()
            problems = repo.integrity_problems()
            required_counts = ('areas', 'subareas', 'map_coordinates')
            if problems:
                failures += 1
                print(f'[ERREUR] Intégrité DB: {problems[:5]}')
            elif any(int(counts.get(key, 0)) <= 0 for key in required_counts):
                failures += 1
                print(f'[ERREUR] Base Dofus incomplète: {counts}')
            else:
                print(f'[OK] Base Dofus: {counts}')
        except Exception as exc:
            failures += 1
            print(f'[ERREUR] Base Dofus: {exc}')

    try:
        music = default_music_dir()
        music.mkdir(parents=True, exist_ok=True)
        print(f'[OK] Musiques: {music}')
    except OSError as exc:
        failures += 1
        print(f'[ERREUR] Dossier Musiques: {exc}')

    if failures:
        print(f'[ECHEC] Validation runtime: {failures} erreur(s)')
        return 1
    print('[OK] Validation runtime Dofusic')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
