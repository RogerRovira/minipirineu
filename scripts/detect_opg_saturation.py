"""Detect where Open-Meteo's precipitation saturates with elevation (S1.3).

Open-Meteo downscales *temperature* to the `elevation` we ask for, but not
precipitation: `elevation` only steers which grid cell is selected, and once the
requested height is above every neighbouring cell the SAME cell answers for
every higher band — precipitation then stops responding to elevation altogether
(the roadmap's "Baqueira 2000 m ≡ 2600 m").

This script measures that from data we already own: every committed revision of
`data/openmeteo.json` is one real 6 h run with all three bands of all three
resorts. Two bands whose 6 h precipitation vectors are IDENTICAL across a wet
run share one grid cell. No API calls, no quota, fully reproducible:

    python scripts/detect_opg_saturation.py            # summary table
    python scripts/detect_opg_saturation.py --json     # machine-readable

Reads git history directly: no datastore branch, no API key, no quota. Needs a
full clone — a depth-1 checkout holds a single run and decides nothing.
"""

import argparse
import collections
import json
import subprocess
import sys

from minipirineu import opg

DATA_PATH = "data/openmeteo.json"
# A run only carries evidence if it is actually raining somewhere in the pair:
# two dry bands are trivially identical (all zeros) and prove nothing.
WET_RUN_MM = 2.0


def run_revisions(path: str = DATA_PATH) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%H", "--all", "--", path],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


def load_revision(sha: str, path: str = DATA_PATH) -> dict:
    blob = subprocess.run(["git", "show", f"{sha}:{path}"],
                          capture_output=True, text=True, check=True).stdout
    return json.loads(blob)


def band_precip_vectors(station: dict, model_index: int) -> dict[int, tuple]:
    """{band elevation: 6 h precipitation vector} for one model of one station."""
    return {
        band["elevation_m"]: tuple(iv["precipitation_mm"] for iv in band["models"][model_index]["intervals"])
        for band in station["bands"]
    }


def _total(vec: tuple) -> float:
    return sum(v or 0.0 for v in vec)


def scan(revisions, wet_mm: float = WET_RUN_MM, load=load_revision) -> dict:
    """Per station×model: the run-by-run saturation verdict plus pair counters.

    The verdict comes from the production rule (`opg.detect_reference`) applied
    to each run, so this script measures exactly what the correction believes.
    The pair counters are the human-readable evidence behind it; a pair only
    counts when its LOWER band is wet (≥ wet_mm over the 48 h), because two dry
    bands are trivially identical and prove nothing.
    """
    stats: dict = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"wet_runs": 0, "identical": 0, "different": 0, "sum_lo": 0.0, "sum_hi": 0.0}))
    verdicts: dict = collections.defaultdict(collections.Counter)
    n_runs = 0
    for sha in revisions:
        snapshot = load(sha)
        n_runs += 1
        for station in snapshot["stations"]:
            for model_index, model in enumerate(station["bands"][0]["models"]):
                key = (station["id"], model["model"])
                vectors = band_precip_vectors(station, model_index)
                detection = opg.detect_reference(vectors, min_total_mm=wet_mm)
                if detection.decidable:
                    verdicts[key][detection.reference_m] += 1
                elevations = sorted(vectors)
                for lo, hi in zip(elevations, elevations[1:]):
                    lo_total, hi_total = _total(vectors[lo]), _total(vectors[hi])
                    if lo_total < wet_mm:
                        continue
                    cell = stats[key][(lo, hi)]
                    cell["wet_runs"] += 1
                    cell["identical" if vectors[lo] == vectors[hi] else "different"] += 1
                    cell["sum_lo"] += lo_total
                    cell["sum_hi"] += hi_total
    return {"n_runs": n_runs, "stats": stats, "verdicts": verdicts}


def reference_elevation(verdicts: collections.Counter) -> tuple[int | None, int, int]:
    """(reference, agreeing runs, decidable runs) from the per-run verdicts.

    A reference is only trustworthy if every decidable run agrees; disagreement
    is surfaced (agreeing < decidable) rather than averaged away, because it
    would mean Open-Meteo's cell selection is not stable at that point.
    """
    decidable = sum(verdicts.values())
    if not decidable:
        return None, 0, 0
    reference, agreeing = verdicts.most_common(1)[0]
    return reference, agreeing, decidable


def implied_gradient_per_100m(pairs: dict) -> list[tuple[tuple[int, int], float]]:
    """The model's OWN resolved precipitation-elevation gradient, from the band
    pairs that do NOT saturate: (Σhi/Σlo − 1) per 100 m. This is the prior the
    correction extrapolates above the saturation elevation."""
    out = []
    for (lo, hi), c in sorted(pairs.items()):
        if c["different"] == 0 or c["sum_lo"] <= 0:
            continue
        out.append(((lo, hi), (c["sum_hi"] / c["sum_lo"] - 1.0) / ((hi - lo) / 100.0)))
    return out


def report(scanned: dict) -> dict:
    out = {"n_runs": scanned["n_runs"], "wet_run_mm": WET_RUN_MM, "points": []}
    for (station, model), pairs in sorted(scanned["stats"].items()):
        reference, agreeing, decidable = reference_elevation(scanned["verdicts"][(station, model)])
        out["points"].append({
            "station": station,
            "model": model,
            "reference_elevation_m": reference,
            "runs_agreeing": agreeing,
            "runs_decidable": decidable,
            "configured_reference_m": opg.resolve_reference(station, model).elevation_m,
            "pairs": [
                {"lo_m": lo, "hi_m": hi, **c,
                 "ratio": (c["sum_hi"] / c["sum_lo"]) if c["sum_lo"] else None}
                for (lo, hi), c in sorted(pairs.items())
            ],
            "implied_gradient_per_100m": [
                {"lo_m": lo, "hi_m": hi, "value": round(g, 4)}
                for (lo, hi), g in implied_gradient_per_100m(pairs)
            ],
        })
    return out


def print_report(rep: dict) -> None:
    print(f"{rep['n_runs']} committed runs of {DATA_PATH}; "
          f"a pair counts when its lower band has ≥ {rep['wet_run_mm']} mm/48 h\n")
    header = f"{'station':11s} {'model':14s} {'pair':13s} {'wet':>4s} {'ident':>6s} {'diff':>5s} {'Σhi/Σlo':>8s} {'%/100m':>7s}"
    print(header)
    print("-" * len(header))
    for point in rep["points"]:
        gradients = {(g["lo_m"], g["hi_m"]): g["value"] for g in point["implied_gradient_per_100m"]}
        model = point["model"].replace("meteofrance_arome_", "")
        for pair in point["pairs"]:
            key = (pair["lo_m"], pair["hi_m"])
            grad = gradients.get(key)
            print(f"{point['station']:11s} {model:14s} {pair['lo_m']}->{pair['hi_m']:<7d} "
                  f"{pair['wet_runs']:4d} {pair['identical']:6d} {pair['different']:5d} "
                  f"{(pair['ratio'] or 0):8.3f} "
                  f"{(f'{grad * 100:+.1f}' if grad is not None else 'saturated'):>7s}")
        agree = f"{point['runs_agreeing']}/{point['runs_decidable']} decidable runs agree"
        configured = point["configured_reference_m"]
        match = "" if configured == point["reference_elevation_m"] else \
            f"  ⚠ config says {configured}"
        print(f"{'':11s} {model:14s} → reference (saturation) elevation: "
              f"{point['reference_elevation_m'] or 'none — bands still resolve their own cell'}"
              f" ({agree}){match}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the machine-readable report")
    args = ap.parse_args(argv)
    revisions = run_revisions()
    if not revisions:
        print(f"no committed revisions of {DATA_PATH} found", file=sys.stderr)
        return 2
    rep = report(scan(revisions))
    print(json.dumps(rep, indent=2, sort_keys=True) if args.json else "", end="")
    if not args.json:
        print_report(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
