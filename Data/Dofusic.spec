# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir slim pour Dofusic V25.1 ECO.

Principe : ne jamais collecter des paquets entiers par confort. PyInstaller suit
le graphe d'import du programme, tandis que nous ajoutons seulement les donnees
runtime indispensables. Cela evite d'embarquer sources, backends et modeles
optionnels qui n'interviennent jamais dans Dofusic.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

DATA_ROOT = Path(SPECPATH).resolve()

QUICKJS_BINARY = (DATA_ROOT.parent / '.portable-build' / 'runtime' / 'qjs.exe').resolve()
if not QUICKJS_BINARY.is_file():
    raise FileNotFoundError(f'Executable QuickJS introuvable pendant le build: {QUICKJS_BINARY}')

binaries = [(str(QUICKJS_BINARY), 'js')]
datas = [
    (str(DATA_ROOT / 'dofus_data.sqlite'), '.'),
    (str(DATA_ROOT / 'place_aliases.json'), '.'),
    (str(DATA_ROOT / 'dungeons.json'), '.'),
    (str(DATA_ROOT / 'Models'), 'Models'),
    (str(DATA_ROOT / 'dofusic' / 'ui' / 'assets'), 'dofusic/ui/assets'),
    (str(DATA_ROOT / 'README.md'), '.'),
    (str(DATA_ROOT / 'PRIVACY.md'), '.'),
    (str(DATA_ROOT / 'THIRD_PARTY_NOTICES.md'), '.'),
]

# RapidOCR charge sa configuration et initialise encore ses sessions Det/Cls au
# constructeur, meme si Dofusic appelle ensuite recognition-only. On conserve
# uniquement les deux modeles auxiliaires requis par cette initialisation et les
# fichiers de configuration, pas le catalogue complet de modeles de la wheel.
rapidocr_data = collect_data_files('rapidocr',
    includes=[
        'config.yaml',
        'default_models.yaml',
        'models/PP-OCRv6_det_small.onnx',
        'models/ch_ppocr_mobile_v2.0_cls_mobile.onnx',
        'models/ppocr_keys_v1.txt',
    ],
)
datas += rapidocr_data

# YouTube 2026: yt-dlp requires local EJS scripts plus a JavaScript runtime.
# QuickJS-NG is bundled as one tiny qjs.exe via `binaries` above.
# yt_dlp_ejs still needs its JavaScript package data.
datas += collect_data_files('yt_dlp_ejs')


hiddenimports = [
    'win32con', 'win32gui', 'win32process', 'win32ui', 'pywintypes',
    'multiprocessing.popen_spawn_win32',
    'yt_dlp_ejs',
]

# Backends et frameworks non utilises. Dofusic reste exclusivement ONNX Runtime
# CPU. Les exclusions ne changent ni le modele OCR, ni les crops, ni les threads.
excludes = [
    'psutil',
    'deno',
    'numpy.testing', 'numpy.f2py',
    'PIL.ImageQt',
    'pytest', 'PyInstaller', 'setuptools', 'pip', 'wheel',
    'sympy',
    'onnxruntime.tools', 'onnxruntime.quantization', 'onnxruntime.transformers',
    'tensorrt',
    'torch', 'torchvision',
    'paddle', 'paddleocr',
    'openvino',
    'mnn', 'MNN',
    'tensorflow',
    'onnxruntime_gpu',
    'matplotlib',
    'pandas',
    # GUI/browser frameworks not used by Dofusic.
    'webview', 'clr', 'cefpython3', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi',
]

a = Analysis(
    [str(DATA_ROOT / 'main.py')],
    pathex=[str(DATA_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Dofusic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(DATA_ROOT / 'dofusic' / 'ui' / 'assets' / 'dofusic.ico'),
    contents_directory='Data',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Dofusic',
)
