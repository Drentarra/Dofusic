"""Build-only verification: music ZIP SHA/extraction, signed ZIP preservation, final ZIP/hash and SBOM pins."""

import argparse
import hashlib
import json
import re
import shutil
import stat
import zipfile
from pathlib import Path

DOS_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in ('1', '2', '3', '4', '5', '6', '7', '8', '9', '\u00b9', '\u00b2', '\u00b3'))}
MUSIC_EXTENSIONS = frozenset({
    '.opus', '.ogg', '.oga', '.mp3', '.mp2', '.flac', '.wav',
    '.m4a', '.aac', '.wma', '.webm', '.mka', '.aiff', '.aif', '.ac3',
})


def _validated_entries(archive, kind):
    """Validate raw paths and Windows aliases, including implicit directories."""
    seen, spellings, files = {}, {}, []
    for item in archive.infolist():
        name = item.orig_filename
        path = name[:-1] if item.is_dir() else name
        parts = path.split('/')
        if any(not part or part in {'.', '..'} or part.endswith((' ', '.'))
               or re.search(r'[\\:\x00-\x1f<>"|?*]', part)
               or part.split('.')[0].rstrip(' ').upper() in DOS_NAMES for part in parts):
            raise ValueError(f'Unsafe ZIP path: {name!r}')
        mode = stat.S_IFMT(item.external_attr >> 16)
        if mode not in (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG):
            raise ValueError(f'Non-regular ZIP path: {name!r}')
        if kind == 'music':
            allowed = parts[0] == 'Musiques' and (len(parts) > 1 or item.is_dir())
        else:
            allowed = parts[0] == 'Dofusic' and (
                (len(parts) == 1 and item.is_dir()) or
                (len(parts) == 2 and parts[1] == 'Dofusic.exe' and not item.is_dir()) or
                (len(parts) >= 2 and parts[1] in {'Data', 'Musiques'} and (len(parts) > 2 or item.is_dir()))
            )
        if not allowed:
            raise ValueError(f'Unexpected ZIP root/layout: {name!r}')
        # The internal runtime may contain licensing/configuration resources,
        # but never user data, logs, diagnostics or developer/build artefacts.
        if any(x.casefold() in {'userdata', 'logs', 'diagnostics', 'tests', '__pycache__',
                                '.pytest_cache', 'audit'} for x in (parts[1:] if item.is_dir() else parts[1:-1])):
            raise ValueError(f'Forbidden ZIP development directory: {name!r}')
        if not item.is_dir() and (
            parts[-1].casefold() in {'build_provenance.json', 'build_size_report.txt',
                                    'portable_archive_name.txt', 'diagnostic_dofus.bat',
                                    'diagnostic_simulateur.bat', 'guide_diagnostic_windows.md',
                                    'readme.md', 'readme.txt', 'readme.rst', 'privacy.md'}
            or Path(parts[-1]).suffix.casefold() in {'.log', '.dmp', '.dump', '.trace',
                                                     '.tmp', '.part', '.pyc', '.pyo',
                                                     '.pdb', '.bak', '.mdmp'}
            or (len(parts) >= 3 and parts[1] == 'Musiques' and
                Path(parts[-1]).suffix.casefold() not in MUSIC_EXTENSIONS)
        ):
            raise ValueError(f'Forbidden ZIP development file: {name!r}')
        key = path.casefold()
        if key in seen:
            raise ValueError(f'Duplicate ZIP path: {name!r}')
        seen[key] = item.is_dir()
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise ValueError(f'Windows path case collision: {name!r}')
            spellings[folded] = prefix
        if not item.is_dir():
            files.append(item)
    for path in seen:
        parts = path.split('/')
        if any(seen.get('/'.join(parts[:i])) is False for i in range(1, len(parts))):
            raise ValueError(f'File/folder ZIP path collision: {path!r}')
    if kind == 'music':
        if not files or sum(item.file_size for item in files) == 0:
            raise ValueError('Music ZIP is empty')
    else:
        if not any(item.orig_filename.startswith('Dofusic/Musiques/')
                   and item.orig_filename.casefold().endswith('.opus')
                   and item.file_size > 0 for item in files):
            raise ValueError('Final ZIP contains no OPUS music')
        for prefix in ('Dofusic/Dofusic.exe', 'Dofusic/Data/', 'Dofusic/Musiques/'):
            selected = [item for item in files if item.orig_filename == prefix or item.orig_filename.startswith(prefix)]
            if not selected or sum(item.file_size for item in selected) == 0:
                raise ValueError(f'Final ZIP required content is empty or absent: {prefix}')
    corrupt = archive.testzip()
    if corrupt:
        raise ValueError(f'Corrupt ZIP entry: {corrupt}')
    return files


def verify_music_sources(archive_path, music_folder):
    """Require an exact, byte-for-byte match with checked-out source tracks."""
    folder = Path(music_folder)
    source_tracks = {}
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError(f'Music source folder is missing: {folder}')
    for source in folder.rglob('*'):
        if source.is_symlink():
            raise ValueError(f'Unexpected music symlink: {source}')
        if source.is_file() and source.suffix.casefold() in MUSIC_EXTENSIONS:
            if source.stat().st_size == 0:
                raise ValueError(f'Empty source OPUS: {source}')
            name = 'Dofusic/Musiques/' + source.relative_to(folder).as_posix()
            source_tracks[name] = source
    opus_tracks = [path for path in source_tracks.values() if path.suffix.casefold() == '.opus']
    if not opus_tracks:
        raise ValueError('No OPUS music in source checkout')
    with zipfile.ZipFile(archive_path) as archive:
        files = {item.orig_filename: item for item in _validated_entries(archive, 'final')}
        zipped_tracks = {name for name in files if name.startswith('Dofusic/Musiques/')}
        if zipped_tracks != set(source_tracks):
            missing = sorted(set(source_tracks) - zipped_tracks)[:5]
            extra = sorted(zipped_tracks - set(source_tracks))[:5]
            kind = 'OPUS' if all(name.casefold().endswith('.opus') for name in missing + extra) else 'Audio'
            raise ValueError(f'{kind} list mismatch: missing={missing}, extra={extra}')
        for name, source in source_tracks.items():
            kind = 'OPUS' if source.suffix.casefold() == '.opus' else 'Audio'
            if files[name].file_size != source.stat().st_size:
                raise ValueError(f'{kind} size mismatch: {name}')
            with source.open('rb') as original, archive.open(files[name]) as packaged:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(packaged, 'sha256').digest():
                    raise ValueError(f'{kind} SHA256 mismatch: {name}')
    return {'opus_count': len(opus_tracks), 'opus_bytes': sum(p.stat().st_size for p in opus_tracks)}


def extract_music(source, expected_sha256, destination):
    """Hash and validate the entire ZIP before creating extraction directories."""
    destination = Path(destination)
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('Invalid expected SHA256')
    with Path(source).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != expected_sha256:
            raise ValueError(f'Music SHA256 mismatch: {digest}')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            files = _validated_entries(archive, 'music')
            destination.mkdir(parents=True, exist_ok=False)
            for item in files:
                target = destination.joinpath(*item.orig_filename.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as incoming, target.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    return {'sha256': digest, 'file_count': len(files)}


def finalize_release(source, checksum):
    """Validate the final ZIP and hash the same opened file, without extraction."""
    source = Path(source)
    with source.open('rb') as stream:
        with zipfile.ZipFile(stream) as archive:
            files = _validated_entries(archive, 'final')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    Path(checksum).write_text(f'{digest}  {source.name}\n', encoding='ascii', newline='\n')
    return {'sha256': digest, 'file_count': len(files)}


def validate_signed_release(unsigned_source, signed_source, destination):
    """Validate both ZIPs, preserve all paths/non-EXE bytes, then extract only EXE.

    The caller must check Authenticode on the extracted executable before replacing
    the candidate with the returned original ZIP. No candidate bytes are written.
    """
    destination = Path(destination)
    executable = 'Dofusic/Dofusic.exe'
    with Path(unsigned_source).open('rb') as unsigned_stream, Path(signed_source).open('rb') as signed_stream:
        with zipfile.ZipFile(unsigned_stream) as unsigned, zipfile.ZipFile(signed_stream) as signed:
            unsigned_files = {item.orig_filename: item for item in _validated_entries(unsigned, 'final')}
            signed_files = {item.orig_filename: item for item in _validated_entries(signed, 'final')}
            if {item.orig_filename for item in unsigned.infolist()} != {item.orig_filename for item in signed.infolist()}:
                raise ValueError('Signed ZIP changed archive paths')
            for name, item in unsigned_files.items():
                if name == executable:
                    continue
                returned = signed_files[name]
                if item.file_size != returned.file_size:
                    raise ValueError(f'Signed ZIP changed non-executable content: {name}')
                with unsigned.open(item) as original, signed.open(returned) as incoming:
                    if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(incoming, 'sha256').digest():
                        raise ValueError(f'Signed ZIP changed non-executable content: {name}')
            destination.mkdir(parents=True, exist_ok=False)
            target = destination.joinpath(*executable.split('/'))
            target.parent.mkdir(parents=True, exist_ok=False)
            with signed.open(signed_files[executable]) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    return {'file_count': len(signed_files), 'executable': executable}


def verify_source_provenance(archive_path, source_root, manifest_path):
    """Compare the shipped portable with the actual checked-out source tree.

    This is a deterministic consistency check; GitHub's attestation is still
    required for independent proof of the public build's origin.
    """
    # This script runs from .github/scripts, not the repository root: do not
    # assume the root is implicitly present in sys.path on Windows runners.
    import importlib.util
    root = Path(source_root).resolve()
    spec = importlib.util.spec_from_file_location(
        'dofusic_checked_builder', root / 'Data' / 'tools' / 'build_portable.py',
    )
    if spec is None or spec.loader is None:
        raise ValueError('Cannot locate the checked-out source builder')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    try:
        record = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Detached release provenance is missing or invalid') from exc
    with Path(archive_path).open('rb') as stream:
        zip_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if record.get('portable_sha256') != zip_sha:
        raise ValueError('Detached provenance ZIP SHA256 mismatch')
    source_sha, files = builder._source_fingerprint(root)
    if record.get('schema') != 1 or record.get('source_sha256') != source_sha or record.get('source_files') != files:
        raise ValueError('Portable does not match these source files')
    commit = builder._checked_local_commit(root)
    if commit is not None and record.get('git_commit') != commit:
        raise ValueError('Portable commit does not match this Git checkout')
    if record.get('github_source_url') != (
        f'https://github.com/Drentarra/Dofusic/tree/{commit}' if commit else None
    ):
        raise ValueError('Portable source URL does not match this Git checkout')
    return {'source_sha256': source_sha, 'source_files': files, 'git_commit': commit}


def validate_sbom(source, lock):
    """Require CycloneDX 1.6, every installed lock pin and separate RapidOCR."""
    document = json.loads(Path(source).read_text(encoding='utf-8'))
    if document.get('bomFormat') != 'CycloneDX' or document.get('specVersion') != '1.6':
        raise ValueError('SBOM must be CycloneDX 1.6')
    def normalize(name):
        return re.sub(r'[-_.]+', '-', name).lower()
    components = {}
    for component in document.get('components', []):
        name = normalize(component['name'])
        if name in components:
            raise ValueError(f'Duplicate SBOM component: {name}')
        components[name] = component.get('version')
    required = {'rapidocr': '3.9.2'}
    for line in Path(lock).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            name, version = line.split('==')
            required[normalize(name)] = version
    if 'opencv-python' in components:
        raise ValueError('SBOM includes conflicting opencv-python')
    for name, version in required.items():
        if components.get(name) != version:
            raise ValueError(f'SBOM missing installed pin: {name}=={version}')
    return {'spec_version': '1.6', 'component_count': len(components)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    music = commands.add_parser('music')
    music.add_argument('source', type=Path)
    music.add_argument('sha256')
    music.add_argument('destination', type=Path)
    match = commands.add_parser('music-source')
    match.add_argument('archive', type=Path)
    match.add_argument('music_folder', type=Path)
    final = commands.add_parser('final')
    final.add_argument('source', type=Path)
    final.add_argument('checksum', type=Path)
    signed = commands.add_parser('signed')
    signed.add_argument('unsigned_source', type=Path)
    signed.add_argument('signed_source', type=Path)
    signed.add_argument('destination', type=Path)
    provenance = commands.add_parser('provenance')
    provenance.add_argument('archive', type=Path)
    provenance.add_argument('source_root', type=Path)
    provenance.add_argument('manifest', type=Path)
    sbom = commands.add_parser('sbom')
    sbom.add_argument('source', type=Path)
    sbom.add_argument('lock', type=Path)
    args = parser.parse_args()
    if args.command == 'music':
        result = extract_music(args.source, args.sha256, args.destination)
    elif args.command == 'music-source':
        result = verify_music_sources(args.archive, args.music_folder)
    elif args.command == 'final':
        result = finalize_release(args.source, args.checksum)
    elif args.command == 'signed':
        result = validate_signed_release(args.unsigned_source, args.signed_source, args.destination)
    elif args.command == 'provenance':
        result = verify_source_provenance(args.archive, args.source_root, args.manifest)
    else:
        result = validate_sbom(args.source, args.lock)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
