from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dofusic.models import Coordinates, LocationKind, LocationRecord

SCHEMA_VERSION = "2"


@dataclass(frozen=True, slots=True)
class PlaceAliasRecord:
    alias: str
    location: LocationRecord
    priority: int = 100


def _read_only_uri(path: Path) -> str:
    """SQLite URI for local disks and Windows UNC network shares.

    A Windows UNC Path.as_uri() has a remote authority (file://server/share),
    which stock SQLite rejects. A four-slash URI instead encodes the network
    path in its path component, leaving the authority empty.
    """
    uri = path.resolve().as_uri()
    if uri.startswith('file://') and not uri.startswith('file:///'):
        uri = 'file:////' + uri[len('file://'):]
    return uri + '?mode=ro'


def _connect(path: Path) -> sqlite3.Connection:
    # The runtime never modifies the bundled geography database. Using the
    # SQLite URI read-only mode prevents accidental writes and, importantly,
    # avoids silently recreating an empty database if a portable drive loses
    # the file between initialization and a query. as_uri() also escapes
    # special filename characters (including # and ?).
    conn = sqlite3.connect(_read_only_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class DofusRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

    def _query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        conn = _connect(self.db_path)
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _row_to_location(row: sqlite3.Row, kind: LocationKind) -> LocationRecord:
        return LocationRecord(
            id=int(row["id"]),
            name=str(row["name"]),
            kind=kind,
            parent_area_id=int(row["area_id"]) if row["area_id"] is not None else None,
            parent_area_name=str(row["area_name"]) if row["area_name"] is not None else None,
        )

    def get_location(self, kind: LocationKind, location_id: int) -> LocationRecord | None:
        if kind is LocationKind.AREA:
            rows = self._query(
                "SELECT a.id, a.name, NULL AS area_id, NULL AS area_name FROM areas a WHERE a.id = ?",
                (int(location_id),),
            )
        else:
            rows = self._query(
                """
                SELECT s.id, s.name, s.area_id, a.name AS area_name
                FROM subareas s
                LEFT JOIN areas a ON a.id = s.area_id
                WHERE s.id = ?
                """,
                (int(location_id),),
            )
        return self._row_to_location(rows[0], kind) if rows else None

    def all_locations(self) -> tuple[LocationRecord, ...]:
        result: list[LocationRecord] = []
        for row in self._query("SELECT id, name, NULL AS area_id, NULL AS area_name FROM areas ORDER BY id"):
            result.append(self._row_to_location(row, LocationKind.AREA))
        for row in self._query(
            """
            SELECT s.id, s.name, s.area_id, a.name AS area_name
            FROM subareas s LEFT JOIN areas a ON a.id = s.area_id ORDER BY s.id
            """
        ):
            result.append(self._row_to_location(row, LocationKind.SUBAREA))
        return tuple(result)

    def areas(self) -> tuple[LocationRecord, ...]:
        return tuple(self._row_to_location(row, LocationKind.AREA) for row in self._query(
            "SELECT id, name, NULL AS area_id, NULL AS area_name FROM areas ORDER BY id"
        ))

    def subareas(self) -> tuple[LocationRecord, ...]:
        return tuple(self._row_to_location(row, LocationKind.SUBAREA) for row in self._query(
            """SELECT s.id, s.name, s.area_id, a.name AS area_name
               FROM subareas s LEFT JOIN areas a ON a.id = s.area_id ORDER BY s.id"""
        ))

    def parent_area(self, subarea: LocationRecord | int) -> LocationRecord | None:
        if isinstance(subarea, int):
            record = self.get_location(LocationKind.SUBAREA, subarea)
        else:
            record = subarea
        if not record or record.kind is not LocationKind.SUBAREA or record.parent_area_id is None:
            return None
        return self.get_location(LocationKind.AREA, record.parent_area_id)

    def coordinate_candidates(self, coordinates: Coordinates, tolerance: int = 0) -> tuple[LocationRecord, ...]:
        tolerance = max(0, int(tolerance))
        if tolerance == 0:
            rows = self._query(
                """
                SELECT DISTINCT s.id, s.name, s.area_id, a.name AS area_name
                FROM map_coordinates mc
                JOIN subareas s ON s.id = mc.subarea_id
                LEFT JOIN areas a ON a.id = s.area_id
                WHERE mc.x = ? AND mc.y = ?
                ORDER BY s.id
                """,
                (coordinates.x, coordinates.y),
            )
        else:
            rows = self._query(
                """
                SELECT s.id, s.name, s.area_id, a.name AS area_name,
                       hits.distance
                FROM (
                    SELECT mc.subarea_id,
                           MIN(ABS(mc.x - ?) + ABS(mc.y - ?)) AS distance
                    FROM map_coordinates mc
                    WHERE mc.x BETWEEN ? AND ?
                      AND mc.y BETWEEN ? AND ?
                      AND ABS(mc.x - ?) + ABS(mc.y - ?) <= ?
                    GROUP BY mc.subarea_id
                ) AS hits
                JOIN subareas s ON s.id = hits.subarea_id
                LEFT JOIN areas a ON a.id = s.area_id
                ORDER BY hits.distance, s.id
                """,
                (
                    coordinates.x, coordinates.y,
                    coordinates.x - tolerance, coordinates.x + tolerance,
                    coordinates.y - tolerance, coordinates.y + tolerance,
                    coordinates.x, coordinates.y, tolerance,
                ),
            )
        return tuple(self._row_to_location(row, LocationKind.SUBAREA) for row in rows)

    def place_aliases(self) -> tuple[PlaceAliasRecord, ...]:
        # Resolve every alias in one SELECT, avoiding one SQLite connection and
        # two lookups per alias on installations with custom place aliases.
        # LEFT JOINs also preserve the previous behaviour for dangling ids.
        try:
            rows = self._query(
                """SELECT p.alias, p.target_kind, p.target_id, p.priority,
                          a.id AS aid, a.name AS aname,
                          s.id AS sid, s.name AS sname, s.area_id AS parent_id,
                          pa.name AS parent_name
                   FROM place_aliases AS p
                   LEFT JOIN areas AS a
                     ON p.target_kind = 'area' AND a.id = p.target_id
                   LEFT JOIN subareas AS s
                     ON p.target_kind != 'area' AND s.id = p.target_id
                   LEFT JOIN areas AS pa ON pa.id = s.area_id
                   ORDER BY p.priority DESC, p.alias"""
            )
        except sqlite3.OperationalError:
            # Legacy v1 databases legitimately have no place_aliases table.
            return tuple()
        result: list[PlaceAliasRecord] = []
        for row in rows:
            if str(row['target_kind']) == 'area':
                if row['aid'] is None:
                    continue
                location = LocationRecord(
                    id=int(row['aid']), name=str(row['aname']), kind=LocationKind.AREA,
                    parent_area_id=None, parent_area_name=None,
                )
            else:
                if row['sid'] is None:
                    continue
                location = LocationRecord(
                    id=int(row['sid']), name=str(row['sname']), kind=LocationKind.SUBAREA,
                    parent_area_id=int(row['parent_id']) if row['parent_id'] is not None else None,
                    parent_area_name=str(row['parent_name']) if row['parent_name'] is not None else None,
                )
            result.append(PlaceAliasRecord(str(row['alias']), location, int(row['priority'])))
        return tuple(result)

    def metadata(self) -> dict[str, str]:
        return {str(row["key"]): str(row["value"]) for row in self._query("SELECT key, value FROM metadata")}

    def counts(self) -> dict[str, int]:
        conn = _connect(self.db_path)
        try:
            return {
                "areas": int(conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0]),
                "subareas": int(conn.execute("SELECT COUNT(*) FROM subareas").fetchone()[0]),
                "map_coordinates": int(conn.execute("SELECT COUNT(*) FROM map_coordinates").fetchone()[0]),
                "place_aliases": int(conn.execute("SELECT COUNT(*) FROM place_aliases").fetchone()[0]),
            }
        finally:
            conn.close()

    def coordinate_coverage(self) -> dict[str, int]:
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS map_coordinates,
                    COUNT(DISTINCT subarea_id) AS mapped_subareas,
                    COUNT(DISTINCT area_id) AS mapped_areas,
                    COUNT(DISTINCT printf('%d,%d', x, y)) AS distinct_xy
                FROM map_coordinates
                """
            ).fetchone()
            return {
                "map_coordinates": int(row["map_coordinates"] or 0),
                "mapped_subareas": int(row["mapped_subareas"] or 0),
                "mapped_areas": int(row["mapped_areas"] or 0),
                "distinct_xy": int(row["distinct_xy"] or 0),
            }
        finally:
            conn.close()

    def integrity_problems(self) -> tuple[str, ...]:
        problems: list[str] = []
        for row in self._query(
            """
            SELECT s.id, s.name, s.area_id
            FROM subareas s
            LEFT JOIN areas a ON a.id = s.area_id
            WHERE s.area_id IS NOT NULL AND a.id IS NULL
            ORDER BY s.id
            """
        ):
            problems.append(
                f"SubArea {row['id']} ({row['name']}) references missing Area {row['area_id']}"
            )
        for row in self._query(
            """
            SELECT mc.map_id, mc.x, mc.y, mc.subarea_id
            FROM map_coordinates mc
            LEFT JOIN subareas s ON s.id = mc.subarea_id
            WHERE mc.subarea_id IS NULL OR s.id IS NULL
            ORDER BY mc.map_id, mc.x, mc.y
            """
        ):
            problems.append(
                f"MapCoordinate {row['map_id']} ({row['x']},{row['y']}) references missing SubArea {row['subarea_id']}"
            )
        # SQLite tables shipped with older releases have no foreign keys.
        # Validate all cross-table references explicitly, including aliases.
        for row in self._query(
            """
            SELECT mc.map_id, mc.x, mc.y, mc.area_id
            FROM map_coordinates mc
            LEFT JOIN areas a ON a.id = mc.area_id
            WHERE mc.area_id IS NOT NULL AND a.id IS NULL
            ORDER BY mc.map_id, mc.x, mc.y
            """
        ):
            problems.append(
                f"MapCoordinate {row['map_id']} ({row['x']},{row['y']}) references missing Area {row['area_id']}"
            )
        for row in self._query(
            """
            SELECT mc.map_id, mc.x, mc.y, mc.area_id, s.area_id AS expected_area
            FROM map_coordinates mc
            JOIN subareas s ON s.id = mc.subarea_id
            WHERE mc.area_id IS NOT NULL AND s.area_id IS NOT NULL AND mc.area_id != s.area_id
            ORDER BY mc.map_id, mc.x, mc.y
            """
        ):
            problems.append(
                f"MapCoordinate {row['map_id']} ({row['x']},{row['y']}) Area {row['area_id']} "
                f"does not match SubArea Area {row['expected_area']}"
            )
        for kind, target_table in (('area', 'areas'), ('subarea', 'subareas')):
            # Table names are hardcoded, not constructed from database input.
            for row in self._query(
                f"""
                SELECT p.alias, p.target_id
                FROM place_aliases p
                LEFT JOIN {target_table} t ON t.id = p.target_id
                WHERE p.target_kind = ? AND t.id IS NULL
                ORDER BY p.alias, p.target_id
                """,
                (kind,),
            ):
                problems.append(
                    f"Alias {row['alias']!r} references missing {kind} {row['target_id']}"
                )
        return tuple(problems)
