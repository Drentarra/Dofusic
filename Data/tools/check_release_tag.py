"""Validate that a requested release tag matches the application's canonical version."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Can run from any working directory, including GitHub Actions on Windows.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dofusic.version import __version__  # noqa: E402


def validate_tag(tag: str) -> None:
    """An empty tag means CI build-only; a release must match exactly."""
    if tag and tag != f'v{__version__}':
        raise ValueError(f'Release tag {tag!r} does not match source version v{__version__}')


if __name__ == '__main__':
    # A missing tag is intentional on build-only workflow_dispatch runs.
    if len(sys.argv) > 2:
        raise SystemExit('Usage: check_release_tag.py [tag]')
    validate_tag(sys.argv[1] if len(sys.argv) == 2 else os.environ.get('RELEASE_TAG', ''))
