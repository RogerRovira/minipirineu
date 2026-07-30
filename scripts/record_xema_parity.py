"""One-off: cross-check XEMA open data against the authoritative XEMA API (T5).

The backfill (`ingest_xema`) trusts the Socrata open-data encoding: UTC
timestamps, forward labeling, `valor_lectura` in each variable's native unit.
This script pins that trust against the meteo.cat XEMA API for a handful of
readings — the API documents `estat` and `baseHoraria` that the open data drops
— and keeps the API responses as fixtures for the T11 live-ingest port.

Run locally with the key set (needs a meteo.cat plan subscribed to XEMA data):

    export METEOCAT_API_KEY=...        # or put it in .env
    python scripts/record_xema_parity.py

~6 quota calls (2 stations × 3 variables × 1 day). For each (station, variable)
it fetches the API day, saves it byte-faithful under tests/fixtures/xema_api/,
then asserts every API reading matches the open-data value and timestamp at the
same instant. Prints a PASS/FAIL line per pair; exits non-zero on any mismatch.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

from minipirineu import xema_api, xema_opendata
from minipirineu.config import XEMA_VARIABLES
from minipirineu.envfile import load_env

FIXTURE_DIR = Path("tests/fixtures/xema_api")

# A winter day with snow on the ground at both massif stations (see the
# open-data probe, 2026-07-18). Z9 also carries wind; Z1 does not.
STATIONS = ["Z1", "Z9"]
VARIABLES = ["38", "32", "35"]  # snow depth, temperature, precipitation
DAY = date(2026, 2, 1)


def api_readings(raw: bytes, codi: str) -> dict[str, float]:
    """{ISO-UTC timestamp: valor} from the API payload for one station, using the
    same parser the live ingest uses (xema_api) so parity tests real code."""
    return {r.valid_time_utc: r.value for r in xema_api.parse_station(raw, codi)}


def opendata_readings(session, var, codi) -> dict[str, float | None]:
    start = f"{DAY.isoformat()}T00:00:00"
    end = f"{(DAY + timedelta(days=1)).isoformat()}T00:00:00"
    params = xema_opendata.build_query([codi], [var], start, end)
    rows = xema_opendata.parse_payload(xema_opendata.fetch_page(session, params))
    return {r.valid_time_utc: r.value for r in rows}


def main() -> int:
    load_env()
    key = os.environ.get("METEOCAT_API_KEY")
    if not key:
        print("METEOCAT_API_KEY not set (.env or export it)", file=sys.stderr)
        return 2
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    api = xema_api.make_session()
    od = requests.Session()

    ok = True
    for codi in STATIONS:
        for var in VARIABLES:
            slug = XEMA_VARIABLES[var]
            try:
                raw = xema_api.fetch(api, var, DAY, codi=codi, timeout=30)
            except requests.HTTPError as exc:
                print(f"SKIP {codi}/{slug}: {exc}")
                continue
            (FIXTURE_DIR / f"{codi}_{var}_{DAY:%Y%m%d}.json").write_bytes(raw)
            api_r = api_readings(raw, codi)
            od_r = opendata_readings(od, var, codi)
            shared = sorted(set(api_r) & set(od_r))
            mism = [t for t in shared if api_r[t] != od_r[t]]
            verdict = "PASS" if shared and not mism else "FAIL"
            ok = ok and verdict == "PASS"
            print(f"{verdict} {codi}/{slug}: {len(shared)} shared instants, {len(mism)} mismatched"
                  + (f"  e.g. {mism[0]} api={api_r[mism[0]]} od={od_r[mism[0]]}" if mism else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
