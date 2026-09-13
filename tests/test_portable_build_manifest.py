from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_single_portable_build_entrypoint_exists():
    assert (ROOT / 'BUILD_PORTABLE.bat').is_file()
    assert (DATA / 'Dofusic.spec').is_file()
    assert (DATA / 'requirements-build.txt').is_file()
    assert (DATA / 'tools' / 'build_portable.py').is_file()


def test_obsolete_first_run_bootstrap_is_removed():
    assert not (DATA / 'SETUP_DEV.bat').exists()
    assert not (DATA / 'tools' / 'setup_runtime.py').exists()
    assert not (DATA / 'tools' / 'runtime_guard.py').exists()


def test_runtime_uses_headless_opencv():
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8')
    assert 'opencv-python-headless' in requirements
    assert '\nopencv-python>=' not in '\n' + requirements


def test_builder_model_manifest_has_expected_sha256_values():
    module_path = DATA / 'tools' / 'build_portable.py'
    spec = importlib.util.spec_from_file_location('dofusic_build_portable', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert set(module.OCR_MODELS) == {'PP-OCRv6_rec_small.onnx'}
    assert module.OCR_MODELS['PP-OCRv6_rec_small.onnx']['sha256'] == '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'


def test_readme_describes_zero_install_end_user_flow():
    readme = (DATA / 'README.md').read_text(encoding='utf-8').lower()
    assert 'aucun python' in readme
    assert 'premier lancement' not in readme or 'aucun téléchargement au premier lancement' in readme


def test_build_bootstrap_uses_uv_managed_python_without_windows_installer_collision():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace').lower()
    assert 'uv-installer.ps1' in batch
    assert 'uv_unmanaged_install' in batch
    assert 'uv_python_install_dir' in batch
    assert 'uv_python_install_registry=0' in batch
    assert 'venv "%venv%" --python 3.11.16 --managed-python' in batch
    assert 'python install 3.11.16' in batch
    assert 'python find 3.11.16 --managed-python' not in batch
    assert 'python-3.11.9-amd64.exe' not in batch
    assert 'include_tcltk=1' not in batch


def test_build_bootstrap_uses_disposable_venv_instead_of_mutating_managed_python():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace').lower()
    assert 'set "venv=' in batch
    assert 'uv.exe" venv' in batch or '"%uvexe%" venv' in batch
    assert '--managed-python' in batch
    assert 'scripts\\python.exe' in batch
    assert 'set "pyexe=%venv%\\scripts\\python.exe"' in batch
    assert 'pip install --python "%pyexe%"' in batch
    assert 'pip install --python "%pymanaged%"' not in batch


def test_build_bootstrap_verifies_tkinter_before_installing_dependencies():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace').lower()
    verify_pos = batch.index('import sys, tkinter')
    install_pos = batch.index('pip install')
    assert verify_pos < install_pos


def test_build_python_isolated_from_global_python_environment():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace').lower()
    assert 'set "pythonhome="' in batch
    assert 'set "pythonpath="' in batch
    assert 'set "pythonnousersite=1"' in batch


def test_portable_youtube_runtime_is_pinned_and_bundled():
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8').lower()
    assert 'yt-dlp==2026.8.19' in requirements
    assert 'yt-dlp-ejs==0.8.0' in requirements
    assert 'deno==' not in requirements

    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    assert "collect_data_files('deno'" not in spec
    assert "collect_data_files('yt_dlp_ejs'" in spec
    assert "qjs.exe" in spec
    assert "(str(QUICKJS_BINARY), 'js')" in spec

    probe = (DATA / 'tools' / 'runtime_probe.py').read_text(encoding='utf-8')
    assert "'yt_dlp_ejs'" in probe
    assert 'resolve_quickjs_executable' in probe


def test_release_validator_requires_bundled_quickjs_and_ejs():
    builder = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert 'qjs.exe' in builder
    assert 'yt_dlp_ejs' in builder
    assert 'Runtime Deno residuel' in builder


def test_embedded_browser_runtime_is_removed_from_public_slim_build():
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8').lower()
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8').lower()
    main = (DATA / 'main.py').read_text(encoding='utf-8').lower()
    probe = (DATA / 'tools' / 'runtime_probe.py').read_text(encoding='utf-8').lower()

    assert 'pywebview' not in requirements
    assert "collect_data_files('webview'" not in spec
    assert "collect_dynamic_libs('webview')" not in spec
    assert "'webview.platforms" not in spec
    assert "'webview'" not in main
    assert "'webview'" not in probe
    assert not (DATA / 'dofusic' / 'online' / 'browser.py').exists()
    assert not (DATA / 'dofusic' / 'online' / 'extractor.py').exists()


def test_online_v2_has_no_invidious_runtime_or_settings_dependency():
    config = (DATA / 'dofusic' / 'config.py').read_text(encoding='utf-8').lower()
    online_sources = '\n'.join(path.read_text(encoding='utf-8').lower() for path in (DATA / 'dofusic' / 'online').glob('*.py'))
    settings = (DATA / 'dofusic' / 'ui' / 'settings_window.py').read_text(encoding='utf-8').lower()
    privacy = (DATA / 'PRIVACY.md').read_text(encoding='utf-8').lower()

    assert 'invidious_instance' not in config
    assert 'invidious' not in online_sources
    assert 'invidious' not in settings
    assert 'invidious' not in privacy
    assert not (DATA / 'dofusic' / 'online' / 'invidious.py').exists()


