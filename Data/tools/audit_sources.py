"""Inventaire statique reproductible de l'ensemble des sources et tests Python.

Vérifie la syntaxe et les définitions qui se masquent dans une portée Python.
Les exceptions larges sont signalées pour une revue humaine, sans les assimiler
automatiquement à des défauts (Win32 et Tkinter comportent des replis légitimes).
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


SOURCE_FOLDERS = ('Data/dofusic', 'Data/tools', '.github/scripts', 'tests')
SOURCE_FILES = ('Data/main.py',)


def python_paths(root: Path) -> tuple[Path, ...]:
    paths = [root / name for name in SOURCE_FILES if (root / name).is_file()]
    for folder in SOURCE_FOLDERS:
        paths.extend((root / folder).rglob('*.py'))
    return tuple(sorted(set(paths)))


def _is_overload_or_setter(node: ast.AST) -> bool:
    for decorator in getattr(node, 'decorator_list', ()):
        if isinstance(decorator, ast.Name) and decorator.id == 'overload':
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr in ('setter', 'deleter', 'getter'):
            return True
    return False


def analyze_python(source: str, *, filename: str) -> dict:
    tree = ast.parse(source, filename=filename)
    conflicts = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defined: dict[str, int] = {}
        for node in scope.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name in defined and not _is_overload_or_setter(node):
                conflicts.append({
                    'name': name,
                    'first_line': defined[name],
                    'second_line': node.lineno,
                    'scope': getattr(scope, 'name', '<module>'),
                })
            else:
                defined[name] = node.lineno

    definitions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    broad_exceptions = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler)
        and (n.type is None or isinstance(n.type, ast.Name) and n.type.id in ('Exception', 'BaseException'))
    ]
    imports = sorted({
        alias.name.partition('.')[0]
        for n in ast.walk(tree) if isinstance(n, ast.Import)
        for alias in n.names
    } | {
        n.module.partition('.')[0]
        for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    })
    return {
        'lines': len(source.splitlines()),
        'classes': sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree)),
        'functions': len(definitions),
        'broad_except_lines': broad_exceptions,
        'masked_definitions': conflicts,
        'top_level_imports': imports,
    }


def collect(root: Path) -> dict:
    files = {}
    invalid = {}
    for path in python_paths(root):
        relative = path.relative_to(root).as_posix()
        try:
            files[relative] = analyze_python(path.read_text(encoding='utf-8-sig'), filename=relative)
        except (SyntaxError, UnicodeError) as exc:
            invalid[relative] = str(exc)
    masked = {
        key: record['masked_definitions']
        for key, record in files.items() if record['masked_definitions']
    }
    return {
        'source_root': 'Dofusic',
        'python_files_checked': len(files) + len(invalid),
        'lines_checked': sum(item['lines'] for item in files.values()),
        'invalid_python_files': invalid,
        'duplicate_definitions': masked,
        'files': files,
    }


def report(result: dict) -> str:
    lines = [
        '# Inventaire statique complet des sources Python',
        '',
        f"- Fichiers analysés : **{result['python_files_checked']}**",
        f"- Lignes analysées : **{result['lines_checked']}**",
        f"- Fichiers Python invalides : **{len(result['invalid_python_files'])}**",
        f"- Portées avec des définitions masquées : **{len(result['duplicate_definitions'])}**",
        '',
        'Ce contrôle porte sur la syntaxe, les portées, les imports et la structure.',
        'Il ne démontre pas que toutes les branches fonctionnelles ont été exécutées',
        'ni que toutes les exceptions larges sont incorrectes.',
        '',
        '| Fichier | Lignes | Classes | Fonctions | Exceptions larges |',
        '|---|---:|---:|---:|---:|',
    ]
    for name, record in result['files'].items():
        lines.append(
            f"| `{name}` | {record['lines']} | {record['classes']} "
            f"| {record['functions']} | {len(record['broad_except_lines'])} |"
        )
    if result['invalid_python_files'] or result['duplicate_definitions']:
        lines.append('')
        lines.append('## Anomalies à corriger')
        lines.append('```json')
        lines.append(json.dumps({
            'invalid': result['invalid_python_files'],
            'duplicates': result['duplicate_definitions'],
        }, ensure_ascii=False, indent=2))
        lines.append('```')
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.root.resolve())
    for target, value in (
        (args.output, json.dumps(result, indent=2, ensure_ascii=False) + '\n'),
        (args.summary, report(result)),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8')
    print(f"Sources : {result['python_files_checked']} fichiers / {result['lines_checked']} lignes")
    print(f"Synthèse : {args.summary}")
    if result['invalid_python_files'] or result['duplicate_definitions']:
        print('ERREUR : syntaxe invalide ou définitions masquées', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
