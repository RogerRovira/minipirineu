"""Backfill CLI for the forecast side of the baseline (S0.6a/T9).

Pure chunking / planning tests, plus a mocked fetch that exercises
archive-before-parse and store idempotence without network. The recorded
Previous Runs fixture stands in for the API response.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from minipirineu import backfill_forecast as bf
from minipirineu import store, verify
from minipirineu.archive import Archive
from minipirineu.truth import parse_stamp

FIXTURE = Path(__file__).parent / "fixtures" / "previous_runs_arome_z1_20250201.json"
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _archive(tmp_path) -> Archive:
    return Archive(root=tmp_path / "datastore")


# --- month ranges + archive floor -------------------------------------------

def test_month_ranges_are_inclusive_calendar_months():
    r = bf.month_ranges("2024-11", "2025-01", floor=date(2020, 1, 1))
    assert [x[2] for x in r] == ["2024-11", "2024-12", "2025-01"]
    assert r[0] == ("2024-11-01", "2024-11-30", "2024-11")
    assert r[1] == ("2024-12-01", "2024-12-31", "2024-12")
    assert r[2][:2] == ("2025-01-01", "2025-01-31")


def test_month_ranges_clamp_and_skip_below_the_archive_floor():
    # the real AROME floor: months before 2024-01 vanish, January starts on the
    # 19th, February onward is whole
    r = bf.month_ranges("2023-12", "2024-02")
    assert [x[2] for x in r] == ["2024-01", "2024-02"]        # Dec 2023 skipped
    assert r[0] == ("2024-01-19", "2024-01-31", "2024-01")    # clamped up
    assert r[1] == ("2024-02-01", "2024-02-29", "2024-02")    # 2024 is a leap year


def test_month_ranges_rejects_reversed_range():
    with pytest.raises(ValueError, match="precedes"):
        bf.month_ranges("2025-03", "2024-11")


# --- station selection + budget plan ----------------------------------------

def test_default_stations_are_the_snow_truth_emas():
    codes = [s.codi for s in bf.backtest_stations()]
    assert codes == ["Z1", "Z2", "Z9"]
    all_scored = {s.codi for s in bf.backtest_stations(all_scored=True)}
    assert {"YN", "CT", "DP"} <= all_scored and all_scored > set(codes)


def test_plan_is_one_call_per_station_month_and_bounded():
    stations = bf.backtest_stations()
    ranges = bf.month_ranges("2024-11", "2025-04", floor=date(2020, 1, 1))
    plan = bf.build_plan(stations, ranges)
    assert len(plan) == len(stations) * len(ranges)   # one call each
    total = sum(p.units for p in plan)
    assert 0 < total < bf.BACKTEST_DAILY_CALL_UNIT_CAP  # nowhere near the cap


# --- fetch loop (mocked) ----------------------------------------------------

class FakeFetch:
    """Returns baked bytes and records the coords/date-range it was called with."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []

    def __call__(self, session, lat, lon, elev, start_date, end_date, lead_days):
        self.calls.append((lat, lon, elev, start_date, end_date))
        return self.payload


def test_run_backfill_upserts_rows_and_is_idempotent(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    fetch = FakeFetch(FIXTURE.read_bytes())
    stations = [s for s in bf.backtest_stations() if s.codi == "Z1"]
    ranges = [("2025-02-01", "2025-02-02", "2025-02")]

    n = bf.run_backfill(archive, conn, stations, ranges, session=None, now_utc=NOW, fetch=fetch)
    assert n == 16                       # 8 buckets × 2 columns (arome_25 + arome_hd)
    count = conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]
    assert count == 16
    # the call went out at Z1's real coords + elevation
    assert fetch.calls[0][:3] == (42.64691, 0.98486, 2262)
    # re-pull the same window → same rows, store count unchanged
    bf.run_backfill(archive, conn, stations, ranges, session=None, now_utc=NOW, fetch=fetch)
    assert conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0] == 16


def test_run_backfill_writes_forecast_columns_the_verifier_reads(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    fetch = FakeFetch(FIXTURE.read_bytes())
    stations = [s for s in bf.backtest_stations() if s.codi == "Z1"]
    bf.run_backfill(archive, conn, stations, [("2025-02-01", "2025-02-02", "2025-02")],
                    session=None, now_utc=NOW, fetch=fetch)
    variables = {v for (v,) in conn.execute("SELECT DISTINCT variable FROM verification_values")}
    assert variables == {"fx.snowfall_cm.arome_25", "fx.snowfall_cm.arome_hd"}


def _calm_truth_rows(station: str, start: str, end: str) -> list[store.Row]:
    """XEMA obs on a 30-min timeline over [start, end): flat pack, no precip,
    calm wind. That is the recipe (used in test_verify) that makes truth-A =
    truth-B = 0 cm with no bucket excluded — a clean 0 cm truth to score against."""
    t, stop = parse_stamp(start), parse_stamp(end)
    rows: list[store.Row] = []
    while t < stop:
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows += [
            store.Row("xema", station, ts, ts, "obs.gruix_neu", 80.0),
            store.Row("xema", station, ts, ts, "obs.temperatura", -5.0),
            store.Row("xema", station, ts, ts, "obs.precipitacio", 0.0),
            store.Row("xema", station, ts, ts, "obs.vent_velocitat", 1.0),
            store.Row("xema", station, ts, ts, "obs.vent_ratxa", 2.0),
        ]
        t += timedelta(minutes=30)
    return rows


def test_backfilled_rows_are_scored_by_verify_end_to_end(tmp_path):
    """The T9→T8 handoff, end to end: rows the backfill actually writes (real
    parser output, not hand-built Rows) are read and scored by verify.py against
    real truth-pipeline output. This is what "verify reads the T9-built store"
    means — the column names, station codes and 6 h bucket grid all have to line
    up across the two modules, or build_pairs silently returns nothing."""
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    fetch = FakeFetch(FIXTURE.read_bytes())
    stations = [s for s in bf.backtest_stations() if s.codi == "Z1"]
    bf.run_backfill(archive, conn, stations, [("2025-02-01", "2025-02-02", "2025-02")],
                    session=None, now_utc=NOW, fetch=fetch)
    # truth spanning the fixture's two days, so every 6 h forecast bucket has a
    # truth bucket to pair against.
    store.upsert_rows(conn, _calm_truth_rows("Z1", "2025-02-01T00:00:00Z", "2025-02-03T00:00:00Z"))

    pairs = verify.build_pairs(conn, ["Z1"], "2025-02-01T00:00:00Z", "2025-02-03T00:00:00Z")
    assert pairs, "verify.build_pairs read zero pairs from the backfilled store"
    assert {p.column for p in pairs} == {"arome_hd", "arome_25"}

    report = verify.verify_report(pairs)
    for col in ("arome_hd", "arome_25"):
        m = report["bucket_6h"]["by_column"][col]
        # truth is a flat, calm 0 cm pack; the fixture forecasts real Feb snow, so
        # each column carries non-negative cm bias and its skill still computes.
        assert m["n_cm"] > 0 and m["mae"] is not None
        assert m["bias"] >= 0
    assert "arome_hd" in verify.to_markdown(report)


def test_run_backfill_archives_before_parsing(tmp_path):
    # a response that isn't valid JSON must still be on disk after the failure
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    fetch = FakeFetch(b"{ not json")
    stations = [s for s in bf.backtest_stations() if s.codi == "Z1"]
    with pytest.raises(json.JSONDecodeError):
        bf.run_backfill(archive, conn, stations, [("2025-02-01", "2025-02-02", "2025-02")],
                        session=None, now_utc=NOW, fetch=fetch)
    archived = list((archive.root / "raw" / bf.ARCHIVE_SOURCE).rglob("*.gz"))
    assert len(archived) == 1  # raw bytes survived the parser blowing up


# --- CLI ---------------------------------------------------------------------

def test_main_missing_args_exit_via_argparse():
    # argparse exits (SystemExit) on missing positionals, code 2
    for argv in ([], ["2024-11"]):
        with pytest.raises(SystemExit) as exc:
            bf.main(argv)
        assert exc.value.code == 2


def test_main_rejects_unknown_flag_instead_of_running():
    # a typo'd --dry-run must be rejected, never silently ignored into a real run
    with pytest.raises(SystemExit) as exc:
        bf.main(["2024-11", "2025-04", "--dryrun"])
    assert exc.value.code == 2


def test_main_reversed_range_is_reported(capsys):
    assert bf.main(["2025-03", "2024-11"]) == 2
    assert "bad month range" in capsys.readouterr().err


def test_main_dry_run_prints_plan_without_network(capsys):
    # a whole winter, dry-run: prints the plan + call units, touches no network
    assert bf.main(["2024-11", "2025-04", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "call units" in out
    assert "Z1" in out and "Z9" in out
