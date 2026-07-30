"""CLI: near-real-time XEMA obs from the meteo.cat API into the datastore (S0.8/T11).

Staleness-gated (~3 cycles/day), with a morning backfill of the previous day
(overnight validation revises provisional readings). Archive-before-parse
(ADR-0002); idempotent because readings carry their own timestamps
(readings-as-run_time, so a re-fetch upserts the same rows). The Socrata backfill
(`ingest_xema.py`) stays the historical truth; this only adds the recent tail.

One call per (variable, day), all stations, filtered to ours — ~5 vars × 1 day ×
3 cycles + a morning 2-day pass keeps well under the 750/month XEMA plan. Writes
only the datastore branch (archive + verification store), never the site on main.

Usage:
    python -m minipirineu.ingest_xema_api            # gated ~3×/day
    python -m minipirineu.ingest_xema_api --force    # ignore the staleness gate
"""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from minipirineu import store, xema_api
from minipirineu.archive import Archive, run_time_from_path
from minipirineu.config import XEMA_STATIONS
from minipirineu.envfile import load_env

# Truth-critical variables for the live tail: snow depth, temperature,
# precipitation, and the two wind fields the truth-B gates need. (The full
# 9-variable history is Socrata's job; here every extra variable is quota.)
LIVE_VARS = ("38", "32", "35", "30", "50")
ARCHIVE_SOURCE = "xema_api"       # distinct raw folder from the Socrata "xema"
SKIP_IF_FRESHER_H = 6.0           # ~3 cycles/day (8 h apart) with margin
MORNING_UTC_HOUR = 12             # before noon UTC also re-pulls yesterday


def target_codis() -> list[str]:
    return [s.codi for s in XEMA_STATIONS]


def days_to_fetch(now_utc: datetime) -> list[date]:
    """Today, plus yesterday on the morning cycle (its readings finish
    validating overnight, so a morning re-pull corrects them)."""
    today = now_utc.date()
    if now_utc.hour < MORNING_UTC_HOUR:
        return [today - timedelta(days=1), today]
    return [today]


def latest_pull_age_h(archive: Archive, now_utc: datetime) -> float | None:
    """Hours since the most recent archived XEMA-API payload, or None if the
    archive is empty (first run — never skip)."""
    paths = sorted((archive.root / "raw" / ARCHIVE_SOURCE).rglob("*.gz"))
    if not paths:
        return None
    fetched = datetime.fromisoformat(run_time_from_path(paths[-1]))
    return (now_utc - fetched).total_seconds() / 3600.0


def run_ingest(archive, conn, session, now_utc, *, variables=LIVE_VARS,
               fetch=xema_api.fetch) -> int:
    """Fetch each (variable, day) all-stations, archive-before-parse, upsert the
    readings of our stations. Returns rows upserted (idempotent on re-run)."""
    codis = target_codis()
    total = 0
    for day in days_to_fetch(now_utc):
        for var in variables:
            raw = fetch(session, var, day)  # all-stations (no codiEstacio)
            archive.store(ARCHIVE_SOURCE, f"{day:%Y%m%d}_v{var}.json", raw, fetched_at=now_utc)
            rows = xema_api.parse_all_stations(raw, codis)  # bytes safely archived first
            n = store.upsert_rows(conn, rows)
            total += n
            print(f"  {day.isoformat()} v{var}: {n} rows")
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m minipirineu.ingest_xema_api",
        description="Near-real-time XEMA obs from the meteo.cat API (S0.8/T11).")
    ap.add_argument("--force", action="store_true",
                    help="ignore the staleness gate (always fetch)")
    args = ap.parse_args(argv)

    load_env()
    now_utc = datetime.now(timezone.utc)
    archive = Archive.from_env()
    age = latest_pull_age_h(archive, now_utc)
    if age is not None and age < SKIP_IF_FRESHER_H and not args.force:
        print(f"skip: last XEMA API pull {age:.1f} h ago (< {SKIP_IF_FRESHER_H} h); --force to override")
        return 0

    conn = store.connect(archive.root / "verification.sqlite")
    with xema_api.make_session() as session:
        total = run_ingest(archive, conn, session, now_utc)
    days = ", ".join(d.isoformat() for d in days_to_fetch(now_utc))
    print(f"xema_api ingest [{days}]: {total} rows upserted into {archive.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
