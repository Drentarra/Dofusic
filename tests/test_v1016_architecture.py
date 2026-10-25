"""Static packaging boundaries and regression tests for the 1.0.16 cleanup."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import numpy as np

from dofusic.audio.player import MusicPlayer
from dofusic.audio.spectrum import compute_spectrum_chunks
from dofusic.audio.spectrum import compute_spectrum_chunks as direct_spectrum
from dofusic.location.repository import DofusRepository
from dofusic.vision.overlay_projection import project_position_overlay
from tools.database_maintenance import build_database, create_schema, upgrade_database

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_sqlite_excludes_maintenance_and_reads_built_database(tmp_path):
    from dofusic.location import repository

    assert not hasattr(repository, 'build_database')
    assert not hasattr(repository, 'extract_coordinates_from_source_sqlite')
    path = build_database(tmp_path / 'test.sqlite', areas={11: 'Amakna'},
                          subareas={21: 'Village'}, subarea_to_area={21: 11})
    assert DofusRepository(path).get_location(repository.LocationKind.AREA, 11).name == 'Amakna'
    assert not (tmp_path / 'test.sqlite.tmp').exists()


def test_alias_primary_key_index_replaces_duplicate_index(tmp_path):
    db = tmp_path / 'alias.sqlite'
    con = sqlite3.connect(db)
    create_schema(con)
    con.execute("INSERT INTO place_aliases VALUES ('astrub', 'area', 1, 100)")
    con.commit()
    indexes = {row[1] for row in con.execute('PRAGMA index_list(place_aliases)')}
    assert 'idx_place_aliases_alias' not in indexes
    plan = con.execute('EXPLAIN QUERY PLAN SELECT * FROM place_aliases WHERE alias=?',
                       ('astrub',)).fetchall()
    assert any('sqlite_autoindex_place_aliases' in row[3] for row in plan)
    con.close()
    upgrade_database(db)
    with sqlite3.connect(db) as check:
        assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert check.execute('SELECT COUNT(*) FROM place_aliases').fetchone()[0] == 1


def test_original_sqlite_remains_consistent_and_without_duplicate_alias_index():
    db = ROOT / 'Data' / 'dofus_data.sqlite'
    with sqlite3.connect(db) as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        indexes = {row[1] for row in con.execute('PRAGMA index_list(place_aliases)')}
        assert 'idx_place_aliases_alias' not in indexes
        assert con.execute('SELECT COUNT(*) FROM map_coordinates').fetchone()[0] >= 15000


def test_player_uses_exact_same_pure_fft_implementation():
    assert compute_spectrum_chunks is direct_spectrum
    assert callable(MusicPlayer._start_loudness_analysis)
    assert callable(MusicPlayer._transcode_playback_fallback)
    assert callable(MusicPlayer._cancel_decode)
    frames, count = direct_spectrum((np.zeros(2048, np.float32),), 16000)
    assert count == 2048
    assert frames.shape[1] == 24


def test_removed_controller_wrapper_has_pure_projection_replacement():
    from dofusic.app import DofusicController
    assert not hasattr(DofusicController, '_position_overlay_screen_rect')
    assert callable(project_position_overlay)
    assert callable(DofusicController._poll_workers)
    assert callable(DofusicController._play_track)


def test_runtime_imports_do_not_pull_in_development_tools():
    sources = ROOT / 'Data' / 'dofusic'
    for file in sources.rglob('*.py'):
        tree = ast.parse(file.read_text(encoding='utf-8'), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith('tools.'), file
            elif isinstance(node, ast.Import):
                assert all(not name.name.startswith('tools.') for name in node.names), file
    assert not (sources / 'vision' / 'image_io.py').exists()
    assert (ROOT / 'Data' / 'tools' / 'image_io.py').is_file()


def test_version_matches_release_source_without_hardcoded_release_artifact():
    from dofusic.version import __version__, portable_zip_name
    assert len(__version__.split('.')) == 3
    assert all(part.isdigit() for part in __version__.split('.'))
    assert portable_zip_name().startswith(f'Dofusic_{__version__}_')
    assert 'combat-shapes' not in (ROOT / '.github/workflows/ci.yml').read_text()
