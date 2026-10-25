from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

OCR_MODELS = {
    'PP-OCRv6_rec_small.onnx': {
        'url': 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx',
        'sha256': '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884',
    },
}


class BuildError(RuntimeError):
    pass


# Files present in the publicly versioned Musiques folder are packaged as data,
# next to Dofusic.exe (NOT inside the PyInstaller internal Data directory).
_MUSIC_EXTENSIONS = frozenset({
    '.opus', '.ogg', '.oga', '.mp3', '.mp2', '.flac', '.wav',
    '.m4a', '.aac', '.wma', '.webm', '.mka', '.aiff', '.aif', '.ac3',
})


def _music_files(folder: Path, *, allow_empty: bool = False) -> list[Path]:
    """Enumerate released tracks; reject symlinks and missing/empty OPUS files."""
    if not folder.is_dir() or folder.is_symlink():
        raise BuildError(f'Dossier Musiques introuvable ou invalide : {folder}')
    paths = []
    for entry in sorted(folder.rglob('*')):
        if entry.is_symlink():
            raise BuildError(f'Lien symbolique interdit dans Musiques : {entry}')
        if entry.is_file() and entry.suffix.casefold() in _MUSIC_EXTENSIONS:
            if entry.stat().st_size <= 0:
                raise BuildError(f'Fichier audio vide dans Musiques : {entry}')
            paths.append(entry)
    if not paths and allow_empty:
        return []
    if not any(path.suffix.casefold() == '.opus' for path in paths):
        raise BuildError('Aucun fichier OPUS dans Musiques : publication interrompue.')
    return paths


def _copy_music(root: Path, release_dir: Path) -> int:
    """Copy every versioned audio file without modifying names or subfolders."""
    folder = root / 'Musiques'
    tracks = _music_files(folder)
    destination = release_dir / 'Musiques'
    destination.mkdir(parents=True, exist_ok=True)
    for source in tracks:
        target = destination / source.relative_to(folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Only audio assets are user-facing; no README or development notes.
    return len(tracks)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(root: Path) -> tuple[str, int]:
    """Hash source/build inputs AND the tracked soundtrack shipped in the portable."""
    patterns = (
        'Data/dofusic/**/*.py', 'Data/dofusic/ui/assets/**/*',
        'Data/tools/**/*.py', 'Data/main.py',
        'Data/Dofusic.spec', 'Data/requirements*.txt',
        'tests/**/*.py', 'tests/fixtures/**/*', 'tests/hud_fixtures/**/*',
        'pyproject.toml', 'requirements-dev.txt',
        '.github/workflows/*.yml',
        '.github/scripts/*.py', 'BUILD_PORTABLE.bat', 'VERIFIER_PORTABLE_WINDOWS.ps1',
        'Data/dofus_data.sqlite', 'Data/dungeons.json', 'Data/place_aliases.json', 'Data/README.md',
        'Data/PRIVACY.md', 'Data/THIRD_PARTY_NOTICES.md',
        'Musiques/**/*',
    )
    paths = sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()})
    digest = hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix().encode('utf-8')
        digest.update(len(name).to_bytes(4, 'big'))
        digest.update(name)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest(), len(paths)


def _checked_local_commit(root: Path) -> str | None:
    """Optional local Git metadata; ZIP compilation works without Git installed."""
    if not (root / '.git').exists() or not shutil.which('git'):
        return None
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--verify', 'HEAD'], cwd=root,
            capture_output=True, text=True, check=False, timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and re.fullmatch(r'[0-9a-f]{40}', value) else None


def write_build_provenance(root: Path, release_dir: Path, *, archive: Path | None = None) -> dict:
    """Generate a detached audit record next to, never inside, the public ZIP."""
    checksum, count = _source_fingerprint(root)
    commit = _checked_local_commit(root)
    provenance = {
        'schema': 1,
        'source_sha256': checksum,
        'source_files': count,
        'git_commit': commit,
        'github_source_url': (
            f'https://github.com/Drentarra/Dofusic/tree/{commit}' if commit else None
        ),
        'git_verified_locally': commit is not None,
        'portable_sha256': _sha256(archive) if archive is not None else None,
    }
    target = release_dir / 'BUILD_PROVENANCE.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return provenance


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


def _run(stage: str, command: list[str], *, cwd: Path, timeout: float | None = None) -> None:
    print(f'\n[ETAPE] {stage}')
    print('>', ' '.join(str(part) for part in command))
    result = subprocess.run(command, cwd=cwd, check=False, timeout=timeout)
    if result.returncode != 0:
        raise BuildError(f'{stage} a échoué (code {result.returncode}).')


def validate_source(root: Path, data_dir: Path, *, require_music: bool = True) -> bool:
    """Validate build inputs and report whether a soundtrack can be packaged.

    Source-only CI can explicitly allow an absent soundtrack. An incomplete or
    invalid soundtrack still fails, and portable/release builds remain strict.
    """
    required = (
        data_dir / 'main.py',
        data_dir / 'dofus_data.sqlite',
        data_dir / 'place_aliases.json',
        data_dir / 'dungeons.json',
        data_dir / 'dofusic' / 'ui' / 'assets' / 'dofusic.ico',
        data_dir / 'Dofusic.spec',
        data_dir / 'requirements.txt',
        data_dir / 'requirements-lock.txt',
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError('Fichiers source manquants:\n- ' + '\n- '.join(missing))
    # This distribution promises the tracks checked out from the repository.
    # Never silently publish an empty music folder from an incomplete checkout.
    return bool(_music_files(root / 'Musiques', allow_empty=not require_music))


# PyInstaller hooks may collect README files from runtime dependencies, e.g.
# imageio_ffmpeg/binaries/README.md beside the FFmpeg executable. These notes
# are not needed to execute the application and must not reach the public ZIP.
# Keep licensing notices and non-documentation resources intact.
_BUNDLED_README_NAMES = frozenset({'readme.md', 'readme.txt', 'readme.rst', 'privacy.md'})


def remove_bundled_readmes(release_dir: Path) -> list[Path]:
    """Remove package READMEs after COLLECT, before verification and ZIP creation.

    Only known documentation filenames below the PyInstaller Data directory
    are removed; binaries, configs, model dictionaries, and license notices are
    never altered. The source tree and its documentation remain unchanged.
    """
    data_dir = release_dir / 'Data'
    if not data_dir.is_dir():
        raise BuildError(f'Dossier runtime introuvable : {data_dir}')
    removed: list[Path] = []
    for path in sorted(data_dir.rglob('*')):
        if path.is_symlink():
            raise BuildError(f'Lien symbolique inattendu dans Data : {path}')
        if path.is_file() and path.name.casefold() in _BUNDLED_README_NAMES:
            path.unlink()
            removed.append(path.relative_to(release_dir))
    return removed


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
        data_dir / 'THIRD_PARTY_NOTICES.md',
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise BuildError('Release incomplète:\n- ' + '\n- '.join(missing))
    _music_files(release_dir / 'Musiques')

    ffmpeg_candidates = [path for path in data_dir.rglob('*.exe') if 'ffmpeg' in path.name.casefold()]
    if len(ffmpeg_candidates) != 1:
        raise BuildError(f'FFmpeg doit être embarqué une seule fois, trouvé: {len(ffmpeg_candidates)} copies.')

    unwanted = {'qjs.exe', 'deno.exe'}
    if any(p.name.casefold() in unwanted for p in data_dir.rglob('*.exe')):
        raise BuildError('Runtime JavaScript inutile détecté dans le portable.')
    if any('yt_dlp' in p.parts or 'yt_dlp_ejs' in p.parts for p in data_dir.rglob('*')):
        raise BuildError('Dépendance vidéo en ligne détectée dans le portable.')

    for filename, metadata in OCR_MODELS.items():
        if _sha256(data_dir / 'Models' / filename) != metadata['sha256']:
            raise BuildError(f'Modèle OCR corrompu dans la release: {filename}')

    validate_public_payload(release_dir)


# The *compiled* distribution contains only the app, its runtime and audio.
# Tests, build reports, diagnostic tools and generated runtime data live elsewhere.
_FORBIDDEN_DIRS = frozenset({'userdata', 'logs', 'diagnostics', 'tests', '__pycache__',
                            '.pytest_cache', '.mypy_cache', '.ruff_cache', 'audit', 'build', 'dist'})
_FORBIDDEN_EXTENSIONS = frozenset({'.log', '.dmp', '.dump', '.trace', '.tmp', '.part',
                                   '.pyc', '.pyo', '.pdb', '.bak', '.mdmp'})
_FORBIDDEN_NAMES = frozenset({'build_provenance.json', 'build_size_report.txt',
                              'portable_archive_name.txt', '.coverage',
                              'diagnostic_dofus.bat', 'diagnostic_simulateur.bat',
                              'guide_diagnostic_windows.md', 'readme.md', 'readme.txt', 'readme.rst', 'privacy.md'})


def validate_public_payload(release_dir: Path) -> None:
    """Fail closed when a build accidentally distributes dev or diagnostic files."""
    allowed_root = {'Dofusic.exe', 'Data', 'Musiques'}
    actual_root = {p.name for p in release_dir.iterdir()}
    if actual_root != allowed_root:
        raise BuildError(f'Racine du portable non conforme: {sorted(actual_root ^ allowed_root)}')
    for path in release_dir.rglob('*'):
        if path.is_symlink():
            raise BuildError(f'Lien symbolique interdit dans le portable: {path}')
        relative = path.relative_to(release_dir)
        parts = relative.parts
        if path.is_dir():
            if len(parts) > 1 and path.name.casefold() in _FORBIDDEN_DIRS:
                raise BuildError(f'Dossier de diagnostic/developpement dans le portable: {relative}')
            continue
        name = path.name.casefold()
        if name in _FORBIDDEN_NAMES or path.suffix.casefold() in _FORBIDDEN_EXTENSIONS:
            raise BuildError(f'Fichier de diagnostic/developpement dans le portable: {relative}')
        if parts[0] == 'Musiques' and path.suffix.casefold() not in _MUSIC_EXTENSIONS:
            raise BuildError(f'Fichier non musical dans Musiques: {relative}')


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
    names = {path.name.casefold() for path in data_dir.iterdir()} if data_dir.is_dir() else set()
    full = any(name.startswith('opencv_python-') and name.endswith('.dist-info') for name in names)
    headless = any(name.startswith('opencv_python_headless-') and name.endswith('.dist-info') for name in names)
    if full and headless:
        raise BuildError('opencv-python + opencv-python-headless detectes simultanement dans la release.')


def _check_removed_runtime_artifacts(data_dir: Path) -> None:
    offenders: list[Path] = []
    if not data_dir.is_dir():
        return
    for path in data_dir.rglob('*'):
        name = path.name.casefold()
        if 'webview' in name or name.startswith('pythonnet-') or name == 'pp-ocrv6_rec_medium.onnx':
            offenders.append(path)
    if offenders:
        details = '\n- '.join(str(path) for path in offenders[:20])
        raise BuildError('Artefacts runtime supprimes detectes dans la release:\n- ' + details)


def _check_large_duplicate_files(data_dir: Path, *, threshold_bytes: int = 8 * 1024 * 1024) -> None:
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
        details = [' = '.join(str(path.relative_to(data_dir)) for path in group) for group in duplicates[:10]]
        raise BuildError('Gros fichiers runtime dupliqués détectés:\n- ' + '\n- '.join(details))


def _write_size_report(release_dir: Path, zip_path: Path, report_path: Path) -> None:
    total = _dir_size(release_dir)
    zip_size = zip_path.stat().st_size if zip_path.is_file() else 0
    data_dir = release_dir / 'Data'

    entries: list[tuple[int, str]] = []
    if data_dir.is_dir():
        for item in data_dir.iterdir():
            entries.append((_dir_size(item), f'Data/{item.name}'))
    entries.append((_dir_size(release_dir / 'Musiques'), 'Musiques'))
    entries.append((_dir_size(release_dir / 'Dofusic.exe'), 'Dofusic.exe'))
    entries.sort(reverse=True)

    lines = [
        'DOFUSIC - PORTABLE BUILD SIZE REPORT',
        '=================================',
        '',
        f'Taille decompressee : {_human_size(total)} ({total} octets)',
        f'Taille ZIP          : {_human_size(zip_size)} ({zip_size} octets)',
        '',
        '20 plus gros elements :',
    ]
    lines.extend(f'{_human_size(size):>12}  {name}' for size, name in entries[:20])
    report_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


# A fixed ZIP timestamp makes the archive reproducible from identical build
# outputs, regardless of filesystem timestamps on the build machine.
_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _zip_info(name: str, *, directory: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.create_system = 3
    # Opus is already compressed: store it as-is for faster packaging.
    info.compress_type = zipfile.ZIP_STORED if name.casefold().endswith('.opus') else zipfile.ZIP_DEFLATED
    info.external_attr = ((0o40755 if directory else 0o100644) << 16)
    if directory:
        info.external_attr |= 0x10
    return info


def _make_zip(release_dir: Path, zip_path: Path) -> None:
    """Package only the clean portable, including all OPUS, with streaming IO."""
    validate_public_payload(release_dir)
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(release_dir.rglob('*')):
            if path.is_symlink():
                raise BuildError(f'Lien symbolique interdit dans le portable : {path}')
            relative = (Path(release_dir.name) / path.relative_to(release_dir)).as_posix()
            if path.is_dir():
                archive.writestr(_zip_info(relative + '/', directory=True), b'')
            elif path.is_file():
                with path.open('rb') as source, archive.open(_zip_info(relative, directory=False), 'w') as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)


def validate_archive(release_dir: Path, zip_path: Path) -> None:
    """Reject missing/extra files, corrupt ZIP members and absent OPUS music."""
    expected_files = {
        (Path(release_dir.name) / path.relative_to(release_dir)).as_posix()
        for path in release_dir.rglob('*') if path.is_file()
    }
    try:
        with zipfile.ZipFile(zip_path) as archive:
            files = [item.filename for item in archive.infolist() if not item.is_dir()]
            if len(files) != len(set(files)) or set(files) != expected_files:
                raise BuildError(
                    f'ZIP invalide: {len(expected_files)} fichiers attendus, '
                    f'{len(files)} entrées réelles; absents={sorted(expected_files - set(files))[:5]}, '
                    f'extras={sorted(set(files) - expected_files)[:5]}'
                )
            if not any(name.startswith('Dofusic/Musiques/') and name.casefold().endswith('.opus') for name in files):
                raise BuildError('ZIP invalide : aucun fichier OPUS embarqué')
            bad_file = archive.testzip()  # CRC of every compressed member
            if bad_file is not None:
                raise BuildError(f'ZIP corrompu : {bad_file}')
    except (OSError, zipfile.BadZipFile) as exc:
        raise BuildError(f'ZIP illisible : {exc}') from exc


def build(root: Path, *, skip_tests: bool = False) -> Path:
    # PyInstaller cross-compilation is unsupported: never pretend that a Linux
    # build has produced a working Windows executable.
    if os.name != 'nt' or sys.maxsize <= 2**32:
        raise BuildError('La compilation et le self-test Dofusic.exe nécessitent Windows x64.')
    root = root.resolve()
    data_dir = root / 'Data'
    release_root = root / 'Release'
    release_dir = release_root / 'Dofusic'
    work_dir = root / '.portable-build' / 'pyinstaller'

    validate_source(root, data_dir)
    prepare_ocr_models(data_dir)
    if not skip_tests:
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

    # Drop dependency README files introduced by PyInstaller hooks before checking
    # the public payload (notably imageio_ffmpeg/binaries/README.md).
    for removed in remove_bundled_readmes(release_dir):
        print(f'[NETTOYAGE] Documentation runtime non requise : {removed}')

    # Copy tracked OPUS soundtrack from GitHub checkout into the portable root.
    music_count = _copy_music(root, release_dir)
    print(f'[OK] Musiques embarquées : {music_count} fichiers audio')
    validate_release(release_dir)

    exe = release_dir / 'Dofusic.exe'
    _run('Auto-test du programme compilé', [str(exe), '--self-test'], cwd=release_dir, timeout=180)

    # Load the canonical version independent of sys.path and build invocation mode.
    version_symbols = runpy.run_path(str(data_dir / 'dofusic' / 'version.py'))
    zip_name = version_symbols['portable_zip_name']()
    zip_path = release_root / zip_name
    _make_zip(release_dir, zip_path)
    validate_archive(release_dir, zip_path)
    write_build_provenance(root, release_root, archive=zip_path)
    report_path = release_root / 'BUILD_SIZE_REPORT.txt'
    _write_size_report(release_dir, zip_path, report_path)
    # Batch and CI consume this exact generated name; never reconstruct it.
    (release_root / 'PORTABLE_ARCHIVE_NAME.txt').write_text(zip_name + '\n', encoding='ascii')
    print(f'\n[OK] Release: {release_dir}')
    print(f'[OK] ZIP: {zip_path}')
    print(f'[OK] Rapport taille: {report_path}')
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--skip-tests', action='store_true',
                        help='CI only: tests already completed in the same job')
    args = parser.parse_args()
    try:
        build(args.root, skip_tests=args.skip_tests)
    except (BuildError, subprocess.TimeoutExpired) as exc:
        print(f'[ERREUR] {exc}', file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == '__main__':
    main()
