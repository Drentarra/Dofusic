"""Inventaire SHA-256 des fichiers suivis Git ou présents dans un ZIP source.

Ce contrôle vérifie la lisibilité et les empreintes des ressources, sans
prétendre valider leur exécution. Les artefacts de build sont exclus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def category(path: str) -> str:
    if path.startswith('Musiques/'):
        return 'musique'
    if path.endswith('.py'):
        return 'source Python'
    if path.endswith('.sqlite'):
        return 'base SQLite'
    if path.endswith(('.json', '.yaml', '.yml')):
        return 'données / configuration'
    if path.endswith(('.png', '.ico', '.jpg', '.jpeg')):
        return 'image'
    if path.endswith(('.md', '.txt', '.bat')):
        return 'documentation / automatisation'
    return 'autre'


def fingerprint(path: Path) -> dict:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def collect(root: Path) -> dict:
    root = root.resolve()
    # Prefer the Git index when present: it remains the authoritative source
    # for releases checked out by CI. Downloaded GitHub source ZIPs contain no
    # .git index, so audit their complete on-disk source inventory instead.
    names: list[str]
    source = 'filesystem'
    if (root / '.git').exists() and shutil.which('git') is not None:
        try:
            output = subprocess.run(
                ['git', '-C', str(root), 'ls-files', '-z'],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout
            names = sorted({name.decode('utf-8') for name in output.split(b'\0') if name})
            source = 'git'
        except (OSError, subprocess.CalledProcessError):
            names = []
    else:
        names = []
    if source == 'filesystem':
        excluded = {'.git', '.portable-build', 'Release', '__pycache__', '.pytest_cache',
                    '.venv', '.mypy_cache', '.ruff_cache', '.tox', 'build', 'dist',
                    'Audit', '.audit-reference', '.sbom-venv', 'Models'}
        names = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob('*')
            if path.is_file() and not any(part in excluded for part in path.relative_to(root).parts)
            and path.name != '.coverage'
            and path.suffix not in {'.pyc', '.pyo'}
        )
    records = {}
    failures = []
    for name in names:
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
            failures.append(name)
            continue
        records[name] = {'category': category(name), **fingerprint(path)}
    return {
        'inventory_source': source,
        'tracked_files': len(names),
        'verified_files': len(records),
        'size_bytes': sum(item['bytes'] for item in records.values()),
        'invalid_files': failures,
        'files': records,
    }


def summary(result: dict) -> str:
    rows = [
        '# Inventaire intégral des fichiers suivis dans Dofusic',
        '',
        f"- Mode inventaire : **{result.get('inventory_source', 'git')}**",
        f"- Fichiers contrôlés : **{result['tracked_files']}**",
        f"- Fichiers lisibles et empreintés : **{result['verified_files']}**",
        f"- Octets suivis : **{result['size_bytes']}**",
        f"- Anomalies d'accès : **{len(result['invalid_files'])}**",
        '',
        'Les empreintes SHA-256 vérifient les octets présents au moment du build.',
        'Elles ne prouvent ni que le contenu est utile, ni qu’il fonctionne dans Dofus.',
        '',
        '| Fichier | Type | Octets | SHA-256 (début) |',
        '|---|---|---:|---|',
    ]
    for name, item in result['files'].items():
        rows.append(
            f"| `{name}` | {item['category']} | {item['bytes']} "
            f"| `{item['sha256'][:16]}` |"
        )
    return '\n'.join(rows) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.root)
    for path, text in (
        (args.output, json.dumps(result, ensure_ascii=False, indent=2) + '\n'),
        (args.summary, summary(result)),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    print(f"Fichiers contrôlés ({result['inventory_source']}) : {result['verified_files']}/{result['tracked_files']}")
    return 1 if result['invalid_files'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
