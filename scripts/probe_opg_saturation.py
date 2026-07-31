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
is the saturation elevation. The ladder reaches BELOW the station as well as
above: a point already saturated at its own altitude would otherwise report its
own height as the reference, which computes to no correction at all (the
2026-06-02 probe hit exactly this at Z2 and Z1/HD).

    python scripts/probe_opg_saturation.py --start 2025-02-01 --end 2025-02-07
    python scripts/probe_opg_saturation.py --start … --end … --all-scored
    python scripts/probe_opg_saturation.py --start … --end … --paste

Quota: free (Open-Meteo, non-commercial). ~6 calls per point; pick a genuinely
WET window (`--start/--end`) — the probe prints the mm and wet hours each
verdict rests on, and refuses to write one that is merely a bound or that rests
on too few wet hours (two different cells match a mostly-zero light spell by
coincidence). Exit code is non-zero unless every point was decided outright.

Paste the printed dict into `config.OPG_PROBED_STATION_ELEVATION_M`, note the
window used in docs/notes/opg.md, and re-run the backtest so the OPG columns are
rebuilt on measured references.
"""

import argparse
import sys

import requests

from minipirineu import opg
from minipirineu.config import MODELS, XEMA_STATIONS

API_URL = "https://api.open-meteo.com/v1/forecast"
MODEL_IDS = tuple(spec.id for spec in MODELS)
# Elevations probed per point, relative to the station's own height. The ladder
# MUST reach below the station: a point can already be above its highest
# neighbouring cell at its own altitude (measured — Z2 Boí and Z1/HD both did on
# 2026-06-02), and an upward-only ladder can then only report the station height
# itself. That reads as "reference = station elevation" → factor 1.0 → the
# correction silently switches itself off exactly where it is most warranted.
# Going down finds the elevation the saturating cell actually represents.
# 300 m steps resolve the reference finely enough for a +3 %/100 m correction
# (one step ≈ 9 % of precipitation).
LADDER_STEPS_M = (-900, -600, -300, 0, 300, 600)


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


def evidence(series) -> tuple[float, int]:
    """(total mm, non-zero hours) — how much a rung's verdict actually rests on."""
    return round(sum(v or 0.0 for v in series), 1), sum(1 for v in series if v)


def probe_point(session, station, start: str, end: str, steps=LADDER_STEPS_M) -> dict:
    """{model id: (Detection, {elevation: (mm, wet hours)})} for one point."""
    by_model: dict[str, dict[int, list]] = {model_id: {} for model_id in MODEL_IDS}
    for step in steps:
        elevation = station.altitude_m + step
        if elevation <= 0:
            continue
        for model_id, series in fetch_precipitation(
                session, station.latitude, station.longitude, elevation, start, end).items():
            by_model[model_id][elevation] = series
    return {model_id: (opg.detect_reference(by_elevation),
                       {e: evidence(s) for e, s in sorted(by_elevation.items())})
            for model_id, by_elevation in by_model.items()}


def bound_warning(detection, evidence_by_elevation: dict) -> str | None:
    """Message for a verdict that is only an upper bound (opg.is_bound_only)."""
    if not opg.is_bound_only(detection, evidence_by_elevation):
        return None
    return (f"saturates at the lowest rung ({detection.reference_m} m) — upper "
            f"bound only; re-probe with a ladder reaching further down")


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
    undecided, bounded = [], []
    with requests.Session() as session:
        for station in stations:
            for model_id, (detection, ev) in probe_point(
                    session, station, args.start, args.end).items():
                inherited = opg.resolve_reference(station.codi, model_id)
                warning = bound_warning(detection, ev)
                if not detection.decidable:
                    undecided.append(f"{station.codi}/{model_id}")
                    verdict = "undecidable (too dry / too few wet hours)"
                elif warning:
                    # a bound is not a measurement: writing it into the config
                    # would set Δz = 0 and quietly disable the correction
                    bounded.append(f"{station.codi}/{model_id}")
                    verdict = f"≤ {detection.reference_m} (bound, not written)"
                else:
                    probed.setdefault(station.codi, {})[model_id] = detection.reference_m
                    verdict = str(detection.reference_m)
                print(f"{station.codi} {station.name[:18]:18s} {station.altitude_m:5d} m "
                      f"{model_id:30s} measured={verdict:32s} "
                      f"(was {inherited.elevation_m}, {inherited.source})")
                # the evidence each verdict rests on: identity across series that
                # are mostly zeros is cheap, so the wet-hour count matters
                print("      " + "  ".join(f"{e}m: {mm}mm/{wet}h"
                                           for e, (mm, wet) in ev.items()))
                if warning:
                    print(f"      ⚠ {warning}")

    if args.paste:
        print("\n# paste into config.OPG_PROBED_STATION_ELEVATION_M "
              f"(window {args.start}..{args.end})")
        print("{")
        for codi, by_model in sorted(probed.items()):
            for model_id, reference in sorted(by_model.items()):
                print(f'    ("{codi}", "{model_id}"): {reference},')
        print("}")
    if bounded:
        print(f"\nbound only, no verdict written: {', '.join(bounded)} — the ladder's "
              f"lowest rung already saturates; re-probe deeper", file=sys.stderr)
    if undecided:
        print(f"\nundecided (no verdict written): {', '.join(undecided)}", file=sys.stderr)
    return 1 if (undecided or bounded) else 0


if __name__ == "__main__":
    sys.exit(main())
