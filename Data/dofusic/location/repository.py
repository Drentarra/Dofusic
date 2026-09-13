from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from dataclasses import dataclass

from dofusic.models import Coordinates, LocationKind, LocationRecord

SCHEMA_VERSION = "2"


@dataclass(frozen=True, slots=True)
class PlaceAliasRecord:
    alias: str
    location: LocationRecord
    priority: int = 100


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS areas (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS subareas (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            area_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS map_coordinates (
            map_id INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            subarea_id INTEGER,
            area_id INTEGER,
            PRIMARY KEY (map_id, x, y)
        );

        CREATE INDEX IF NOT EXISTS idx_map_coordinates_xy
            ON map_coordinates (x, y);
        CREATE TABLE IF NOT EXISTS place_aliases (
            alias TEXT NOT NULL,
            target_kind TEXT NOT NULL CHECK(target_kind IN ('area','subarea')),
            target_id INTEGER NOT NULL,
            priority INTEGER NOT NULL DEFAULT 100,
            PRIMARY KEY (alias, target_kind, target_id)
        );

        CREATE INDEX IF NOT EXISTS idx_place_aliases_alias
            ON place_aliases (alias);
        CREATE INDEX IF NOT EXISTS idx_subareas_area_id
            ON subareas (area_id);
        """
    )



def _refresh_count_metadata(conn: sqlite3.Connection) -> None:
    """Keep descriptive metadata aligned with the rows actually stored."""
    counts = {
        "areas_count": conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0],
        "subareas_count": conn.execute("SELECT COUNT(*) FROM subareas").fetchone()[0],
        "coordinates_count": conn.execute("SELECT COUNT(*) FROM map_coordinates").fetchone()[0],
        "place_aliases_count": conn.execute("SELECT COUNT(*) FROM place_aliases").fetchone()[0],
    }
    conn.executemany(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        [(key, str(value)) for key, value in counts.items()],
    )

def build_database(
    db_path: Path | str,
    *,
    areas: Mapping[int, str],
    subareas: Mapping[int, str],
    subarea_to_area: Mapping[int, int],
    map_coordinates: Optional[Iterable[tuple[int, int, int, int | None]]] = None,
    place_aliases: Optional[Iterable[tuple[str, str, int, int]]] = None,
    metadata: Optional[Mapping[str, object]] = None,
) -> Path:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    conn = _connect(path)
    try:
        create_schema(conn)
        conn.executemany(
            "INSERT INTO areas(id, name) VALUES (?, ?)",
            sorted((int(k), str(v)) for k, v in areas.items()),
        )
        conn.executemany(
            "INSERT INTO subareas(id, name, area_id) VALUES (?, ?, ?)",
            sorted(
                (int(sub_id), str(name), int(subarea_to_area[sub_id]) if sub_id in subarea_to_area else None)
                for sub_id, name in subareas.items()
            ),
        )

        inserted_coordinates = 0
        if map_coordinates:
            rows = []
            valid_subareas = {int(sub_id) for sub_id in subareas}
            for map_id, x, y, subarea_id in map_coordinates:
                sid = int(subarea_id) if subarea_id is not None else None
                if sid is None or sid not in valid_subareas:
                    continue
                area_id = subarea_to_area.get(sid)
                rows.append((int(map_id), int(x), int(y), sid, area_id))
            conn.executemany(
                "INSERT OR REPLACE INTO map_coordinates(map_id, x, y, subarea_id, area_id) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            inserted_coordinates = len(rows)

        if place_aliases:
            conn.executemany(
                "INSERT OR REPLACE INTO place_aliases(alias, target_kind, target_id, priority) VALUES (?, ?, ?, ?)",
                [
                    (str(alias), str(kind), int(target_id), int(priority))
                    for alias, kind, target_id, priority in place_aliases
                ],
            )

        meta = {"schema_version": SCHEMA_VERSION}
        if metadata:
            meta.update({str(k): str(v) for k, v in metadata.items()})
        conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            sorted(meta.items()),
        )
        _refresh_count_metadata(conn)
        conn.commit()
    finally:
        conn.close()
    return path


class DofusRepository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)

    def _query(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        conn = _connect(self.db_path)
        try:
            return list(conn.execute(sql, tuple(params)).fetchall())
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
        return tuple(item for item in self.all_locations() if item.kind is LocationKind.AREA)

    def subareas(self) -> tuple[LocationRecord, ...]:
        return tuple(item for item in self.all_locations() if item.kind is LocationKind.SUBAREA)

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
                SELECT DISTINCT s.id, s.name, s.area_id, a.name AS area_name,
                       ABS(mc.x - ?) + ABS(mc.y - ?) AS distance
                FROM map_coordinates mc
                JOIN subareas s ON s.id = mc.subarea_id
                LEFT JOIN areas a ON a.id = s.area_id
                WHERE mc.x BETWEEN ? AND ?
                  AND mc.y BETWEEN ? AND ?
                  AND ABS(mc.x - ?) + ABS(mc.y - ?) <= ?
                ORDER BY distance, s.id
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
        try:
            rows = self._query(
                "SELECT alias, target_kind, target_id, priority FROM place_aliases ORDER BY priority DESC, alias"
            )
        except sqlite3.OperationalError:
            # Schema v1 databases remain readable during migration. The production
            # database upgrader rebuilds them to schema v2, but runtime must never crash.
            return tuple()
        result: list[PlaceAliasRecord] = []
        for row in rows:
            kind = LocationKind.AREA if str(row["target_kind"]) == "area" else LocationKind.SUBAREA
            location = self.get_location(kind, int(row["target_id"]))
            if location is None:
                continue
            result.append(PlaceAliasRecord(str(row["alias"]), location, int(row["priority"])))
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
        return tuple(problems)



def upgrade_database(db_path: Path | str, *, aliases_path: Path | str | None = None) -> Path:
    """Upgrade an existing Dofusic DB in place without deleting map coordinates."""
    import json

    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = _connect(path)
    try:
        create_schema(conn)
        if aliases_path is not None:
            alias_file = Path(aliases_path)
            if alias_file.exists():
                payload = json.loads(alias_file.read_text(encoding='utf-8'))
                rows = payload.get('aliases', []) if isinstance(payload, dict) else []
                conn.execute('DELETE FROM place_aliases')
                conn.executemany(
                    'INSERT OR REPLACE INTO place_aliases(alias, target_kind, target_id, priority) VALUES (?, ?, ?, ?)',
                    [
                        (str(row['alias']), str(row['target_kind']), int(row['target_id']), int(row.get('priority', 100)))
                        for row in rows if isinstance(row, dict)
                    ],
                )
        conn.execute(
            'INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)',
            ('schema_version', SCHEMA_VERSION),
        )
        _refresh_count_metadata(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def import_coordinates_into_database(db_path: Path | str, source_sqlite: Path | str) -> int:
    """Replace only map coordinate rows from a source dofus.sqlite.

    Areas, subareas, aliases and user-facing metadata remain intact.
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    rows = extract_coordinates_from_source_sqlite(source_sqlite)
    if not rows:
        return 0

    conn = _connect(path)
    try:
        create_schema(conn)
        area_by_sub = {int(row['id']): (int(row['area_id']) if row['area_id'] is not None else None)
                       for row in conn.execute('SELECT id, area_id FROM subareas')}
        conn.execute('DELETE FROM map_coordinates')
        payload = []
        for map_id, x, y, subarea_id in rows:
            sid = int(subarea_id) if subarea_id is not None else None
            # The resolver joins map_coordinates to our curated subareas table. Rows
            # for unknown source subareas are therefore unusable at runtime and only
            # bloat the commercial DB / coordinate lookups. Keep resolvable rows only.
            if sid is None or sid not in area_by_sub:
                continue
            payload.append((int(map_id), int(x), int(y), sid, area_by_sub[sid]))
        conn.executemany(
            'INSERT OR REPLACE INTO map_coordinates(map_id, x, y, subarea_id, area_id) VALUES (?, ?, ?, ?, ?)',
            payload,
        )
        conn.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)', ('source_sqlite', Path(source_sqlite).name))
        conn.execute('INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)', ('schema_version', SCHEMA_VERSION))
        _refresh_count_metadata(conn)
        conn.commit()
        return len(payload)
    finally:
        conn.close()

def _table_names(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]).lower(): str(row[0]) for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]).lower(): str(row[1]) for row in rows}


def _pick_column(columns: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        if candidate.lower() in columns:
            return columns[candidate.lower()]
    return None


def extract_coordinates_from_source_sqlite(
    source_path: Path | str,
) -> tuple[tuple[int, int, int, int | None], ...]:
    """Best-effort extraction of (map_id, x, y, subarea_id).

    Supports the current community schema with MapsCoordinateData + junction +
    MapInformationData, plus older MapInformationData tables carrying x/y.
    """
    path = Path(source_path)
    if not path.exists():
        return tuple()

    conn = sqlite3.connect(str(path))
    try:
        tables = _table_names(conn)
        map_info = tables.get("mapinformationdata") or tables.get("mapinformation")
        coord_table = tables.get("mapscoordinatedata") or tables.get("mapcoordinatedata")
        junction = (
            tables.get("mapscoordinatedata_mapids_junction")
            or tables.get("mapcoordinatedata_mapids_junction")
        )
        result: list[tuple[int, int, int, int | None]] = []

        if map_info and coord_table and junction:
            mi_cols = _column_names(conn, map_info)
            co_cols = _column_names(conn, coord_table)
            ju_cols = _column_names(conn, junction)
            map_id_col = _pick_column(mi_cols, "id", "mapId", "map_id")
            subarea_col = _pick_column(mi_cols, "subAreaId", "subarea_id", "subareaid")
            coord_id_col = _pick_column(co_cols, "id", "coordinateId", "coordinate_id")
            x_col = _pick_column(co_cols, "x", "posX", "pos_x")
            y_col = _pick_column(co_cols, "y", "posY", "pos_y")

            junction_coord: str | None = None
            junction_map: str | None = None
            for original in ju_cols.values():
                low = original.lower()
                if "coordinate" in low:
                    junction_coord = original
                elif "map" in low:
                    junction_map = original
            if junction_coord is None:
                candidates = list(ju_cols.values())
                junction_coord = candidates[0] if candidates else None
            if junction_map is None:
                candidates = list(ju_cols.values())
                junction_map = candidates[1] if len(candidates) > 1 else None

            if all((map_id_col, coord_id_col, x_col, y_col, junction_coord, junction_map)):
                sub_expr = f'mi."{subarea_col}"' if subarea_col else "NULL"
                sql = (
                    f'SELECT mi."{map_id_col}", c."{x_col}", c."{y_col}", {sub_expr} '
                    f'FROM "{junction}" j '
                    f'JOIN "{coord_table}" c ON c."{coord_id_col}" = j."{junction_coord}" '
                    f'JOIN "{map_info}" mi ON mi."{map_id_col}" = j."{junction_map}" '
                    f'WHERE c."{x_col}" IS NOT NULL AND c."{y_col}" IS NOT NULL'
                )
                for row in conn.execute(sql):
                    try:
                        result.append(
                            (
                                int(row[0]),
                                int(row[1]),
                                int(row[2]),
                                int(row[3]) if row[3] is not None else None,
                            )
                        )
                    except (TypeError, ValueError):
                        continue
                if result:
                    return tuple(result)

        if map_info:
            cols = _column_names(conn, map_info)
            map_id_col = _pick_column(cols, "id", "mapId", "map_id")
            subarea_col = _pick_column(cols, "subAreaId", "subarea_id", "subareaid")
            x_col = _pick_column(cols, "x", "posX", "pos_x")
            y_col = _pick_column(cols, "y", "posY", "pos_y")
            if all((map_id_col, x_col, y_col)):
                sub_expr = f'"{subarea_col}"' if subarea_col else "NULL"
                sql = (
                    f'SELECT "{map_id_col}", "{x_col}", "{y_col}", {sub_expr} '
                    f'FROM "{map_info}" '
                    f'WHERE "{x_col}" IS NOT NULL AND "{y_col}" IS NOT NULL'
                )
                for row in conn.execute(sql):
                    try:
                        result.append(
                            (
                                int(row[0]),
                                int(row[1]),
                                int(row[2]),
                                int(row[3]) if row[3] is not None else None,
                            )
                        )
                    except (TypeError, ValueError):
                        continue

        return tuple(result)
    finally:
        conn.close()
