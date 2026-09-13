"""Build-only verification: music ZIP SHA/extraction, final ZIP/hash and SBOM pins."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile


DOS_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'{prefix}{i}' for prefix in ('COM', 'LPT') for i in ('1', '2', '3', '4', '5', '6', '7', '8', '9', '\u00b9', '\u00b2', '\u00b3'))}


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
        for prefix in ('Dofusic/Dofusic.exe', 'Dofusic/Data/', 'Dofusic/Musiques/'):
            selected = [item for item in files if item.orig_filename == prefix or item.orig_filename.startswith(prefix)]
            if not selected or sum(item.file_size for item in selected) == 0:
                raise ValueError(f'Final ZIP required content is empty or absent: {prefix}')
    corrupt = archive.testzip()
    if corrupt:
        raise ValueError(f'Corrupt ZIP entry: {corrupt}')
    return files


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


def validate_sbom(source, lock):
    """Require CycloneDX 1.6, every installed lock pin and separate RapidOCR."""
    document = json.loads(Path(source).read_text(encoding='utf-8'))
    if document.get('bomFormat') != 'CycloneDX' or document.get('specVersion') != '1.6':
        raise ValueError('SBOM must be CycloneDX 1.6')
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
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
    final = commands.add_parser('final')
    final.add_argument('source', type=Path)
    final.add_argument('checksum', type=Path)
    sbom = commands.add_parser('sbom')
    sbom.add_argument('source', type=Path)
    sbom.add_argument('lock', type=Path)
    args = parser.parse_args()
    if args.command == 'music':
        result = extract_music(args.source, args.sha256, args.destination)
    elif args.command == 'final':
        result = finalize_release(args.source, args.checksum)
    else:
        result = validate_sbom(args.source, args.lock)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
