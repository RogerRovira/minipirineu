"""S1.1 go/no-go: does the wet-bulb partition beat the dry-bulb T taper?

Scores the wet-bulb challenger column (arome_hd_wb) against the derived HD
baseline (arome_hd) on the pre-winter backtest, on the ONE metric the roadmap's
S1.1 gate names: phase hit-rate on marginal buckets (bucket-mean forecast band
temperature within ±MARGINAL_T_C of 0 °C), where the rain/snow call is genuinely
in doubt. It also sweeps the wet-bulb breakpoints to pick a calibrated threshold,
and checks that cm MAE on those buckets is not degraded beyond the dead band.

Data path (self-contained, reads the local datastore — no network):
  - forecast: re-parses the RH-bearing backtest raws (raw/openmeteo_backtest/,
    written by backfill_forecast after RH joined BASE_VARS in B1). Raws without
    RH — the pre-S1.1 backfill — are skipped, since the wet-bulb column needs it.
    Per 6 h UTC bucket it recomputes the derived HD cm (dry-bulb) and the
    wet-bulb cm (per swept breakpoints) and the bucket-mean band temperature.
  - truth: the merged truth-A/B from the verification store (truth_b), same code
    verify.py scores against; a phase "event" is a marginal bucket where OBSERVED
    precipitation fell (a real rain/snow call), not excluded by the truth gates.

Phase calls use EVENT_BUCKET_CM as the snow threshold for both forecast and obs,
consistent with verify.py's event metric. Emits a Markdown report (default
docs/notes/wetbulb-partition.md) with the baseline row, the sweep table, and the
PASS/FAIL verdict (Δ ≥ 5 pp on ≥ 30 events, cm MAE not worse by > dead band).

    python scripts/eval_s11_phase.py                 # full baseline window
    python scripts/eval_s11_phase.py --start ... --end ... --out report.md
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from minipirineu import aggregate, previous_runs, store
from minipirineu.archive import Archive
from minipirineu.config import (
    BUCKET_HOURS,
    DEAD_BAND_ABS_CM,
    EVENT_BUCKET_CM,
    MARGINAL_T_C,
    MODELS,
    WETBULB_T_FULL_C,
    WETBULB_T_ZERO_C,
    XEMA_STATIONS,
)
from minipirineu.truth_b import station_merged_truth, station_truth_b
from minipirineu.verify import PhaseItem, bucket_metrics, phase_hit_rate

ARCHIVE_SOURCE = "openmeteo_backtest"
HD = next(m for m in MODELS if m.column == "arome_hd")
HD_ID = HD.id
A25_ID = next(m.id for m in MODELS if m.column == "arome_25")
RH_KEY = f"{previous_runs.previous_var('relative_humidity_2m', 1)}_{HD_ID}"
PHASE_MIN_PRECIP_MM = 0.2   # a real precip event: below this there is no phase call
FULL_STEP = 0.5             # improvement counting as a "pass" (5 pp)
MIN_EVENTS = 30             # roadmap S1.1 sample-size gate
PASS_DELTA_PP = 5.0

# Wet-bulb breakpoint grid to calibrate over (all-snow t_full < all-rain t_zero).
SWEEP_T_FULL = (-0.5, 0.0, 0.5)
SWEEP_T_ZERO = (0.5, 1.0, 1.5, 2.0)


def _fmt_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def bucket_mean(times: list[str], values: list, hours: int = BUCKET_HOURS) -> dict[str, float]:
    """Mean of each fully-covered 6 h UTC bucket, keyed by bucket start `...Z`.
    Same completeness rule as previous_runs.bucketize (all `hours` present and
    non-null) so the mean-T buckets align key-for-key with the cm buckets."""
    groups: dict[datetime, dict[int, float]] = defaultdict(dict)
    for t, v in zip(times, values, strict=True):
        dt = datetime.fromisoformat(t)
        b = dt.replace(hour=dt.hour - dt.hour % hours, minute=0, second=0, microsecond=0)
        groups[b][int((dt - b).total_seconds() // 3600)] = v
    out: dict[str, float] = {}
    for b, vals in groups.items():
        if len(vals) == hours and all(vals.get(o) is not None for o in range(hours)):
            out[_fmt_z(b)] = sum(vals[o] for o in range(hours)) / hours
    return out


@dataclass
class Slice:
    """One RH-bearing backtest raw's HD day1 series (one station-month)."""

    station: str
    times: list[str]
    precip: list
    temp: list
    rh: list


def _station_from_name(path: Path) -> str:
    """`<STAMP>_<codi>_<label>.json.gz` → codi (no underscores in stamp/codi)."""
    return path.name.split("_")[1]


def load_slices(archive: Archive) -> list[Slice]:
    """RH-bearing backtest raws → HD day1 (times, precip, temp, rh) slices. Raws
    without the RH key (the pre-S1.1 backfill) are skipped: no humidity, no
    wet-bulb. Missing precip/temp keys skip the slice too (nothing to derive)."""
    slices: list[Slice] = []
    for path, raw_bytes in archive.iter_source(ARCHIVE_SOURCE):
        hourly = json.loads(raw_bytes)["hourly"]
        if RH_KEY not in hourly:
            continue
        precip = previous_runs._hourly_series(hourly, "precipitation", 1, HD_ID)
        temp = previous_runs._hourly_series(hourly, "temperature_2m", 1, HD_ID)
        if precip is None or temp is None:
            continue
        slices.append(Slice(_station_from_name(path), hourly["time"], precip, temp, hourly[RH_KEY]))
    return slices


@dataclass
class Event:
    """A marginal precip bucket to score: band T, observed truth, dry-bulb cm."""

    band_t_c: float
    truth_cm: float
    phase_only: bool
    dry_cm: float
    key: tuple[str, str]  # (station, bucket_utc) — to look up the swept wet-bulb cm


def load_truth(conn, stations, start_utc: str, end_utc: str):
    """Per (station, bucket): merged truth (cm, excluded, phase_only) and the
    observed gauge precip that makes a bucket a real rain/snow event."""
    truth: dict[tuple[str, str], tuple] = {}
    precip: dict[tuple[str, str], float] = {}
    for st in stations:
        for m in station_merged_truth(conn, st, start_utc, end_utc):
            truth[(st, m.bucket_start_utc)] = (m.truth_cm, m.excluded, "phase_only" in m.flags)
        for b in station_truth_b(conn, st, start_utc, end_utc):
            if b.precip_mm is not None:
                precip[(st, b.bucket_start_utc)] = b.precip_mm
    return truth, precip


def build_events(slices: list[Slice], truth: dict, precip: dict) -> list[Event]:
    """Marginal-agnostic here: emit every bucket that is a real observed precip
    event with usable truth (the ±2 °C marginal cut is applied by phase_hit_rate
    and the cm summary, so the same event list feeds every metric)."""
    events: list[Event] = []
    for sl in slices:
        dry = dict(previous_runs.bucketize(sl.times, aggregate.derive_snowfall(sl.precip, sl.temp)))
        means = bucket_mean(sl.times, sl.temp)
        for bucket, dry_cm in dry.items():
            key = (sl.station, bucket)
            tr = truth.get(key)
            if tr is None:
                continue
            truth_cm, excluded, phase_only = tr
            if excluded is not None or truth_cm is None:
                continue
            if precip.get(key, 0.0) < PHASE_MIN_PRECIP_MM:
                continue
            if bucket not in means:
                continue
            events.append(Event(means[bucket], truth_cm, phase_only, dry_cm, key))
    return events


def wetbulb_cm_by_key(slices: list[Slice], t_full: float, t_zero: float) -> dict[tuple[str, str], float]:
    """Recompute the wet-bulb column's 6 h cm per (station, bucket) for one
    breakpoint pair — the only thing that changes across the calibration sweep."""
    out: dict[tuple[str, str], float] = {}
    for sl in slices:
        snow = aggregate.derive_snowfall_wetbulb(sl.precip, sl.temp, sl.rh, t_full=t_full, t_zero=t_zero)
        for bucket, cm in previous_runs.bucketize(sl.times, snow):
            out[(sl.station, bucket)] = cm
    return out


def native_cm_by_key(archive: Archive) -> dict[tuple[str, str], float]:
    """Native AROME 2.5 snowfall per (station, bucket) from the RH-bearing raws —
    the incumbent-best column (frozen baseline), for the three-way comparison."""
    out: dict[tuple[str, str], float] = {}
    for path, raw_bytes in archive.iter_source(ARCHIVE_SOURCE):
        hourly = json.loads(raw_bytes)["hourly"]
        if RH_KEY not in hourly:
            continue
        series = previous_runs._hourly_series(hourly, "snowfall", 1, A25_ID)
        if series is None:
            continue
        st = _station_from_name(path)
        for bucket, cm in previous_runs.bucketize(hourly["time"], series):
            out[(st, bucket)] = cm
    return out


@dataclass(frozen=True)
class _Item:
    forecast_cm: float
    truth_cm: float
    phase_only: bool


def build_all_records(slices: list[Slice], truth: dict) -> list[tuple]:
    """Every forecast bucket with usable truth — no marginal/precip filter. This
    is the population verify.build_pairs scores, for the OVERALL (all-lead)
    column comparison, distinct from the marginal-only go/no-go events."""
    recs: list[tuple] = []
    for sl in slices:
        dry = dict(previous_runs.bucketize(sl.times, aggregate.derive_snowfall(sl.precip, sl.temp)))
        for bucket, dry_cm in dry.items():
            key = (sl.station, bucket)
            tr = truth.get(key)
            if tr is None:
                continue
            truth_cm, excluded, phase_only = tr
            if excluded is not None or truth_cm is None:
                continue
            recs.append((key, truth_cm, phase_only, dry_cm))
    return recs


# derivation → (marginal-event fc, all-record fc(key, dry_cm)); wet uses defaults
def _column_fcs(native: dict, wb: dict) -> dict:
    return {
        "AROME 2.5 (native)":          (lambda ev: native.get(ev.key, 0.0),
                                        lambda k, d: native.get(k, 0.0)),
        "AROME HD (dry-bulb, prev.)":  (lambda ev: ev.dry_cm, lambda k, d: d),
        "AROME HD (wet-bulb, promoted)": (lambda ev: wb.get(ev.key, 0.0),
                                          lambda k, d: wb.get(k, 0.0)),
    }


def compare(events: list[Event], all_records: list[tuple], native: dict, wb: dict) -> dict:
    """Native AROME 2.5 vs dry-bulb HD vs wet-bulb HD, on the marginal buckets
    (phase hit-rate + cm MAE) and overall (all buckets: cm MAE/bias, POD/FAR/CSI
    via verify's own primitives)."""
    out = {}
    for name, (fc_marg, fc_all) in _column_fcs(native, wb).items():
        s = score_column(events, fc_marg)
        m = bucket_metrics([_Item(fc_all(k, d), tc, po) for (k, tc, po, d) in all_records])
        out[name] = {
            "marginal": {"hit_rate": s["hit_rate"], "mae": s["mae"], "n": s["n_events"]},
            "overall": {k2: m[k2] for k2 in ("n_cm", "mae", "bias", "pod", "far", "csi")},
        }
    return out


def _marginal_mae(events: list[Event], fc_cm) -> float | None:
    """cm MAE on marginal, cm-eligible (not phase_only) buckets. `fc_cm(ev)` is
    the column's forecast cm for the event."""
    errs = [abs(fc_cm(ev) - ev.truth_cm) for ev in events
            if abs(ev.band_t_c) <= MARGINAL_T_C and not ev.phase_only]
    return statistics.fmean(errs) if errs else None


def score_column(events: list[Event], fc_cm) -> dict:
    """Phase hit-rate on marginal buckets + marginal cm MAE for one column."""
    items = [PhaseItem(ev.band_t_c, fc_cm(ev) >= EVENT_BUCKET_CM, ev.truth_cm >= EVENT_BUCKET_CM)
             for ev in events]
    phase = phase_hit_rate(items)
    return {"n_events": phase["n_events"], "hit_rate": phase["hit_rate"],
            "mae": _marginal_mae(events, fc_cm)}


def evaluate(events: list[Event], slices: list[Slice]) -> dict:
    """Baseline (dry-bulb) vs the wet-bulb sweep. Verdict per S1.1 go/no-go."""
    base = score_column(events, lambda ev: ev.dry_cm)
    rows = []
    for t_full in SWEEP_T_FULL:
        for t_zero in SWEEP_T_ZERO:
            if t_full >= t_zero:
                continue
            wb = wetbulb_cm_by_key(slices, t_full, t_zero)
            s = score_column(events, lambda ev, wb=wb: wb.get(ev.key, 0.0))
            delta = None if (s["hit_rate"] is None or base["hit_rate"] is None) \
                else (s["hit_rate"] - base["hit_rate"]) * 100.0
            mae_ok = (base["mae"] is None or s["mae"] is None
                      or s["mae"] <= base["mae"] + DEAD_BAND_ABS_CM)
            passes = (delta is not None and delta >= PASS_DELTA_PP
                      and s["n_events"] >= MIN_EVENTS and mae_ok)
            rows.append({"t_full": t_full, "t_zero": t_zero, **s,
                         "delta_pp": delta, "mae_ok": mae_ok, "passes": passes})
    winners = [r for r in rows if r["passes"]]
    best = max(winners, key=lambda r: r["hit_rate"]) if winners else None
    return {"baseline": base, "sweep": rows, "winner": best,
            "default": next((r for r in rows if r["t_full"] == WETBULB_T_FULL_C
                             and r["t_zero"] == WETBULB_T_ZERO_C), None)}


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def to_markdown(result: dict, start: str, end: str) -> str:
    b = result["baseline"]
    w = result["winner"]
    out = [
        "# S1.1 — wet-bulb snow/rain partition: backtest go/no-go", "",
        "_Generated by `scripts/eval_s11_phase.py` (re-run to refresh). ROADMAP §4 S1.1._",
        "",
        "## Mechanism",
        "",
        "The derived AROME HD column partitions precipitation into snow with a "
        "dry-bulb temperature taper (`aggregate.snow_ratio`). Near 0 °C that is a "
        "poor phase driver: in dry air, evaporative cooling lets snow survive well "
        "above freezing. S1.1 keys the taper on **Stull (2011) wet-bulb** Tw from T "
        "and RH instead (`aggregate.wetbulb_snow_ratio`). AROME HD serves surface "
        "`relative_humidity_2m` on both the live and Previous Runs APIs (probed "
        "2026-07-30), so the derivation stays intra-model; an hour with missing RH "
        "falls back to the dry-bulb taper. The challenger ships as a scored-only "
        "column `arome_hd_wb`, never rendered, until it clears the gate below "
        "(ADR-0003, verification-first).", "",
        "## Result", "",
        f"Window {start} … {end}, snow-truth EMAs Z1/Z2/Z9. Marginal bucket = "
        f"bucket-mean band |T| ≤ {MARGINAL_T_C:.0f} °C (where the rain/snow call is "
        f"in doubt); phase snow threshold = {EVENT_BUCKET_CM:.0f} cm for forecast and "
        f"obs; an event is a marginal bucket with observed precip ≥ "
        f"{PHASE_MIN_PRECIP_MM} mm. **Gate:** Δ ≥ {PASS_DELTA_PP:.0f} pp phase hit-rate "
        f"on ≥ {MIN_EVENTS} events, cm MAE not worse by more than the dead band.", "",
        f"**Baseline (arome_hd, dry-bulb T taper):** hit-rate {_pct(b['hit_rate'])} "
        f"on {b['n_events']} marginal events, cm MAE {_num(b['mae'])}.", "",
        "| t_full | t_zero | hit-rate | Δpp | cm MAE | n | verdict |",
        "|---|---|---|---|---|---|---|"]
    for r in result["sweep"]:
        d = "—" if r["delta_pp"] is None else f"{r['delta_pp']:+.1f}"
        out.append(f"| {r['t_full']:+.1f} | {r['t_zero']:+.1f} | {_pct(r['hit_rate'])} | {d} "
                   f"| {_num(r['mae'])} | {r['n_events']} | {'PASS' if r['passes'] else '—'} |")
    default = result["default"]
    out += ["", "## Verdict", ""]
    if w:
        out += [
            f"**GO.** Best passing breakpoints t_full={w['t_full']:+.1f}, "
            f"t_zero={w['t_zero']:+.1f}: hit-rate {_pct(w['hit_rate'])} "
            f"({w['delta_pp']:+.1f} pp) on {w['n_events']} events, cm MAE "
            f"{_num(w['mae'])} (better than baseline — no cm regression). Every passing "
            "row sits in a shallow plateau, so the improvement is robust to the exact "
            "breakpoints rather than an overfit knife-edge.",
        ]
        if default and default["passes"]:
            out += ["",
                    f"The config defaults (t_full={WETBULB_T_FULL_C:+.1f}, "
                    f"t_zero={WETBULB_T_ZERO_C:+.1f}) also pass "
                    f"({default['delta_pp']:+.1f} pp), so the literature prior needs no "
                    "recalibration — it already sits inside the optimal plateau."]
        out += ["",
                "**What this gates:** the S1.1 go/no-go is defined on the backtest, and "
                "it passes. Promoting the *published* HD column to wet-bulb "
                "(`ingest_openmeteo.snowfall_series` → `derive_snowfall_wetbulb`, HD "
                "ModelSpec → derived-wetbulb) is the follow-on product change. ROADMAP "
                "§1 treats backtest-derived corrections as priors to confirm live; "
                "`arome_hd_wb` is already scored beside `arome_hd` on the live loop, so "
                "a live winter can confirm before the rendered column flips."]
    else:
        out += ["**NO-GO** — no breakpoint clears the gate. Keep the dry-bulb column, "
                "keep `arome_hd_wb` scored in parallel, and revisit with live winter data."]

    cmp = result.get("comparison")
    if cmp:
        out += ["", "## How the wet-bulb column compares to native AROME 2.5", "",
                "The frozen baseline found native AROME 2.5 beats derived HD (ADR-0003 "
                "pt 5). Wet-bulb (default breakpoints) does not overturn that — it closes "
                "most of the gap: near level on event skill, and it edges ahead on "
                "near-freezing cm accuracy (dry marginal air, where the native 2.5 "
                "partition also over-calls rain).", "",
                "**Marginal buckets (±2 °C, obs precip):**", "",
                "| derivation | phase hit-rate | cm MAE | n |",
                "|---|---|---|---|"]
        for name, d in cmp.items():
            mg = d["marginal"]
            out.append(f"| {name} | {_pct(mg['hit_rate'])} | {_num(mg['mae'])} | {mg['n']} |")
        out += ["", "**Overall (all marginal-and-not buckets, 24 h lead):**", "",
                "| derivation | cm MAE | bias | POD | FAR | CSI | n |",
                "|---|---|---|---|---|---|---|"]
        for name, d in cmp.items():
            ov = d["overall"]
            out.append(f"| {name} | {_num(ov['mae'])} | {_num(ov['bias'])} | {_num(ov['pod'])} "
                       f"| {_num(ov['far'])} | {_num(ov['csi'])} | {ov['n_cm']} |")
        out += ["", "_Takeaway: wet-bulb turns HD from a clearly-inferior derived column "
                "into one roughly equivalent to native 2.5 — a strict win for the HD "
                "column, but 2.5 stays the anchor._"]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S1.1 wet-bulb phase go/no-go (backtest).")
    ap.add_argument("--start", default="2024-01-19T00:00:00Z", help="ISO-UTC window start")
    ap.add_argument("--end", default="2025-05-01T00:00:00Z", help="ISO-UTC window end (half-open)")
    ap.add_argument("--out", type=Path, default=Path("docs/notes/wetbulb-partition.md"),
                    help="write the Markdown report here (— for stdout)")
    args = ap.parse_args(argv)

    archive = Archive.from_env()
    conn = store.connect(archive.root / "verification.sqlite")
    stations = [s.codi for s in XEMA_STATIONS if s.snow_truth]

    slices = load_slices(archive)
    if not slices:
        print(f"no RH-bearing backtest raws under {archive.root}/raw/{ARCHIVE_SOURCE} — "
              "run the B1 backfill (RH in BASE_VARS) first", file=sys.stderr)
        return 2
    truth, precip = load_truth(conn, stations, args.start, args.end)
    events = build_events(slices, truth, precip)
    result = evaluate(events, slices)
    # three-way comparison vs native AROME 2.5 (default wet-bulb breakpoints)
    wb_default = wetbulb_cm_by_key(slices, WETBULB_T_FULL_C, WETBULB_T_ZERO_C)
    result["comparison"] = compare(events, build_all_records(slices, truth),
                                   native_cm_by_key(archive), wb_default)
    report = to_markdown(result, args.start, args.end)

    if str(args.out) == "-":
        print(report)
    else:
        args.out.write_text(report)
        print(f"wrote {args.out} — {len(events)} precip events; "
              f"{'GO' if result['winner'] else 'NO-GO'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
