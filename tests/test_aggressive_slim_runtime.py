from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_runtime_uses_quickjs_not_deno():
    retrieval = (DATA / 'dofusic' / 'online' / 'retrieval.py').read_text(encoding='utf-8')
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8')
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert 'resolve_quickjs_executable' in retrieval
    assert "'quickjs'" in retrieval
    assert 'deno==' not in requirements
    assert "qjs.exe" in spec
    assert 'deno.exe' not in spec
    assert 'QUICKJS_RUNTIME' in builder
    assert 'DENO_BINARY' not in builder


def test_quickjs_is_pinned_and_hash_checked():
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert "'version': '0.16.2'" in builder
    assert 'qjs-windows-x86_64.exe' in builder
    assert '7b27412de844403545bd151fbe49191b4d5b91a9e15b5db7c863fea54639a82b' in builder
    assert '_download_checked' in builder


def test_release_rejects_deno_and_requires_single_small_qjs():
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert "rglob('qjs.exe')" in builder
    assert 'Runtime Deno residuel' in builder
    assert 'QuickJS anormalement volumineux' in builder


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
