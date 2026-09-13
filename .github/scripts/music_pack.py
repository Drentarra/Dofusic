"""Build the immutable music-v1 archive from the verified v1.0.1 release.

ZIP_STORED avoids compression-library differences for already compressed music.
Run with Python 3.11.9: python .github/scripts/music_pack.py SOURCE OUTPUT
The CLI prints JSON containing sha256, file_count and uncompressed_bytes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import zipfile


SOURCE_SHA256 = 'b282f7dabd465ba9d66ec78d9b1092e77dbb1c7082bf9c4f769784d7e6306c91'
SOURCE_PREFIX = 'Dofusic/Musiques/'
DOS_NAMES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def _safe_relative_path(name):
    parts = name.split('/')
    if any(
        not part or part in {'.', '..'} or part.endswith((' ', '.'))
        or re.search(r'[\\:\x00-\x1f<>"|?*]', part)
        or part.split('.')[0].upper() in DOS_NAMES
        for part in parts
    ):
        raise ValueError(f'Invalid music path: {name!r}')
    return 'Musiques/' + name


def build_pack(source: Path, output: Path):
    """Verify the source before any writes; refuse unsafe or empty music packs."""
    source, output = Path(source), Path(output)
    companion = output.with_suffix(output.suffix + '.sha256')
    if output.exists() or companion.exists():
        raise FileExistsError('Music pack or checksum already exists')
    with source.open('rb') as source_file:
        digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
        if digest != SOURCE_SHA256:
            raise ValueError(f'Source SHA256 mismatch: {digest}')
        source_file.seek(0)
        with zipfile.ZipFile(source_file) as archive:
            selected = []
            seen = set()
            for item in archive.infolist():
                # ZipInfo normalizes backslashes on Windows; validate the raw name.
                original_name = item.orig_filename
                if not original_name.startswith(SOURCE_PREFIX):
                    continue
                relative = original_name[len(SOURCE_PREFIX):]
                if not relative:
                    continue
                path = _safe_relative_path(relative.rstrip('/') if item.is_dir() else relative)
                if item.is_dir():
                    continue
                if stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG):
                    raise ValueError(f'Invalid non-file music path: {item.filename!r}')
                key = path.casefold()
                if key in seen:
                    raise ValueError(f'Music path collision: {path!r}')
                seen.add(key)
                selected.append((path, item))
            if not selected or sum(item.file_size for _, item in selected) == 0:
                raise ValueError('Music pack is empty')
            for path, _ in selected:
                parts = path.casefold().split('/')
                if any('/'.join(parts[:i]) in seen for i in range(1, len(parts))):
                    raise ValueError(f'Music file/folder path collision: {path!r}')
            selected.sort(key=lambda pair: pair[0])
            created_output = created_companion = False
            try:
                with output.open('xb') as target:
                    created_output = True
                    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as pack:
                        for path, item in selected:
                            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                            info.create_system = 3
                            info.external_attr = (stat.S_IFREG | 0o644) << 16
                            info.compress_type = zipfile.ZIP_STORED
                            info.file_size = item.file_size
                            with archive.open(item) as track, pack.open(info, 'w') as dest:
                                shutil.copyfileobj(track, dest, length=1024 * 1024)
                with output.open('rb') as finished:
                    pack_digest = hashlib.file_digest(finished, 'sha256').hexdigest()
                with companion.open('x', encoding='ascii', newline='\n') as checksum:
                    created_companion = True
                    checksum.write(f'{pack_digest}  {output.name}\n')
            except Exception:
                if created_output:
                    output.unlink(missing_ok=True)
                if created_companion:
                    companion.unlink(missing_ok=True)
                raise
    return {'sha256': pack_digest, 'file_count': len(selected),
            'uncompressed_bytes': sum(item.file_size for _, item in selected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build_pack(args.source, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
