from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_pyinstaller_aggressive_but_safe_settings():
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    assert 'optimize=2' in spec
    for name in ('numpy.testing', 'numpy.f2py', 'PIL.ImageQt', 'pytest', 'PyInstaller', 'sympy', 'onnxruntime.tools', 'onnxruntime.quantization'):
        assert repr(name) in spec
    assert 'upx=False' in spec
    assert 'strip=False' in spec


def test_release_zip_uses_max_deflate_level():
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert 'compresslevel=9' in builder
