"""Live forecast pairing: archived Open-Meteo runs → verification forecast rows (S0.7/T11b).

The "how wrong were we" page scores the *published* product. Those forecasts are
the 6 h Open-Meteo runs archived raw on the datastore (`raw/openmeteo/`, ADR-0002).
This module re-buckets each hourly run onto the SAME 6 h UTC grid as the truth and
the frozen backtest — one bucketizer for backtest and live (ADR-0003) — so
verify.py (T8) scores the live tail and the baseline with identical code.

Only the HIGH band of each snow-truth resort is scored (its grid point is nearest
the resort's high XEMA truth station): Baqueira 2600→Z1, Boí 2750→Z2, La Molina
2500→Z9. run_time = the run's fetch stamp (recovered from the archive filename);
each 6 h bucket's lead then follows from valid − run. The archived series are
LOCAL time (Europe/Madrid, as the site needs); `utc_offset_seconds` converts them
to UTC before bucketing. Snowfall per model uses the same native/derived rule as
the site (ingest_openmeteo.snowfall_series). Missing stays missing: an incomplete
6 h bucket produces no row, never a fabricated 0.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from minipirineu import aggregate, ingest_openmeteo, openmeteo, previous_runs, store, verify
from minipirineu.archive import Archive, run_time_from_path
from minipirineu.config import DERIVED_COLUMNS, MODELS, STATIONS, XEMA_STATIONS
from minipirineu.store import Row
from minipirineu.truth import parse_stamp

SOURCE = "openmeteo"          # store row source (same as backtest; run_time differs)
ARCHIVE_SOURCE = "openmeteo"  # the live 6 h cron's raw folder on the datastore


def high_band_targets() -> dict[tuple[str, int], str]:
    """(resort id, high-band elevation m) → XEMA snow-truth station code. The live
    page scores the published high band against the resort's high EMA."""
    xema_by_resort = {x.resort: x.codi for x in XEMA_STATIONS if x.snow_truth and x.resort}
    targets: dict[tuple[str, int], str] = {}
    for st in STATIONS:
        if st.id in xema_by_resort:
            elev = st.bands[-1][1]          # bands are low→high; the last is the top
            targets[(st.id, elev)] = xema_by_resort[st.id]
    return targets


def parse_archive_name(path: Path) -> tuple[str, str, int]:
    """Archive filename → (run_time_utc, resort_id, elevation_m). The name is
    `<STAMP>_<resort>_<elev>.json.gz`; the resort id may contain '-', so split
    the stamp off the front and the elevation off the back."""
    run = run_time_from_path(path)
    rest = path.name.split("_", 1)[1]
    for suffix in (".json.gz", ".json"):
        if rest.endswith(suffix):
            rest = rest[: -len(suffix)]
            break
    resort, _, elev = rest.rpartition("_")
    return run, resort, int(elev)


def _utc_times(local_times: list[str], offset_s: int) -> list[str]:
    """Naive Europe/Madrid ISO stamps → naive UTC ISO stamps for bucketize."""
    return [(datetime.fromisoformat(t) - timedelta(seconds=offset_s)).isoformat()
            for t in local_times]


def raw_to_rows(raw_bytes: bytes, run_time_utc: str, station_code: str) -> list[Row]:
    """One archived run at one band → forecast rows (fx.snowfall_cm.<col>) on the
    6 h UTC grid. Same snowfall rule as the site and same bucketizer as the
    backtest (ADR-0003), so the numbers are directly comparable."""
    raw = json.loads(raw_bytes)
    parsed = openmeteo.parse_response(raw)
    utc_times = _utc_times(parsed["time"], raw.get("utc_offset_seconds", 0))
    run_dt = parse_stamp(run_time_utc)
    rows: list[Row] = []

    def emit(variable: str, snow: list) -> None:
        for bucket_start, cm in previous_runs.bucketize(utc_times, snow):
            # a bucket starting before the run is analysis/nowcast, not a
            # forecast — scoring it would flatter forecast skill; drop it.
            if parse_stamp(bucket_start) < run_dt:
                continue
            rows.append(Row(SOURCE, station_code, run_time_utc, bucket_start, variable, cm))

    for spec in MODELS:
        series = parsed["models"][spec.id]
        try:
            snow = ingest_openmeteo.snowfall_series(spec, series)
        except ValueError:
            continue  # a native model gone all-null → API changed; skip, never 0
        emit(verify.forecast_variable(spec.column), snow)
    # Scored-only challenger columns (S1.1): the archived live raws carry surface
    # RH (T4), so the wet-bulb column genuinely differs from the T-taper here.
    # Never rendered — scored beside the published column until it beats baseline.
    for dc in DERIVED_COLUMNS:
        series = parsed["models"][dc.from_model]
        snow = aggregate.derive_column_snowfall(
            dc.partition, series["precipitation_mm"],
            series["temperature_c"], series["relative_humidity_pct"],
        )
        emit(verify.forecast_variable(dc.column), snow)
    return rows


def ingest_live_forecasts(archive: Archive, conn, *, targets=None) -> int:
    """Re-bucket every archived live run at a scored high band into forecast
    rows and upsert them. Idempotent (a run's rows have a fixed run_time), so
    re-processing the whole archive changes 0 rows."""
    targets = high_band_targets() if targets is None else targets
    total = 0
    for path, raw_bytes in archive.iter_source(ARCHIVE_SOURCE):
        run_time, resort, elev = parse_archive_name(path)
        station = targets.get((resort, elev))
        if station is None:
            continue  # not a scored high band (low/mid band, or a non-truth resort)
        total += store.upsert_rows(conn, raw_to_rows(raw_bytes, run_time, station))
    return total


def build_live_report(conn, start_utc: str, end_utc: str) -> dict:
    """Score the ingested live forecast rows against the merged truth over the
    window, run-grouped (the live loop's default: one run covers the day)."""
    stations = [x.codi for x in XEMA_STATIONS if x.snow_truth]
    pairs = verify.build_pairs(conn, stations, start_utc, end_utc)
    return verify.verify_report(pairs, daily_by="run")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the trailing live verification report from the forecast archive (T11b).")
    ap.add_argument("start", help="ISO-UTC window start, e.g. 2025-11-01T00:00:00Z")
    ap.add_argument("end", help="ISO-UTC window end (half-open)")
    ap.add_argument("--out-json", type=Path, help="write the live report here (for the page)")
    args = ap.parse_args(argv)

    archive = Archive.from_env()
    conn = store.connect(archive.root / "verification.sqlite")
    n = ingest_live_forecasts(archive, conn)
    report = build_live_report(conn, args.start, args.end)
    print(f"live: {n} forecast rows ingested; {report['n_pairs']} pairs scored")
    if args.out_json:
        args.out_json.write_text(verify.to_json(report))
        print(f"wrote {args.out_json}")
    else:
        print(verify.to_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
