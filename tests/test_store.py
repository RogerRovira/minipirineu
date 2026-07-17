"""Verification store: long-format SQLite with idempotent upserts.

Re-ingesting a payload (or re-running a backfill chunk) must be a no-op, so
the store can always be rebuilt from the archive without duplicate rows.
"""

from minipirineu.store import Row, connect, upsert_rows


def _row(**overrides) -> Row:
    base = {
        "source": "xema",
        "station": "Z1",
        "run_time_utc": "2026-07-17T11:00Z",
        "valid_time_utc": "2026-07-17T11:00Z",
        "variable": "obs.gruix_neu",
        "value": 42.0,
    }
    return Row(**{**base, **overrides})


def _count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]


def test_upsert_is_idempotent(tmp_path):
    conn = connect(tmp_path / "v.sqlite")
    rows = [_row(), _row(variable="obs.temperatura", value=-3.5)]
    assert upsert_rows(conn, rows) == 2
    upsert_rows(conn, rows)
    assert _count(conn) == 2


def test_upsert_updates_value_on_same_key(tmp_path):
    conn = connect(tmp_path / "v.sqlite")
    upsert_rows(conn, [_row(value=10.0)])
    upsert_rows(conn, [_row(value=11.5)])  # XEMA revises readings
    assert _count(conn) == 1
    value = conn.execute("SELECT value FROM verification_values").fetchone()[0]
    assert value == 11.5


def test_none_value_is_storable(tmp_path):
    conn = connect(tmp_path / "v.sqlite")
    upsert_rows(conn, [_row(value=None)])
    value = conn.execute("SELECT value FROM verification_values").fetchone()[0]
    assert value is None


def test_rows_differing_in_any_key_field_coexist(tmp_path):
    conn = connect(tmp_path / "v.sqlite")
    upsert_rows(
        conn,
        [
            _row(),
            _row(station="Z2"),
            _row(variable="obs.precipitacio"),
            _row(valid_time_utc="2026-07-17T11:30Z"),
            _row(run_time_utc="2026-07-17T12:00Z"),
            _row(source="openmeteo"),
        ],
    )
    assert _count(conn) == 6


def test_connect_creates_parent_directories(tmp_path):
    conn = connect(tmp_path / "deep" / "nested" / "v.sqlite")
    upsert_rows(conn, [_row()])
    assert _count(conn) == 1
