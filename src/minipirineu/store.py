"""Verification store: long-format SQLite with idempotent upserts (ADR-0002).

Holds observations, forecast rows for scoring, and derived diagnostics — never
anything the rendered site reads (that stays in data/*.json on main). Schema
ported from PiriNeu `db.py`: run_time vs valid_time is what lets the system
measure its own forecasts (observations store their reading time as both).
Upserts are idempotent so the store is always rebuildable from the archive.
"""

import sqlite3
from pathlib import Path
from typing import Iterable, NamedTuple


class Row(NamedTuple):
    source: str
    station: str
    run_time_utc: str
    valid_time_utc: str
    variable: str
    value: float | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS verification_values (
    source          TEXT NOT NULL,
    station         TEXT NOT NULL,
    run_time_utc    TEXT NOT NULL,
    valid_time_utc  TEXT NOT NULL,
    variable        TEXT NOT NULL,
    value           REAL,
    PRIMARY KEY (source, station, run_time_utc, valid_time_utc, variable)
)
"""

_UPSERT = """
INSERT INTO verification_values
    (source, station, run_time_utc, valid_time_utc, variable, value)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (source, station, run_time_utc, valid_time_utc, variable)
DO UPDATE SET value = excluded.value
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    return conn


def upsert_rows(conn: sqlite3.Connection, rows: Iterable[Row]) -> int:
    rows = list(rows)
    conn.executemany(_UPSERT, rows)
    conn.commit()
    return len(rows)
