"""Regression coverage for portable read-only data and offline SQL import."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing

import pytest

from dofusic.location.repository import DofusRepository, LocationKind, _connect
from dofusic.models import Coordinates
from tools.database_maintenance import build_database, extract_coordinates_from_source_sqlite


@pytest.mark.parametrize('filename', [
    'zone#with%special.sqlite',
    'zone avec espaces et accents é.sqlite',
    pytest.param('zone#with?special.sqlite', marks=pytest.mark.skipif(
        os.name == 'nt', reason='Windows does not allow ? in filenames')),
])
def test_runtime_connection_cannot_modify_database(tmp_path, filename):
    db = build_database(tmp_path / filename, areas={1: 'Astrub'},
                        subareas={}, subarea_to_area={})
    repository = DofusRepository(db)
    assert repository.get_location(LocationKind.AREA, 1).name == 'Astrub'
    with closing(_connect(db)) as connection:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute("INSERT INTO areas(id, name) VALUES (2, 'Unsafe')")
    assert repository.all_locations()[0].name == 'Astrub'
    assert sorted(path.name for path in tmp_path.iterdir()) == [db.name]


def test_missing_database_during_runtime_does_not_create_empty_sqlite(tmp_path):
    db = build_database(tmp_path / 'game.sqlite', areas={1: 'Astrub'},
                        subareas={}, subarea_to_area={})
    repository = DofusRepository(db)
    db.unlink()
    with pytest.raises(sqlite3.OperationalError):
        repository.all_locations()
    assert not db.exists()


def test_nearby_coordinates_are_unique_and_ordered_by_nearest_distance(tmp_path):
    db = build_database(
        tmp_path / 'nearby.sqlite', areas={9: 'Amakna'},
        subareas={10: 'Near', 11: 'Far'}, subarea_to_area={10: 9, 11: 9},
        map_coordinates=[(1, 2, 0, 10), (2, 0, 0, 10), (3, 1, 0, 11),
                         (4, -1, 0, 11)],
    )
    repository = DofusRepository(db)
    assert [record.id for record in repository.coordinate_candidates(Coordinates(0, 0), tolerance=2)] == [10, 11]
    assert [record.id for record in repository.coordinate_candidates(Coordinates(0, 0))] == [10]


def test_external_database_identifiers_are_escaped(tmp_path):
    """SQLite column names discovered in third-party sources can contain quotes."""
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.executescript('''
            CREATE TABLE "MapInformationData" ("id" INTEGER, "subAreaId" INTEGER);
            CREATE TABLE "MapsCoordinateData" ("id" INTEGER, "x" INTEGER, "y" INTEGER);
            CREATE TABLE "MapsCoordinateData_MapIds_junction" (
                "coordinate""id" INTEGER, "map""id" INTEGER
            );
            INSERT INTO "MapInformationData" VALUES (42, 22);
            INSERT INTO "MapsCoordinateData" VALUES (7, 5, -6);
            INSERT INTO "MapsCoordinateData_MapIds_junction" VALUES (7, 42);
        ''')
    assert extract_coordinates_from_source_sqlite(source) == ((42, 5, -6, 22),)
