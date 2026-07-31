"""One-off: measure where each VERIFICATION point's precipitation saturates (S1.3).

The three resort points are measured for free from the archived runs
(`scripts/detect_opg_saturation.py`, three bands per run). The XEMA truth
stations are not: the backtest and the live pairing fetch them at ONE elevation,
so nothing in the archive can show whether that point already sits above its
highest neighbouring grid cell. Until this probe runs, those points inherit
their resort's saturation elevation — an explicit prior, flagged as such by
`opg.resolve_reference` (source="inherited").

This asks Open-Meteo directly: the same point and window at a ladder of
elevations, one call per elevation. Where two consecutive elevations return an
IDENTICAL precipitation series, the same grid cell answered both — the lower one
is the saturation elevation.

    python scripts/probe_opg_saturation.py                 # snow-truth stations
    python scripts/probe_opg_saturation.py --all-scored    # + valley stations
    python scripts/probe_opg_saturation.py --paste         # config-ready dict

Quota: free (Open-Meteo, non-commercial). ~4 calls per point; pick a WET window
(`--start/--end`, past dates use the Previous Runs archive floor rules) — a dry
window decides nothing and the probe says so rather than guessing.

Paste the printed dict into `config.OPG_PROBED_STATION_ELEVATION_M`, note the
window used in docs/notes/opg.md, and re-run the backtest so the OPG columns are
rebuilt on measured references.
"""

import argparse
import json
import sys

import requests

from minipirineu import opg
from minipirineu.config import MODELS, XEMA_STATIONS

API_URL = "https://api.open-meteo.com/v1/forecast"
MODEL_IDS = tuple(spec.id for spec in MODELS)
# Elevations probed per point: the station's own height and three steps up.
# 300 m steps resolve the reference finely enough for a +3 %/100 m correction
# (one step ≈ 9 % of precipitation).
LADDER_STEPS_M = (0, 300, 600, 900)


def fetch_precipitation(session, latitude, longitude, elevation_m, start, end, timeout=60) -> dict:
    """{model id: hourly precipitation} at one point/elevation over [start, end]."""
    params = {
        "latitude": latitude, "longitude": longitude, "elevation": elevation_m,
        "models": ",".join(MODEL_IDS), "hourly": "precipitation",
        "start_date": start, "end_date": end, "timezone": "UTC",
    }
    resp = session.get(API_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    hourly = resp.json()["hourly"]
    return {model_id: hourly[f"precipitation_{model_id}"] for model_id in MODEL_IDS}


def probe_point(session, station, start: str, end: str, steps=LADDER_STEPS_M) -> dict:
    """{model id: Detection} for one point, from an elevation ladder."""
    by_model: dict[str, dict[int, list]] = {model_id: {} for model_id in MODEL_IDS}
    for step in steps:
        elevation = station.altitude_m + step
        for model_id, series in fetch_precipitation(
                session, station.latitude, station.longitude, elevation, start, end).items():
            by_model[model_id][elevation] = series
    return {model_id: opg.detect_reference(by_elevation)
            for model_id, by_elevation in by_model.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", required=True, help="window start date (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="window end date, inclusive")
    ap.add_argument("--all-scored", action="store_true",
                    help="probe the valley stations too, not just the snow-truth EMAs")
    ap.add_argument("--paste", action="store_true",
                    help="print a config-ready OPG_PROBED_STATION_ELEVATION_M dict")
    args = ap.parse_args(argv)

    stations = [x for x in XEMA_STATIONS
                if x.snow_truth or (args.all_scored and x.resort)]
    probed: dict[str, dict[str, int | None]] = {}
    undecided = []
    with requests.Session() as session:
        for station in stations:
            detections = probe_point(session, station, args.start, args.end)
            for model_id, detection in detections.items():
                inherited = opg.resolve_reference(station.codi, model_id)
                if not detection.decidable:
                    undecided.append(f"{station.codi}/{model_id}")
                    verdict = "undecidable (window too dry — probe a wet window)"
                else:
                    probed.setdefault(station.codi, {})[model_id] = detection.reference_m
                    verdict = str(detection.reference_m)
                print(f"{station.codi} {station.name[:18]:18s} {station.altitude_m:5d} m "
                      f"{model_id:30s} measured={verdict:12s} "
                      f"(was {inherited.elevation_m}, {inherited.source})")

    if args.paste:
        print("\n# paste into config.OPG_PROBED_STATION_ELEVATION_M "
              f"(window {args.start}..{args.end})")
        print("{")
        for codi, by_model in sorted(probed.items()):
            for model_id, reference in sorted(by_model.items()):
                print(f'    ("{codi}", "{model_id}"): {reference},')
        print("}")
    if undecided:
        print(f"\nundecided (no verdict written): {', '.join(undecided)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
