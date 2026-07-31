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
    MARGINAL_T_C,
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


# --- phase skill on marginal buckets (S1.1 go/no-go) ------------------------

@dataclass(frozen=True)
class PhaseItem:
    """One bucket where a rain/snow call was warranted (precipitation fell).
    band_t_c is the bucket-mean forecast band temperature (the marginal filter);
    the two booleans are the forecast's and the observation's snow/rain call.
    Built by the S1.1 backtest evaluator, which owns the domain definitions."""

    band_t_c: float
    forecast_snow: bool
    obs_snow: bool


def phase_hit_rate(items, *, marginal_t_c: float = MARGINAL_T_C) -> dict:
    """Fraction of MARGINAL buckets whose forecast phase matches the observed
    phase (S1.1 go/no-go). Marginal = |bucket-mean band T| ≤ marginal_t_c, the
    band near 0 °C where the rain/snow call is genuinely in doubt and the
    wet-bulb driver can help; away from freezing every method agrees, so scoring
    there would only dilute the signal. Returns the marginal event count (the
    ≥30 sample-size gate) and the hit rate (None if no marginal events)."""
    marginal = [it for it in items if abs(it.band_t_c) <= marginal_t_c]
    if not marginal:
        return {"n_events": 0, "hit_rate": None}
    hits = sum(it.forecast_snow == it.obs_snow for it in marginal)
    return {"n_events": len(marginal), "hit_rate": hits / len(marginal)}


def _group(items, dims: tuple[str, ...]) -> dict:
    groups: dict[tuple, list] = defaultdict(list)
    for it in items:
        groups[tuple(getattr(it, d) for d in dims)].append(it)
    return groups


def group_metrics(pairs, dims: tuple[str, ...], event_cm: float = EVENT_BUCKET_CM) -> dict:
    return {key: bucket_metrics(items, event_cm) for key, items in _group(pairs, dims).items()}


# --- 24 h totals + snow-day events ------------------------------------------

def daily_totals(pairs, buckets_per_day: int = 4, *, daily_by: str = "run") -> list[DayTotal]:
    """Sum forecast and truth over each UTC calendar day. A day is scored only
    when all `buckets_per_day` 6 h buckets are present — an incomplete day is
    missing, never a partial total.

    `daily_by` selects what a "day" groups on:
      - "run" (live loop, default): buckets sharing one forecast run, which
        covers the day at increasing leads — the structure a 6 h cron produces.
      - "date" (fixed-lead backtest): all buckets of a UTC calendar day
        regardless of run. The Previous-Runs reconstruction gives each 6 h
        bucket a distinct run (valid − 24 h), so a day never shares one run and
        "run" grouping would score zero days (T10 finding).
    """
    # Key each day by its distinct 6 h valid buckets, not by a running list: a
    # bucket covered by more than one run (a live/multi-run store scored with
    # daily_by="date") then counts once, never doubling the day's total. In "run"
    # mode each run already has one pair per valid bucket, so this is a no-op.
    groups: dict[tuple, dict[str, "Pair"]] = defaultdict(dict)
    for p in pairs:
        run_key = p.run_utc if daily_by == "run" else ""
        groups[(p.column, p.station, run_key, p.valid_bucket_utc[:10])][p.valid_bucket_utc] = p
    totals: list[DayTotal] = []
    for (col, st, _run, date), by_bucket in groups.items():
        if len(by_bucket) != buckets_per_day:
            continue
        ps = list(by_bucket.values())
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


def verify_report(pairs, *, event_cm: float = EVENT_BUCKET_CM, daily_by: str = "run") -> dict:
    """Full machine-readable report over a flat list of Pairs. `daily_by` is
    passed to daily_totals — "date" for the fixed-lead backtest (T10)."""
    days = daily_totals(pairs, daily_by=daily_by)
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
    ap.add_argument("--daily-by", choices=("run", "date"), default="run",
                    help="24 h day grouping: 'run' (live loop) or 'date' "
                         "(fixed-lead backtest, where each bucket has its own run)")
    args = ap.parse_args(argv)

    conn = store.connect(Archive.from_env().root / "verification.sqlite")
    stations = [s.codi for s in XEMA_STATIONS if s.snow_truth]
    report = verify_report(build_pairs(conn, stations, args.start, args.end),
                           daily_by=args.daily_by)
    if args.out_json:
        args.out_json.write_text(to_json(report))
    if args.out_md:
        args.out_md.write_text(to_markdown(report))
    if not (args.out_json or args.out_md):
        print(to_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
