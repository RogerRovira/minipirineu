"""One-off: probe the Previous Runs API archive for AROME (T9/S0.6a).

Reproduces the coverage findings recorded in
docs/notes/previous-runs-coverage.md and re-records the parser fixture. No API
key needed (the Previous Runs API is free, non-commercial). Run locally:

    python scripts/probe_previous_runs.py            # print findings
    python scripts/probe_previous_runs.py --record   # also refresh the fixture

Checks, at Z1 (Bonaigua) coords:
  1. key structure + which `previous_dayN` are served for both AROME models;
  2. archive floor — null before 2024-01-19T12:00Z, populated at/after;
  3. AROME HD serves no snowfall (previous_day1 all-null → derived column);
  4. surface relative_humidity_2m served (both models) — the S1.1 wet-bulb
     backfill (BASE_VARS) depends on it.

Exits non-zero if any finding regresses (the API changed under us).
"""

import argparse
import sys
from pathlib import Path

import requests

from minipirineu import previous_runs
from minipirineu.config import PREVIOUS_RUNS_AROME_START_UTC

API = previous_runs.API_URL
Z1 = dict(latitude=42.64691, longitude=0.98486, elevation=2262)
A25 = "meteofrance_arome_france"
HD = "meteofrance_arome_france_hd"
FIXTURE = Path("tests/fixtures/previous_runs_arome_z1_20250201.json")


def _get(session, hourly, start, end) -> dict:
    # always request both models: Open-Meteo only suffixes hourly keys with the
    # model id when >1 model is requested (a single-model call is unsuffixed).
    resp = session.get(API, params={**Z1, "models": f"{HD},{A25}", "hourly": ",".join(hourly),
                                     "start_date": start, "end_date": end, "timezone": "UTC"},
                       timeout=60)
    resp.raise_for_status()
    return resp.json()


def _nonnull(series) -> int:
    return sum(1 for x in series if x is not None)


def probe(session) -> bool:
    ok = True
    h = ["temperature_2m_previous_day1", "temperature_2m_previous_day2",
         "snowfall_previous_day1", "snowfall_previous_day2"]
    win = _get(session, h, "2025-02-01", "2025-02-07")["hourly"]

    # 1 + 3: day1 served, day2 empty for AROME; HD snowfall null
    d1 = _nonnull(win[f"temperature_2m_previous_day1_{A25}"])
    d2 = _nonnull(win[f"temperature_2m_previous_day2_{A25}"])
    hd_snow = _nonnull(win[f"snowfall_previous_day1_{HD}"])
    a25_snow = _nonnull(win[f"snowfall_previous_day1_{A25}"])
    print(f"[1] 2.5 temperature previous_day1 non-null = {d1}/168  (expect 168)")
    print(f"[1] 2.5 temperature previous_day2 non-null = {d2}/168  (expect 0 — AROME horizon)")
    print(f"[3] HD  snowfall    previous_day1 non-null = {hd_snow}/168  (expect 0 — derived)")
    print(f"[3] 2.5 snowfall    previous_day1 non-null = {a25_snow}/168  (expect >0 native)")
    ok &= d1 == 168 and d2 == 0 and hd_snow == 0 and a25_snow > 0

    # 2: archive floor
    floor_day = PREVIOUS_RUNS_AROME_START_UTC[:10]  # 2024-01-19
    before = _get(session, ["temperature_2m_previous_day1"], "2024-01-10", "2024-01-18")
    at = _get(session, ["temperature_2m_previous_day1"], "2024-01-19", "2024-01-20")
    b_nn = _nonnull(before["hourly"][f"temperature_2m_previous_day1_{A25}"])
    at_series = at["hourly"][f"temperature_2m_previous_day1_{A25}"]
    at_times = at["hourly"]["time"]
    first = next((t for t, v in zip(at_times, at_series) if v is not None), None)
    print(f"[2] previous_day1 non-null before {floor_day} = {b_nn}  (expect 0)")
    print(f"[2] first non-null at/after {floor_day} = {first}  (expect {floor_day}T12:00)")
    ok &= b_nn == 0 and first == f"{floor_day}T12:00"

    # 4: surface RH served for both models (S1.1 wet-bulb column)
    rh = _get(session, ["relative_humidity_2m_previous_day1"], "2025-02-01", "2025-02-07")["hourly"]
    hd_rh = _nonnull(rh[f"relative_humidity_2m_previous_day1_{HD}"])
    a25_rh = _nonnull(rh[f"relative_humidity_2m_previous_day1_{A25}"])
    print(f"[4] HD  relative_humidity_2m previous_day1 non-null = {hd_rh}/168  (expect 168)")
    print(f"[4] 2.5 relative_humidity_2m previous_day1 non-null = {a25_rh}/168  (expect 168)")
    ok &= hd_rh == 168 and a25_rh == 168

    print("PASS — coverage matches docs/notes/previous-runs-coverage.md" if ok
          else "FAIL — a finding regressed; the API changed")
    return ok


def record_fixture(session) -> None:
    hourly = [previous_runs.previous_var(v, d)
              for d in (1, 2) for v in ("temperature_2m", "precipitation", "snowfall")]
    resp = session.get(API, params={**Z1, "models": f"{HD},{A25}", "hourly": ",".join(hourly),
                                     "start_date": "2025-02-01", "end_date": "2025-02-02",
                                     "timezone": "UTC"}, timeout=60)
    resp.raise_for_status()
    FIXTURE.write_bytes(resp.content)
    print(f"recorded {FIXTURE} ({len(resp.content)} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true", help="refresh the parser fixture")
    args = ap.parse_args()
    with requests.Session() as session:
        ok = probe(session)
        if args.record:
            record_fixture(session)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
