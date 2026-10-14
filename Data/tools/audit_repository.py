"""Inventorier tous les fichiers suivis par Git, sources comme médias.

Ce contrôle protège la traçabilité des livrables sans prétendre tester
l'exécution des ressources binaires. Exécuter depuis une copie Git complète.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


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
    output = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '-z'],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    names = sorted({name.decode('utf-8') for name in output.split(b'\0') if name})
    records = {}
    failures = []
    for name in names:
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
            failures.append(name)
            continue
        records[name] = {'category': category(name), **fingerprint(path)}
    return {
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
        f"- Fichiers Git : **{result['tracked_files']}**",
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
    print(f"Fichiers Git contrôlés : {result['verified_files']}/{result['tracked_files']}")
    return 1 if result['invalid_files'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
