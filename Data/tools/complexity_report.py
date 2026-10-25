"""Mesure AST de complexité cyclomatique simplifiée, stable et reproductible.

Compte chaque fonction indépendamment; les sous-fonctions ne sont pas comptées
dans la fonction parente. Ce n'est pas une analyse des chemins sémantiques.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

CONTROL_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp,
                 ast.ExceptHandler, ast.Assert, ast.comprehension)
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def complexity(node: ast.AST) -> int:
    score = 1
    pending = [node]
    while pending:
        current = pending.pop()
        if current is not node and isinstance(current, (*FUNCTION_NODES, ast.ClassDef, ast.Lambda)):
            # ast.walk cannot prune a subtree: skip nested bodies explicitly.
            continue
        pending.extend(ast.iter_child_nodes(current))
        if isinstance(current, CONTROL_NODES):
            score += 1
        elif isinstance(current, ast.BoolOp):
            score += max(0, len(current.values) - 1)
        elif isinstance(current, ast.Match):
            score += max(0, len(current.cases) - 1)
    return score


def measure(root: Path) -> dict:
    results = []
    for path in sorted((root / 'Data' / 'dofusic').rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, FUNCTION_NODES):
                results.append({'file': path.relative_to(root).as_posix(),
                                'function': node.name, 'line': node.lineno,
                                'complexity': complexity(node)})
    values = [item['complexity'] for item in results]
    return {'metric': 'AST approximative: decisions+BoolOp+comprehensions+assertions, base 1',
            'functions': len(results), 'aggregate': sum(values),
            'max': max(values, default=0),
            'over_15': sum(c > 15 for c in values),
            'over_25': sum(c > 25 for c in values),
            'top20': sorted(results, key=lambda x: -x['complexity'])[:20],
            'app': [r for r in results if r['file'] == 'Data/dofusic/app.py']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(measure(args.root.resolve()), indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
