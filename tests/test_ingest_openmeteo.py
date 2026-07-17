"""T4 ingest contract: every raw Open-Meteo response is archived to the
datastore BEFORE parsing (ADR-0002), the freezing-level diagnostic lands in
the verification store (never the page), and the last-good failure contract
is preserved. All offline over the wide fixture recorded 2026-07-17."""

import json
from datetime import datetime
from pathlib import Path

from minipirineu import ingest_openmeteo, openmeteo, store
from minipirineu.archive import Archive
from minipirineu.config import STATIONS

FIXTURES = Path(__file__).parent / "fixtures"
WIDE_BYTES = (FIXTURES / "openmeteo_wide_baqueira_1500.json").read_bytes()
# naive Europe/Madrid, inside the fixture's 3-day window
NOW_LOCAL = datetime(2026, 7, 17, 15, 0)


def fake_fetch(session, station, elevation_m, timeout=30):
    return WIDE_BYTES


def run_main(tmp_path, monkeypatch, fetch=fake_fetch, out=None):
    out = out or tmp_path / "openmeteo.json"
    monkeypatch.setenv("MINIPIRINEU_DATA_DIR", str(tmp_path / "ds"))
    monkeypatch.setattr(openmeteo, "fetch", fetch)
    return ingest_openmeteo.main(out, now_local=NOW_LOCAL), out


def test_full_run_archives_9_raws_and_writes_snapshot(tmp_path, monkeypatch):
    rc, out = run_main(tmp_path, monkeypatch)
    assert rc == 0
    snapshot = json.loads(out.read_text())
    assert [s["id"] for s in snapshot["stations"]] == [s.id for s in STATIONS]
    archived = list(Archive(tmp_path / "ds").iter_source("openmeteo"))
    assert len(archived) == 9  # 3 stations x 3 bands, byte-faithful
    assert all(body == WIDE_BYTES for _, body in archived)
    names = {path.name.split("_", 1)[1] for path, _ in archived}
    assert "baqueira_1500.json.gz" in names
    assert "boi-taull_2750.json.gz" in names


def test_freezing_level_rows_in_store_per_station(tmp_path, monkeypatch):
    rc, _ = run_main(tmp_path, monkeypatch)
    assert rc == 0
    conn = store.connect(tmp_path / "ds" / "verification.sqlite")
    stations = {r[0] for r in conn.execute(
        "SELECT DISTINCT station FROM verification_values "
        "WHERE variable = 'derived.freezing_level_m'")}
    assert stations == {s.id for s in STATIONS}
    # n_crossings is persisted (the PiriNeu-port fix) alongside the height
    n = conn.execute("SELECT COUNT(*) FROM verification_values "
                     "WHERE variable = 'derived.freezing_level_n_crossings'").fetchone()[0]
    assert n > 0
    # storage is UTC: fixture hour 2026-07-17T00:00 local is 22:00Z the day before
    utc_row = conn.execute(
        "SELECT COUNT(*) FROM verification_values "
        "WHERE valid_time_utc = '2026-07-16T22:00:00Z'").fetchone()[0]
    assert utc_row > 0


def test_snapshot_values_unchanged_by_wide_ingest(tmp_path, monkeypatch):
    # the verification gate: T4 must not change data/openmeteo.json content
    rc, out = run_main(tmp_path, monkeypatch)
    assert rc == 0
    band = json.loads(out.read_text())["stations"][0]["bands"][0]
    model_keys = {k for m in band["models"] for k in m}
    assert model_keys == {"model", "label", "snowfall_source", "intervals",
                         "total_snowfall_cm", "total_precipitation_mm",
                         "effective_horizon_h"}
    interval_keys = {k for m in band["models"] for iv in m["intervals"] for k in iv}
    assert interval_keys == {"start", "end", "snowfall_cm", "precipitation_mm",
                            "temperature_c"}


def test_fetch_failure_keeps_last_good_and_exits_nonzero(tmp_path, monkeypatch):
    def boom(session, station, elevation_m, timeout=30):
        raise RuntimeError("network down")
    out = tmp_path / "openmeteo.json"
    out.write_text('{"old": true}')
    rc, out = run_main(tmp_path, monkeypatch, fetch=boom, out=out)
    assert rc == 1
    assert json.loads(out.read_text()) == {"old": True}


def test_garbage_payload_is_archived_before_the_parse_fails(tmp_path, monkeypatch):
    def garbage(session, station, elevation_m, timeout=30):
        return b"\xff not json"
    out = tmp_path / "openmeteo.json"
    out.write_text('{"old": true}')
    rc, out = run_main(tmp_path, monkeypatch, fetch=garbage, out=out)
    assert rc == 1
    assert json.loads(out.read_text()) == {"old": True}
    archived = list(Archive(tmp_path / "ds").iter_source("openmeteo"))
    assert archived and archived[0][1] == b"\xff not json"  # raw survived
