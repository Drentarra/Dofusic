"""Offline database creation, repair and imports. Not part of the bundled runtime.

Use this module from development tools and tests; application code imports only
`dofusic.location.repository` so PyInstaller does not include these routines.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable, Mapping, Optional

from dofusic.location.repository import SCHEMA_VERSION, _read_only_uri


def _connect_write(path: Path) -> sqlite3.Connection:
    """Writable connection restricted to the offline database-maintenance tool."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
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
    # Build beside the destination: a failed import must not destroy the
    # previous database, and os.replace() is atomic on the same filesystem.
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    os.close(fd)
    staged = Path(name)
    try:
        _populate_database(staged, areas=areas, subareas=subareas,
                           subarea_to_area=subarea_to_area,
                           map_coordinates=map_coordinates, place_aliases=place_aliases,
                           metadata=metadata)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return path


def _populate_database(
    path: Path,
    *,
    areas: Mapping[int, str],
    subareas: Mapping[int, str],
    subarea_to_area: Mapping[int, int],
    map_coordinates: Optional[Iterable[tuple[int, int, int, int | None]]],
    place_aliases: Optional[Iterable[tuple[str, str, int, int]]],
    metadata: Optional[Mapping[str, object]],
) -> None:
    conn = _connect_write(path)
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
        # Commit first so the check validates exactly what will be published.
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise sqlite3.DatabaseError('Base reconstruite non intègre')
    finally:
        conn.close()


def upgrade_database(db_path: Path | str, *, aliases_path: Path | str | None = None) -> Path:
    """Upgrade an existing Dofusic DB in place without deleting map coordinates."""
    import json

    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = _connect_write(path)
    try:
        create_schema(conn)
        # SQLite's composite PK already indexes its leading `alias` column.
        # Older generated DBs had an additional duplicate index.
        conn.execute('DROP INDEX IF EXISTS idx_place_aliases_alias')
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

    conn = _connect_write(path)
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


def _sql_identifier(value: str) -> str:
    """Quote identifiers from foreign SQLite schemas, never interpolate raw names."""
    return '"' + value.replace('"', '""') + '"'


def _column_names(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f'PRAGMA table_info({_sql_identifier(table)})').fetchall()
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

    conn = sqlite3.connect(_read_only_uri(path), uri=True)
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
                sub_expr = f'mi.{_sql_identifier(subarea_col)}' if subarea_col else "NULL"
                mi_map = _sql_identifier(map_id_col)
                co_x, co_y = _sql_identifier(x_col), _sql_identifier(y_col)
                sql = (
                    f'SELECT mi.{mi_map}, c.{co_x}, c.{co_y}, {sub_expr} '
                    f'FROM {_sql_identifier(junction)} j '
                    f'JOIN {_sql_identifier(coord_table)} c '
                    f'ON c.{_sql_identifier(coord_id_col)} = j.{_sql_identifier(junction_coord)} '
                    f'JOIN {_sql_identifier(map_info)} mi '
                    f'ON mi.{mi_map} = j.{_sql_identifier(junction_map)} '
                    f'WHERE c.{co_x} IS NOT NULL AND c.{co_y} IS NOT NULL'
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
                sub_expr = _sql_identifier(subarea_col) if subarea_col else "NULL"
                mi_map = _sql_identifier(map_id_col)
                co_x, co_y = _sql_identifier(x_col), _sql_identifier(y_col)
                sql = (
                    f'SELECT {mi_map}, {co_x}, {co_y}, {sub_expr} '
                    f'FROM {_sql_identifier(map_info)} '
                    f'WHERE {co_x} IS NOT NULL AND {co_y} IS NOT NULL'
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
