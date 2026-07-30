"""CLI: backfill AROME forecast rows for the pre-winter baseline (S0.6a/T9).

Fetches fixed-lead (24 h) AROME forecast series from the Previous Runs API at
each XEMA truth-station point, one call per station × calendar month, and writes
them as `fx.snowfall_cm.<column>` rows into the verification store — the forecast
side of the frozen baseline (ADR-0003), scored later by verify.py (T8) against
the XEMA truth (T6/T7). Archive-before-parse (ADR-0002): every raw response is
written to the datastore BEFORE decoding.

Quota-guarded and idempotent: `--dry-run` prints the call plan and estimated
call units without touching the network; a hard cap
(config.BACKTEST_DAILY_CALL_UNIT_CAP) refuses an oversized plan unless --force;
store upserts are idempotent, so re-running any range changes 0 rows and the
fetch can be spread over several days.

Only months within the Previous Runs archive floor (2024-01-19, see
docs/notes/previous-runs-coverage.md) are requested. Default stations are the
scored snow-truth EMAs (Z1, Z2, Z9); --all-scored adds the valley stations.

Usage:
    python -m minipirineu.backfill_forecast 2024-11 2025-04            # a winter
    python -m minipirineu.backfill_forecast 2024-02 2025-05 --dry-run  # plan only
    python -m minipirineu.backfill_forecast 2024-11 2025-04 --all-scored
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from minipirineu import previous_runs, store
from minipirineu.archive import Archive
from minipirineu.config import (
    BACKTEST_DAILY_CALL_UNIT_CAP,
    BACKTEST_LEAD_DAYS,
    PREVIOUS_RUNS_AROME_START_UTC,
    XEMA_STATIONS,
)
from minipirineu.ingest_xema import make_session

ARCHIVE_SOURCE = "openmeteo_backtest"  # raw folder, distinct from live openmeteo
ARCHIVE_FLOOR = date.fromisoformat(PREVIOUS_RUNS_AROME_START_UTC[:10])


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_ranges(start: str, end: str, floor: date = ARCHIVE_FLOOR) -> list[tuple[str, str, str]]:
    """Inclusive "YYYY-MM".."YYYY-MM" → one (start_date, end_date, label) per
    month with INCLUSIVE daily bounds (the Previous Runs API is inclusive on
    end_date). A month's start is clamped up to the archive floor, and months
    entirely before the floor are skipped — requesting them costs quota and
    returns only nulls."""
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    if (ey, em) < (sy, sm):
        raise ValueError(f"end {end} precedes start {start}")
    out: list[tuple[str, str, str]] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        first = date(y, m, 1)
        ny, nm = _next_month(y, m)
        last = date(ny, nm, 1) - timedelta(days=1)
        if last >= floor:  # skip a month wholly before the archive floor
            out.append((max(first, floor).isoformat(), last.isoformat(), f"{y:04d}-{m:02d}"))
        y, m = ny, nm
    return out


def backtest_stations(all_scored: bool = False):
    """Points to re-fetch forecasts at. Default: the scored snow-truth EMAs
    (their var-38 truth is what a snowfall baseline is scored against). With
    --all-scored, include the valley stations too (resort is not None)."""
    if all_scored:
        return [s for s in XEMA_STATIONS if s.resort]
    return [s for s in XEMA_STATIONS if s.snow_truth]


@dataclass(frozen=True)
class PlanItem:
    station: str
    label: str
    start_date: str
    end_date: str
    n_days: int
    units: int


def build_plan(stations, ranges, lead_days=BACKTEST_LEAD_DAYS) -> list[PlanItem]:
    """One API call per (station × month); its estimated cost in call units."""
    n_series = len(previous_runs.BASE_VARS) * len(previous_runs.MODEL_IDS) * len(lead_days)
    plan: list[PlanItem] = []
    for s in stations:
        for start_date, end_date, label in ranges:
            n_days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
            units = previous_runs.estimate_call_units(n_days, n_series)
            plan.append(PlanItem(s.codi, label, start_date, end_date, n_days, units))
    return plan


def run_backfill(
    archive: Archive,
    conn,
    stations,
    ranges,
    session,
    now_utc: datetime,
    lead_days=BACKTEST_LEAD_DAYS,
    fetch=previous_runs.fetch,
) -> int:
    """Fetch, archive-before-parse, and upsert every (station × month) chunk.
    Returns rows upserted (idempotent: a re-run upserts the same rows and leaves
    the store row count unchanged)."""
    total = 0
    for s in stations:
        for start_date, end_date, label in ranges:
            raw = fetch(session, s.latitude, s.longitude, s.altitude_m, start_date, end_date, lead_days)
            archive.store(ARCHIVE_SOURCE, f"{s.codi}_{label}.json", raw, fetched_at=now_utc)
            rows = previous_runs.parse_payload(raw, s.codi, lead_days)  # bytes safely archived first
            n = store.upsert_rows(conn, rows)
            total += n
            print(f"  {s.codi} {label} [{start_date}..{end_date}]: {n} rows")
    return total


def _print_plan(plan: list[PlanItem], total_units: int) -> None:
    for p in plan:
        print(f"  {p.station} {p.label} {p.n_days}d ~{p.units}u")
    print(f"plan: {len(plan)} calls, ~{total_units} call units "
          f"(cap {BACKTEST_DAILY_CALL_UNIT_CAP})")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    # argparse (not hand-rolled flag matching) so a typo'd flag — e.g. --dryrun
    # for --dry-run — is rejected, never silently ignored into a real network run.
    ap = argparse.ArgumentParser(
        prog="python -m minipirineu.backfill_forecast",
        description="Backfill AROME forecast rows from the Previous Runs API (S0.6a/T9).")
    ap.add_argument("start", help="first month, inclusive (YYYY-MM)")
    ap.add_argument("end", help="last month, inclusive (YYYY-MM)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the call plan and estimated units, touch no network")
    ap.add_argument("--force", action="store_true",
                    help="run even if the plan exceeds the daily call-unit cap")
    ap.add_argument("--all-scored", action="store_true",
                    help="include the valley stations, not just the snow-truth EMAs")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        ranges = month_ranges(args.start, args.end)
    except ValueError as exc:
        print(f"bad month range {args.start!r}..{args.end!r} (expected YYYY-MM): {exc}",
              file=sys.stderr)
        return 2
    if not ranges:
        print(f"no months within the Previous Runs archive floor ({ARCHIVE_FLOOR})", file=sys.stderr)
        return 2

    stations = backtest_stations(args.all_scored)
    plan = build_plan(stations, ranges)
    total_units = sum(p.units for p in plan)
    _print_plan(plan, total_units)

    if args.dry_run:
        return 0
    if total_units > BACKTEST_DAILY_CALL_UNIT_CAP and not args.force:
        print(f"plan exceeds cap ({total_units} > {BACKTEST_DAILY_CALL_UNIT_CAP}); "
              f"narrow the range or pass --force", file=sys.stderr)
        return 2

    now_utc = datetime.now(timezone.utc)
    archive = Archive.from_env()
    conn = store.connect(archive.root / "verification.sqlite")
    with make_session() as session:
        total = run_backfill(archive, conn, stations, ranges, session, now_utc)
    print(f"backfill {args.start}..{args.end}: {total} rows upserted into {archive.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
