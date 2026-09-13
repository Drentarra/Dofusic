from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile



QUICKJS_RUNTIME = {
    'version': '0.16.2',
    'url': 'https://github.com/quickjs-ng/quickjs/releases/download/v0.16.2/qjs-windows-x86_64.exe',
    'sha256': '7b27412de844403545bd151fbe49191b4d5b91a9e15b5db7c863fea54639a82b',
}

OCR_MODELS = {
    'PP-OCRv6_rec_small.onnx': {
        'url': 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx',
        'sha256': '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884',
    },
}


class BuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download_checked(url: str, target: Path, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and _sha256(target) == expected_sha256:
        print(f'[OK] Modèle déjà valide: {target.name}')
        return

    temporary = target.with_suffix(target.suffix + '.part')
    temporary.unlink(missing_ok=True)
    print(f'[DL] {target.name}')
    request = urllib.request.Request(url, headers={'User-Agent': 'Dofusic-Portable-Builder/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise BuildError(f'Téléchargement impossible pour {target.name}: {exc}') from exc

    actual = _sha256(temporary)
    if actual != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise BuildError(
            f'Hash invalide pour {target.name}: attendu {expected_sha256}, obtenu {actual}'
        )
    os.replace(temporary, target)


def prepare_ocr_models(data_dir: Path) -> None:
    model_dir = data_dir / 'Models'
    for filename, metadata in OCR_MODELS.items():
        _download_checked(str(metadata['url']), model_dir / filename, str(metadata['sha256']))


def prepare_quickjs_runtime(root: Path) -> Path:
    target = root / '.portable-build' / 'runtime' / 'qjs.exe'
    _download_checked(
        str(QUICKJS_RUNTIME['url']),
        target,
        str(QUICKJS_RUNTIME['sha256']),
    )
    return target


def _run(stage: str, command: list[str], *, cwd: Path, timeout: float | None = None) -> None:
    print(f'\n[ETAPE] {stage}')
    print('>', ' '.join(str(part) for part in command))
    result = subprocess.run(command, cwd=cwd, check=False, timeout=timeout)
    if result.returncode != 0:
        raise BuildError(f'{stage} a échoué (code {result.returncode}).')


def validate_source(root: Path, data_dir: Path) -> None:
    required = (
        data_dir / 'main.py',
        data_dir / 'dofus_data.sqlite',
        data_dir / 'place_aliases.json',
        data_dir / 'dungeons.json',
        data_dir / 'dofusic' / 'ui' / 'assets' / 'dofusic.ico',
        data_dir / 'Dofusic.spec',
        data_dir / 'requirements.txt',
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError('Fichiers source manquants:\n- ' + '\n- '.join(missing))
    if not (root / 'Musiques').is_dir():
        (root / 'Musiques').mkdir(parents=True, exist_ok=True)


def validate_release(release_dir: Path) -> None:
    data_dir = release_dir / 'Data'
    _check_no_opencv_duplicate(data_dir)
    _check_removed_runtime_artifacts(data_dir)
    _check_large_duplicate_files(data_dir)
    required = (
        release_dir / 'Dofusic.exe',
        release_dir / 'Musiques',
        data_dir / 'dofus_data.sqlite',
        data_dir / 'place_aliases.json',
        data_dir / 'dungeons.json',
        data_dir / 'Models' / 'PP-OCRv6_rec_small.onnx',
        data_dir / 'rapidocr' / 'config.yaml',
        data_dir / 'rapidocr' / 'default_models.yaml',
        data_dir / 'rapidocr' / 'models' / 'PP-OCRv6_det_small.onnx',
        data_dir / 'rapidocr' / 'models' / 'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
        data_dir / 'README.md',
        data_dir / 'PRIVACY.md',
        data_dir / 'THIRD_PARTY_NOTICES.md',
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise BuildError('Release incomplète:\n- ' + '\n- '.join(missing))

    ffmpeg_candidates = [
        path for path in data_dir.rglob('*.exe')
        if 'ffmpeg' in path.name.casefold()
    ]
    if not ffmpeg_candidates:
        raise BuildError('FFmpeg embarqué introuvable dans Data.')
    if len(ffmpeg_candidates) != 1:
        raise BuildError(f'FFmpeg doit être embarqué une seule fois, trouvé: {len(ffmpeg_candidates)} copies.')

    qjs_candidates = [path for path in data_dir.rglob('qjs.exe') if path.is_file()]
    if not qjs_candidates:
        raise BuildError('Runtime QuickJS embarqué introuvable dans Data.')
    if len(qjs_candidates) != 1:
        raise BuildError(f'QuickJS doit être embarqué une seule fois, trouvé: {len(qjs_candidates)} copies.')
    if qjs_candidates[0].stat().st_size > 10 * 1024 * 1024:
        raise BuildError(f'QuickJS anormalement volumineux: {_human_size(qjs_candidates[0].stat().st_size)}')

    deno_candidates = [path for path in data_dir.rglob('deno.exe') if path.is_file()]
    if deno_candidates:
        raise BuildError('Runtime Deno residuel detecte dans la release slim.')
    ejs_candidates = [
        path for path in data_dir.rglob('*')
        if path.is_file() and 'yt_dlp_ejs' in path.parts
    ]
    if not ejs_candidates:
        raise BuildError('Scripts yt_dlp_ejs embarqués introuvables dans Data.')

    for filename, metadata in OCR_MODELS.items():
        model = data_dir / 'Models' / filename
        actual = _sha256(model)
        if actual != metadata['sha256']:
            raise BuildError(f'Modèle OCR corrompu dans la release: {filename}')

    (data_dir / 'UserData' / 'logs').mkdir(parents=True, exist_ok=True)
    (data_dir / 'UserData' / 'cache' / 'online').mkdir(parents=True, exist_ok=True)
    (data_dir / 'UserData' / 'cache' / 'thumbnails').mkdir(parents=True, exist_ok=True)



def _dir_size(path: Path) -> int:
    total = 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    for item in path.rglob('*'):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _human_size(size: int) -> str:
    value = float(max(0, size))
    units = ('B', 'KiB', 'MiB', 'GiB')
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f'{value:.1f} {unit}'
        value /= 1024.0
    return f'{value:.1f} GiB'


def _check_no_opencv_duplicate(data_dir: Path) -> None:
    # Un doublon opencv-python + opencv-python-headless fournit le meme module cv2
    # deux fois. Il gonfle fortement le venv/build et peut rendre la version de cv2
    # non deterministe. Le package final ne doit jamais etre produit dans cet etat.
    names = {path.name.casefold() for path in data_dir.iterdir()} if data_dir.is_dir() else set()
    full = any(name.startswith('opencv_python-') and name.endswith('.dist-info') for name in names)
    headless = any(name.startswith('opencv_python_headless-') and name.endswith('.dist-info') for name in names)
    if full and headless:
        raise BuildError('opencv-python + opencv-python-headless detectes simultanement dans la release.')


def _check_removed_runtime_artifacts(data_dir: Path) -> None:
    """Fail the public build if a removed V24 runtime slips back into Data."""
    offenders: list[Path] = []
    if not data_dir.is_dir():
        return
    for path in data_dir.rglob('*'):
        name = path.name.casefold()
        if (
            'webview' in name
            or name.startswith('pythonnet-')
            or name == 'pp-ocrv6_rec_medium.onnx'
        ):
            offenders.append(path)
    if offenders:
        details = '\n- '.join(str(path) for path in offenders[:20])
        raise BuildError('Artefacts runtime supprimes detectes dans la release:\n- ' + details)


def _check_large_duplicate_files(data_dir: Path, *, threshold_bytes: int = 8 * 1024 * 1024) -> None:
    """Reject exact duplicate large runtime files before publishing the ZIP."""
    by_size: dict[int, list[Path]] = {}
    if not data_dir.is_dir():
        return
    for path in data_dir.rglob('*'):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size >= threshold_bytes:
            by_size.setdefault(size, []).append(path)

    duplicates: list[list[Path]] = []
    for paths in by_size.values():
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in paths:
            by_hash.setdefault(_sha256(path), []).append(path)
        duplicates.extend(group for group in by_hash.values() if len(group) > 1)
    if duplicates:
        details = []
        for group in duplicates[:10]:
            details.append(' = '.join(str(path.relative_to(data_dir)) for path in group))
        raise BuildError('Gros fichiers runtime dupliqués détectés:\n- ' + '\n- '.join(details))


def _write_size_report(release_dir: Path, zip_path: Path, report_path: Path) -> None:
    total = _dir_size(release_dir)
    zip_size = zip_path.stat().st_size if zip_path.is_file() else 0
    data_dir = release_dir / 'Data'

    entries: list[tuple[int, str]] = []
    if data_dir.is_dir():
        for item in data_dir.iterdir():
            entries.append((_dir_size(item), f'Data/{item.name}'))
    if (release_dir / 'Musiques').exists():
        entries.append((_dir_size(release_dir / 'Musiques'), 'Musiques'))
    if (release_dir / 'Dofusic.exe').is_file():
        entries.append((_dir_size(release_dir / 'Dofusic.exe'), 'Dofusic.exe'))
    entries.sort(reverse=True)

    lines = [
        'DOFUSIC V25.1 ECO - BUILD SIZE REPORT',
        '=================================',
        '',
        f'Taille decompressee : {_human_size(total)} ({total} octets)',
        f'Taille ZIP          : {_human_size(zip_size)} ({zip_size} octets)',
        '',
        '20 plus gros elements :',
    ]
    lines.extend(f'{_human_size(size):>12}  {name}' for size, name in entries[:20])
    lines += [
        '',
        'Regles Slim appliquees :',
        '- un seul OpenCV : opencv-python-headless',
        '- RapidOCR 3.9.2 installe --no-deps (dependances maitrisees explicitement)',
        '- aucun collect_all PyInstaller pour RapidOCR / yt-dlp / imageio-ffmpeg',
        '- aucun navigateur embarque ni WebView2 : recherche online via yt-dlp',
        '- QuickJS-NG embarque en qjs.exe (~2 MiB) a la place de Deno (~97 MiB)',
        '- garde anti-doublons: aucun gros fichier runtime identique en plusieurs copies',
        '- modules de build/tests et sous-modules lourds inutiles exclus de PyInstaller',
        '- backends OCR inutiles exclus (TensorRT, Torch, Paddle, OpenVINO, MNN)',
        '- UPX desactive : aucune penalite de demarrage ni risque antivirus ajoute',
        '',
    ]
    report_path.write_text('\n'.join(lines), encoding='utf-8')

def _copy_music_library(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, target)


def _make_zip(release_dir: Path, zip_path: Path) -> None:
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(release_dir.rglob('*')):
            if path.is_file():
                archive.write(path, Path(release_dir.name) / path.relative_to(release_dir))


def build(root: Path) -> Path:
    root = root.resolve()
    data_dir = root / 'Data'
    release_root = root / 'Release'
    release_dir = release_root / 'Dofusic'
    work_dir = root / '.portable-build' / 'pyinstaller'

    validate_source(root, data_dir)
    prepare_ocr_models(data_dir)
    quickjs = prepare_quickjs_runtime(root)
    os.environ['DOFUSIC_QJS_BINARY'] = str(quickjs)
    _run('Tests automatiques', [sys.executable, '-m', 'pytest', '-q'], cwd=root)
    _run('Validation des dépendances et de la base', [sys.executable, str(data_dir / 'tools' / 'runtime_probe.py')], cwd=data_dir)
    _run('Validation locale du moteur OCR', [sys.executable, str(data_dir / 'tools' / 'prepare_models.py')], cwd=data_dir, timeout=180)

    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_root.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    _run(
        'Construction PyInstaller portable',
        [
            sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
            '--distpath', str(release_root), '--workpath', str(work_dir),
            str(data_dir / 'Dofusic.spec'),
        ],
        cwd=data_dir,
        timeout=900,
    )

    _copy_music_library(root / 'Musiques', release_dir / 'Musiques')
    validate_release(release_dir)

    exe = release_dir / 'Dofusic.exe'
    _run('Auto-test du programme compilé', [str(exe), '--self-test'], cwd=release_dir, timeout=180)

    zip_path = release_root / 'Dofusic_V25_1_ECO_WINDOWS_X64.zip'
    _make_zip(release_dir, zip_path)
    report_path = release_root / 'BUILD_SIZE_REPORT.txt'
    _write_size_report(release_dir, zip_path, report_path)
    print(f'\n[OK] Release: {release_dir}')
    print(f'[OK] ZIP: {zip_path}')
    print(f'[OK] Rapport taille: {report_path}')
    return zip_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Construit Dofusic V25.1 ECO en version Windows portable autonome.')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        build(args.root)
    except (BuildError, OSError, subprocess.SubprocessError) as exc:
        print(f'\n[ECHEC] {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
