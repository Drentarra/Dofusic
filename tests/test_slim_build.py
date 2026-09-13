from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'


def test_rapidocr_is_installed_without_dependencies_to_avoid_full_opencv_duplicate():
    batch = (ROOT / 'BUILD_PORTABLE.bat').read_text(encoding='utf-8', errors='replace').lower()
    assert '--no-deps rapidocr==3.9.2' in batch
    requirements = (DATA / 'requirements.txt').read_text(encoding='utf-8').lower()
    assert 'opencv-python-headless' in requirements
    assert '\nopencv-python==' not in '\n' + requirements
    assert '\nrapidocr==' not in '\n' + requirements


def test_slim_spec_does_not_collect_entire_heavy_packages():
    spec = (DATA / 'Dofusic.spec').read_text(encoding='utf-8')
    assert 'collect_all' not in spec
    assert "collect_data_files('rapidocr'" in spec
    assert 'PP-OCRv6_det_small.onnx' in spec
    assert 'ch_ppocr_mobile_v2.0_cls_mobile.onnx' in spec
    assert 'tensorrt' in spec
    assert 'openvino' in spec
    assert 'torch' in spec
    assert 'paddle' in spec


def test_builder_writes_size_report_and_detects_opencv_duplication():
    source = (DATA / 'tools' / 'build_portable.py').read_text(encoding='utf-8')
    assert 'BUILD_SIZE_REPORT.txt' in source
    assert 'opencv-python + opencv-python-headless' in source
    assert '_write_size_report' in source
