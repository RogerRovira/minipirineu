"""CLI: backfill XEMA open-data observations into the datastore (S0.3/T5).

Chunked per station-group × month, idempotent, resumable, quota-free (Socrata).
Archive-before-parse (ADR-0002): every raw page is written to the datastore
BEFORE it is decoded, so a parser bug can never lose an irreplaceable pull.
Writes only the verification store + raw archive on the `datastore` branch —
nothing here touches the published site on `main`.

Two station groups (config.XEMA_STATIONS):
  - scored truth (resort set): every variable in XEMA_VARIABLES;
  - archive-wide high EMAs (resort=None): snow depth (var 38) only.

Resumable because each station-group × month chunk is independent and the store
upserts are idempotent — re-running any range re-pulls it and changes 0 rows.

Usage:
    python -m minipirineu.ingest_xema 2023-11 2024-05        # a winter
    python -m minipirineu.ingest_xema 2023-11 2024-05 --scored-only
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from minipirineu import store, xema_opendata
from minipirineu.archive import Archive
from minipirineu.config import XEMA_SNOW_DEPTH_VAR, XEMA_STATIONS, XEMA_VARIABLES


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_chunks(start: str, end: str) -> list[tuple[str, str, str]]:
    """Inclusive month range "YYYY-MM".."YYYY-MM" → half-open [start, next)
    ISO windows, one per month: (start_iso, end_iso, label "YYYY-MM")."""
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    if (ey, em) < (sy, sm):
        raise ValueError(f"end {end} precedes start {start}")
    chunks = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        ny, nm = _next_month(y, m)
        chunks.append(
            (
                f"{_month_start(y, m).isoformat()}T00:00:00",
                f"{_month_start(ny, nm).isoformat()}T00:00:00",
                f"{y:04d}-{m:02d}",
            )
        )
        y, m = ny, nm
    return chunks


def backfill_chunk(
    archive: Archive,
    conn,
    station_codes,
    variable_codes,
    start_iso: str,
    end_iso: str,
    label: str,
    session: requests.Session,
    now_utc: datetime,
    fetch_page=xema_opendata.fetch_page,
    page_limit: int = xema_opendata.PAGE_LIMIT,
) -> int:
    """Page through one window, archiving each raw page before parsing, and
    upsert every reading. Returns rows upserted (idempotent: a re-run of the
    same window upserts the same rows and leaves the count unchanged)."""
    total = 0
    offset = 0
    page = 0
    while True:
        params = xema_opendata.build_query(
            station_codes, variable_codes, start_iso, end_iso, page_limit, offset
        )
        raw = fetch_page(session, params)
        archive.store("xema", f"{label}_p{page:03d}.json", raw, fetched_at=now_utc)
        rows = xema_opendata.parse_payload(raw)
        total += store.upsert_rows(conn, rows)
        n = len(rows)
        if n < page_limit:
            return total
        offset += page_limit
        page += 1


def backfill(
    archive: Archive,
    conn,
    chunks,
    session: requests.Session,
    now_utc: datetime,
    scored_only: bool = False,
    fetch_page=xema_opendata.fetch_page,
) -> int:
    """Run every (group × month) chunk. Scored stations pull all variables;
    archive-wide stations pull snow depth only (zero marginal scoring cost)."""
    scored = [s.codi for s in XEMA_STATIONS if s.resort]
    wide = [s.codi for s in XEMA_STATIONS if not s.resort]
    groups = [(scored, list(XEMA_VARIABLES))]
    if wide and not scored_only:
        groups.append((wide, [XEMA_SNOW_DEPTH_VAR]))

    total = 0
    for codes, variables in groups:
        for start_iso, end_iso, label in chunks:
            written = backfill_chunk(
                archive, conn, codes, variables, start_iso, end_iso,
                label, session, now_utc, fetch_page,
            )
            total += written
            print(f"  {label} [{len(codes)} st × {len(variables)} var]: {written} rows")
    return total


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    scored_only = "--scored-only" in argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    start, end = args
    now_utc = datetime.now(timezone.utc)
    archive = Archive.from_env()
    conn = store.connect(archive.root / "verification.sqlite")
    chunks = month_chunks(start, end)
    with make_session() as session:
        total = backfill(archive, conn, chunks, session, now_utc, scored_only)
    print(f"backfill {start}..{end}: {total} rows upserted into {archive.root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
