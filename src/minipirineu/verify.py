"""verify.py — the metric engine (S0.5/T8).

One scoring path for the pre-winter backtest (T10) and the live loop (T11): it
takes (forecast column × station × lead) pairs against the merged truth (T7) and
computes accumulation and event skill, with no knowledge of where the pairs came
from (ADR-0003). That symmetry is the point — the frozen baseline and the live
"how wrong were we" page are the same numbers computed the same way.

Metric spec (docs/ROADMAP.md §1): cm MAE/bias per (column, station, lead) on 6 h
buckets and 24 h totals; a dead band that forgives sensor-floor noise; POD/FAR/CSI
for per-bucket snow events and ≥1 cm/24 h snow days. Truth exclusions drop the
pair; phase_only truth (melt/rain-on-snow, cm only a lower bound) feeds the event
metrics but not the cm ones. Missing stays missing: an absent group scores None,
never 0.

Forecast rows live in the verification store under variable `fx.snowfall_cm.<column>`
(source-agnostic, so every column — AROME HD/2.5, Meteocat, later HARMONIE/IFS —
is pulled by one query), station = XEMA truth-station code, valid_time = 6 h
bucket start. T9/T11 write to this convention.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from minipirineu.archive import Archive
from minipirineu.config import (
    DEAD_BAND_ABS_CM,
    DEAD_BAND_FRAC,
    EVENT_BUCKET_CM,
    SNOW_DAY_CM,
    XEMA_STATIONS,
)
from minipirineu import store
from minipirineu.truth import parse_stamp
from minipirineu.truth_b import station_merged_truth

FORECAST_PREFIX = "fx.snowfall_cm."


def forecast_variable(column: str) -> str:
    """Store `variable` for a forecast column's snowfall (T9/T11 write these)."""
    return f"{FORECAST_PREFIX}{column}"


def forecast_column(variable: str) -> str | None:
    """Inverse of forecast_variable; None if `variable` is not a forecast row."""
    return variable[len(FORECAST_PREFIX):] if variable.startswith(FORECAST_PREFIX) else None


@dataclass(frozen=True)
class Pair:
    column: str
    station: str
    resort: str | None
    run_utc: str
    valid_bucket_utc: str
    lead_h: float
    forecast_cm: float
    truth_cm: float
    phase_only: bool


@dataclass(frozen=True)
class DayTotal:
    column: str
    station: str
    resort: str | None
    date_utc: str
    forecast_cm: float
    truth_cm: float
    phase_only: bool  # any constituent bucket was melt/lower-bound → cm not scored


# --- metric primitives (over anything with forecast_cm/truth_cm/phase_only) --

def _accumulation(items) -> dict:
    """cm MAE, bias and dead-band hit-rate over cm-eligible items (phase_only
    excluded — its cm is only a lower bound)."""
    cm = [it for it in items if not it.phase_only]
    if not cm:
        return {"n_cm": 0, "mae": None, "bias": None, "dead_band_rate": None}
    errs = [it.forecast_cm - it.truth_cm for it in cm]
    hits = sum(
        abs(e) <= max(DEAD_BAND_ABS_CM, DEAD_BAND_FRAC * it.truth_cm)
        for it, e in zip(cm, errs)
    )
    return {
        "n_cm": len(cm),
        "mae": statistics.fmean(abs(e) for e in errs),
        "bias": statistics.fmean(errs),
        "dead_band_rate": hits / len(cm),
    }


def _contingency(items, threshold: float) -> tuple[int, int, int, int]:
    """(hits, misses, false_alarms, correct_negatives) for an event at ≥ threshold.
    All items feed events — phase_only included (a melt bucket still had precip)."""
    h = m = f = c = 0
    for it in items:
        obs, fx = it.truth_cm >= threshold, it.forecast_cm >= threshold
        h += obs and fx
        m += obs and not fx
        f += (not obs) and fx
        c += (not obs) and not fx
    return h, m, f, c


def _skill(h: int, m: int, f: int, c: int) -> dict:
    return {
        "n_event": h + m + f + c,
        "hits": h, "misses": m, "false_alarms": f, "correct_neg": c,
        "pod": h / (h + m) if h + m else None,
        "far": f / (h + f) if h + f else None,
        "csi": h / (h + m + f) if h + m + f else None,
    }


def bucket_metrics(items, event_cm: float = EVENT_BUCKET_CM) -> dict:
    return {**_accumulation(items), **_skill(*_contingency(items, event_cm))}


def _group(items, dims: tuple[str, ...]) -> dict:
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        groups[tuple(getattr(it, d) for d in dims)].append(it)
    return groups


def group_metrics(pairs, dims: tuple[str, ...], event_cm: float = EVENT_BUCKET_CM) -> dict:
    return {key: bucket_metrics(items, event_cm) for key, items in _group(pairs, dims).items()}


# --- 24 h totals + snow-day events ------------------------------------------

def daily_totals(pairs, buckets_per_day: int = 4) -> list[DayTotal]:
    """Sum forecast and truth over each UTC calendar day, per (column, station,
    run). A day is scored only when all `buckets_per_day` 6 h buckets are present
    for that run — an incomplete day is missing, never a partial total."""
    groups: dict[tuple, list] = defaultdict(list)
    for p in pairs:
        groups[(p.column, p.station, p.run_utc, p.valid_bucket_utc[:10])].append(p)
    totals: list[DayTotal] = []
    for (col, st, _run, date), ps in groups.items():
        if len(ps) != buckets_per_day:
            continue
        totals.append(DayTotal(
            col, st, ps[0].resort, date,
            sum(p.forecast_cm for p in ps), sum(p.truth_cm for p in ps),
            any(p.phase_only for p in ps),
        ))
    return totals


def snow_day_metrics(day_totals, dims: tuple[str, ...] = ("column",)) -> dict:
    return {
        key: {**_accumulation(items), **_skill(*_contingency(items, SNOW_DAY_CM))}
        for key, items in _group(day_totals, dims).items()
    }


# --- report -----------------------------------------------------------------

def _keyed(grouped: dict) -> dict:
    """Tuple group keys → "a|b" strings so the report is JSON-serializable."""
    return {"|".join(str(k) for k in key): val for key, val in grouped.items()}


def verify_report(pairs, *, event_cm: float = EVENT_BUCKET_CM) -> dict:
    """Full machine-readable report over a flat list of Pairs."""
    days = daily_totals(pairs)
    return {
        "n_pairs": len(pairs),
        "columns": sorted({p.column for p in pairs}),
        "bucket_6h": {
            "by_column": _keyed(group_metrics(pairs, ("column",), event_cm)),
            "by_column_lead": _keyed(group_metrics(pairs, ("column", "lead_h"), event_cm)),
            "by_column_station": _keyed(group_metrics(pairs, ("column", "station"), event_cm)),
        },
        "snow_day_24h": {"by_column": _keyed(snow_day_metrics(days))},
    }


def _fmt(x) -> str:
    if x is None:
        return "—"
    return f"{x:.2f}" if isinstance(x, float) else str(x)


def _table(title: str, grouped: dict) -> list[str]:
    cols = ("n_cm", "mae", "bias", "dead_band_rate", "pod", "far", "csi")
    lines = [f"### {title}", "", "| group | " + " | ".join(cols) + " |",
             "|" + "---|" * (len(cols) + 1)]
    for key, mx in sorted(grouped.items()):
        lines.append("| " + key + " | " + " | ".join(_fmt(mx.get(c)) for c in cols) + " |")
    return lines + [""]


def to_markdown(report: dict) -> str:
    out = [f"# Verification report — {report['n_pairs']} pairs",
           f"columns: {', '.join(report['columns']) or '(none)'}", ""]
    out += _table("6 h buckets, by column", report["bucket_6h"]["by_column"])
    out += _table("6 h buckets, by column × lead", report["bucket_6h"]["by_column_lead"])
    out += _table("6 h buckets, by column × station", report["bucket_6h"]["by_column_station"])
    out += _table("Snow days (24 h ≥ 1 cm), by column", report["snow_day_24h"]["by_column"])
    return "\n".join(out)


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


# --- verification-store facing ----------------------------------------------

def build_pairs(conn, stations, start_utc: str, end_utc: str, **truth_kw) -> list[Pair]:
    """Join stored forecast columns to the merged truth over [start, end).

    A pair needs a usable truth bucket: excluded buckets and None truth are
    dropped; phase_only buckets pair with their flag set. Forecast station codes
    are XEMA truth-station codes (forecasts re-fetched at those points, T9)."""
    resort_by = {s.codi: s.resort for s in XEMA_STATIONS}
    truth = {}
    for st in stations:
        for mt in station_merged_truth(conn, st, start_utc, end_utc, **truth_kw):
            if mt.excluded is None and mt.truth_cm is not None:
                truth[(st, mt.bucket_start_utc)] = mt
    rows = conn.execute(
        """SELECT station, run_time_utc, valid_time_utc, variable, value
           FROM verification_values
           WHERE variable LIKE ? AND valid_time_utc >= ? AND valid_time_utc < ?
             AND value IS NOT NULL""",
        (FORECAST_PREFIX + "%", start_utc, end_utc),
    ).fetchall()
    pairs = []
    for station, run, valid, variable, fval in rows:
        mt = truth.get((station, valid))
        if mt is None:
            continue
        lead_h = (parse_stamp(valid) - parse_stamp(run)).total_seconds() / 3600.0
        pairs.append(Pair(
            forecast_column(variable), station, resort_by.get(station),
            run, valid, lead_h, float(fval), mt.truth_cm, "phase_only" in mt.flags,
        ))
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score forecast columns against XEMA truth.")
    ap.add_argument("start", help="ISO-UTC window start, e.g. 2025-03-01T00:00:00Z")
    ap.add_argument("end", help="ISO-UTC window end (half-open)")
    ap.add_argument("--out-json", type=Path, help="write the machine report here")
    ap.add_argument("--out-md", type=Path, help="write the human report here")
    args = ap.parse_args(argv)

    conn = store.connect(Archive.from_env().root / "verification.sqlite")
    stations = [s.codi for s in XEMA_STATIONS if s.snow_truth]
    report = verify_report(build_pairs(conn, stations, args.start, args.end))
    if args.out_json:
        args.out_json.write_text(to_json(report))
    if args.out_md:
        args.out_md.write_text(to_markdown(report))
    if not (args.out_json or args.out_md):
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
