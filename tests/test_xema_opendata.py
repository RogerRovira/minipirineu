import json
from pathlib import Path

import pytest

from minipirineu import store, xema_opendata
from minipirineu.config import XEMA_VARIABLES

FIXTURES = Path(__file__).parent / "fixtures"
# Real extract: Z1 + Z9, scored variables, 2026-02-01 10:00–13:00 UTC.
FIXTURE = FIXTURES / "xema_opendata_z1_z9_20260201.json"


def fixture_rows():
    return json.loads(FIXTURE.read_text())


# --- query builder ----------------------------------------------------------

def test_build_query_filters_order_and_columns():
    params = xema_opendata.build_query(
        ["Z1", "Z9"], ["38", "32"], "2024-01-01T00:00:00", "2024-02-01T00:00:00"
    )
    assert params["$select"] == "codi_estacio,codi_variable,data_lectura,valor_lectura,codi_base"
    assert "codi_estacio in ('Z1','Z9')" in params["$where"]
    assert "codi_variable in ('38','32')" in params["$where"]
    # deterministic total order is what makes $offset paging safe
    assert params["$order"] == "data_lectura,codi_estacio,codi_variable,codi_base"


def test_build_query_window_is_half_open():
    # adjacent months must not both claim the boundary reading
    params = xema_opendata.build_query(
        ["Z1"], ["38"], "2024-01-01T00:00:00", "2024-02-01T00:00:00"
    )
    assert "data_lectura >= '2024-01-01T00:00:00'" in params["$where"]
    assert "data_lectura < '2024-02-01T00:00:00'" in params["$where"]


def test_build_query_paging():
    p = xema_opendata.build_query(["Z1"], ["38"], "a", "b", limit=1000, offset=3000)
    assert p["$limit"] == 1000 and p["$offset"] == 3000


def test_app_token_header_present_only_when_env_set(monkeypatch):
    monkeypatch.delenv(xema_opendata.APP_TOKEN_ENV, raising=False)
    assert xema_opendata.request_headers() == {}
    monkeypatch.setenv(xema_opendata.APP_TOKEN_ENV, "tok123")
    assert xema_opendata.request_headers() == {"X-App-Token": "tok123"}


# --- timestamp semantics ----------------------------------------------------

def test_normalize_timestamp_marks_utc_without_shifting():
    assert xema_opendata.normalize_timestamp("2026-02-01T11:00:00.000") == "2026-02-01T11:00:00Z"


def test_forward_label_preserved():
    # an 11:00 reading stays 11:00Z — never re-centered to 11:15 or shifted back
    rows = xema_opendata.parse_rows(
        [{"codi_estacio": "Z1", "codi_variable": "38",
          "data_lectura": "2026-02-01T11:00:00.000", "valor_lectura": "172"}]
    )
    assert rows[0].valid_time_utc == "2026-02-01T11:00:00Z"
    # an observation is its own run and valid time
    assert rows[0].run_time_utc == rows[0].valid_time_utc


# --- parser over the recorded fixture ---------------------------------------

def test_parse_recorded_fixture_shape():
    rows = xema_opendata.parse_rows(fixture_rows())
    assert len(rows) == len(fixture_rows())
    assert all(r.source == "xema" for r in rows)
    assert {r.station for r in rows} == {"Z1", "Z9"}
    # every variable maps to an obs.<slug>, none left as a raw code
    assert all(r.variable.startswith("obs.") for r in rows)
    slugs = {r.variable for r in rows}
    assert "obs.gruix_neu" in slugs and "obs.temperatura" in slugs


def test_parse_recorded_fixture_snow_depth_value():
    rows = xema_opendata.parse_rows(fixture_rows())
    snow = [r for r in rows if r.variable == "obs.gruix_neu"
            and r.station == "Z1" and r.valid_time_utc == "2026-02-01T10:00:00Z"]
    assert len(snow) == 1
    assert snow[0].value == 172.0  # cm, as a float — not the string "172"


def test_missing_sensor_stays_missing():
    # Z1 (Bonaigua) has no wind sensors; those rows simply don't exist, they
    # are never invented as 0
    rows = xema_opendata.parse_rows(fixture_rows())
    z1_wind = [r for r in rows if r.station == "Z1" and "vent" in r.variable]
    assert z1_wind == []
    z9_wind = [r for r in rows if r.station == "Z9" and r.variable == "obs.vent_velocitat"]
    assert z9_wind  # Z9 does report wind


def test_parse_skips_unmapped_variable_code():
    rows = xema_opendata.parse_rows(
        [{"codi_estacio": "Z1", "codi_variable": "999",
          "data_lectura": "2026-02-01T11:00:00.000", "valor_lectura": "5"}]
    )
    assert rows == []


def test_parse_blank_and_nonnumeric_values_become_none():
    rows = xema_opendata.parse_rows(
        [{"codi_estacio": "Z1", "codi_variable": "38",
          "data_lectura": "2026-02-01T11:00:00.000", "valor_lectura": ""},
         {"codi_estacio": "Z1", "codi_variable": "32",
          "data_lectura": "2026-02-01T11:00:00.000", "valor_lectura": None},
         {"codi_estacio": "Z1", "codi_variable": "35",
          "data_lectura": "2026-02-01T11:00:00.000", "valor_lectura": "x"}]
    )
    assert [r.value for r in rows] == [None, None, None]


def test_parse_skips_rows_without_timestamp():
    rows = xema_opendata.parse_rows(
        [{"codi_estacio": "Z1", "codi_variable": "38", "valor_lectura": "10"}]
    )
    assert rows == []


def test_variable_slugs_are_shell_and_sql_safe():
    assert all(s.replace("_", "").isalpha() for s in XEMA_VARIABLES.values())


# --- parse → store idempotence ----------------------------------------------

def test_parse_payload_then_upsert_is_idempotent(tmp_path):
    raw = FIXTURE.read_bytes()
    conn = store.connect(tmp_path / "v.sqlite")
    first = store.upsert_rows(conn, xema_opendata.parse_payload(raw))
    store.upsert_rows(conn, xema_opendata.parse_payload(raw))  # re-ingest same page
    count = conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]
    assert first == count == len(fixture_rows())


# --- opt-in live schema check -----------------------------------------------

@pytest.mark.live
def test_live_open_data_schema_is_stable():
    import requests

    session = requests.Session()
    params = xema_opendata.build_query(
        ["Z1"], ["38"], "2026-02-01T11:00:00", "2026-02-01T11:30:00"
    )
    rows = xema_opendata.parse_payload(xema_opendata.fetch_page(session, params))
    assert rows and rows[0].variable == "obs.gruix_neu"
    assert rows[0].value is not None
