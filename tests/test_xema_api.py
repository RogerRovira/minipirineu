"""XEMA API client + parser (S0.8/T11).

Parser runs over the recorded single-station fixture (tests/fixtures/xema_api/,
from scripts/record_xema_parity.py) and a synthetic all-stations payload. No
network. Live parity against the open data is the parity script's job; here we
pin the parse: shapes, station/variable filtering, timestamp canonicalisation,
and readings-as-run_time idempotence.
"""

import json
from pathlib import Path

from minipirineu import xema_api

FIXTURE = Path(__file__).parent / "fixtures" / "xema_api" / "Z1_38_20260201.json"


def test_parse_station_flat_payload():
    rows = xema_api.parse_station(FIXTURE.read_bytes(), "Z1")
    assert len(rows) == 48                       # 30-min readings over one day
    r = rows[0]
    assert r.source == "xema" and r.station == "Z1"
    assert r.variable == "obs.gruix_neu"         # var 38 → slug
    assert r.run_time_utc == r.valid_time_utc     # readings-as-run_time
    assert r.valid_time_utc == "2026-02-01T00:00:00Z"  # seconds restored
    assert isinstance(r.value, float)


def test_norm_stamp_canonicalises_to_seconds_utc():
    assert xema_api._norm_stamp("2026-02-01T00:00Z") == "2026-02-01T00:00:00Z"
    assert xema_api._norm_stamp("2026-02-01T00:30:00Z") == "2026-02-01T00:30:00Z"
    assert xema_api._norm_stamp("2026-02-01T00:00+00:00") == "2026-02-01T00:00:00Z"


_ALL_STATIONS = json.dumps([
    {"codi": "Z1", "variables": [
        {"codi": 38, "lectures": [
            {"data": "2026-02-01T00:00Z", "valor": 176, "estat": "V", "baseHoraria": "SH"},
            {"data": "2026-02-01T00:30Z", "valor": None, "estat": "T", "baseHoraria": "SH"},
        ]},
        {"codi": 32, "lectures": [
            {"data": "2026-02-01T00:00Z", "valor": -3.2, "estat": "V", "baseHoraria": "SH"},
        ]},
    ]},
    {"codi": "XX", "variables": [  # not a target station → whole entry skipped
        {"codi": 38, "lectures": [{"data": "2026-02-01T00:00Z", "valor": 5, "estat": "V"}]},
    ]},
    {"codi": "Z9", "variables": [  # a variable we don't map → skipped
        {"codi": 99, "lectures": [{"data": "2026-02-01T00:00Z", "valor": 1, "estat": "V"}]},
    ]},
]).encode()


def test_parse_all_stations_filters_and_maps():
    rows = xema_api.parse_all_stations(_ALL_STATIONS, ["Z1", "Z9"])
    # Z1 gruix_neu (00:00 only; the None reading is dropped) + Z1 temperatura;
    # XX filtered out (not a target); Z9's unmapped var 99 yields nothing.
    assert len(rows) == 2
    by_var = {r.variable: r for r in rows}
    assert by_var["obs.gruix_neu"].value == 176.0
    assert by_var["obs.temperatura"].value == -3.2
    assert all(r.station == "Z1" and r.run_time_utc == r.valid_time_utc for r in rows)


def test_missing_valor_is_dropped_not_zeroed():
    payload = json.dumps([{"codi": "Z1", "variables": [{"codi": 38, "lectures": [
        {"data": "2026-02-01T00:00Z", "valor": None, "estat": "T"}]}]}]).encode()
    assert xema_api.parse_all_stations(payload, ["Z1"]) == []


def test_build_url_shape():
    from datetime import date
    assert xema_api.build_url("38", date(2026, 2, 1)).endswith("/mesurades/38/2026/02/01")
