"""Orographic precipitation gradient (S1.3) — correction, fit and go/no-go gate.

Open-Meteo's `elevation` parameter downscales *temperature* to the band we ask
for, but precipitation is served as the grid cell holds it: `elevation` only
steers which cell is selected, so once the requested height is above every
neighbouring cell the same cell answers for every higher band and precipitation
stops responding to elevation at all. Measured over every committed run of
`data/openmeteo.json` (`scripts/detect_opg_saturation.py`, docs/notes/opg.md):
Baqueira's 2000 m and 2600 m bands are precipitation-identical in 10/10 wet runs
for AROME 2.5 and 11/11 for HD; La Molina's 2100/2500 do the same for HD. Above
that saturation height the model's own resolved elevation gradient (+2.6 %/100 m
median between its cells) simply stops — which is one concrete, measurable
source of the frozen baseline's systematic under-prediction (bias −0.73 cm/6 h).

The correction is one multiplicative factor per (point, model, elevation):

    factor(z) = 1 + OPG_PER_100M · (z − z_ref)/100 m,  capped, 1.0 at/below z_ref

applied to precipitation and, equivalently, to the snowfall column (the derived
ratio and the native partition are both linear in precipitation at fixed
temperature, so scaling either end gives the same cm — the site scales the
hourly series, the verification paths scale the 6 h bucket cm).

ADR-0003 governs: this ships DISABLED for the published page (`OPG_ENABLED`),
while the corrected column is written to the verification store beside the
uncorrected one as `fx.snowfall_cm.<column>_opg` and scored by the same
verify.py. `evaluate_gate` implements the roadmap's go/no-go — cm MAE on the
affected points −≥10 % on wet buckets, with no bias-sign flip — and
`fit_gradient` replaces the literature/model prior with a value fitted from
XEMA peak/valley gauge pairs, per ADR-0003 pt 4 (priors, not final constants).
"""

import argparse
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from minipirineu import config, store, verify
from minipirineu.archive import Archive
from minipirineu.config import (
    MODELS,
    OPG_GATE_MIN_MAE_GAIN,
    OPG_MAX_FACTOR,
    OPG_PER_100M,
    OPG_PROBED_STATION_ELEVATION_M,
    OPG_REFERENCE_ELEVATION_M,
    OPG_WET_BUCKET_CM,
    OPG_WET_BUCKET_MM,
    STATIONS,
    XEMA_STATIONS,
)
from minipirineu.truth_b import catch_efficiency, station_truth_b

COLUMN_SUFFIX = "_opg"
DIAG_REFERENCE = "derived.opg_reference_m"  # per-run detection, for drift history


# --- column naming ----------------------------------------------------------

def opg_column(column: str) -> str:
    """`arome_25` → `arome_25_opg`: the corrected column, scored side by side
    with the uncorrected one (never replacing it until the gate passes)."""
    return f"{column}{COLUMN_SUFFIX}"


def base_column(column: str) -> str | None:
    """Inverse of opg_column; None if `column` is not an OPG variant."""
    return column[: -len(COLUMN_SUFFIX)] if column.endswith(COLUMN_SUFFIX) else None


# --- reference (saturation) elevation ---------------------------------------

@dataclass(frozen=True)
class Reference:
    """Where a point's precipitation stops responding to elevation.

    `elevation_m is None` means no correction applies — either the point still
    resolves its own cell per band ("measured") or we have never measured it
    ("unknown"). The `source` is carried so a report can never present an
    inherited prior as a measurement.
    """

    elevation_m: int | None
    source: str  # "measured" | "inherited" | "unknown"


_RESORT_BY_XEMA = {x.codi: x.resort for x in XEMA_STATIONS}


def resolve_reference(point_id: str, model_id: str) -> Reference:
    """Saturation elevation for a forecast point (resort id or XEMA station code).

    Resort points were measured from the archived runs. XEMA verification points
    are single-elevation fetches, so nothing in the archive can reveal their own
    saturation height: until `scripts/probe_opg_saturation.py` fills
    `OPG_PROBED_STATION_ELEVATION_M`, they inherit their resort's value and are
    reported as "inherited" — a flagged prior, not a measurement.
    """
    key = (point_id, model_id)
    if key in OPG_PROBED_STATION_ELEVATION_M:
        return Reference(OPG_PROBED_STATION_ELEVATION_M[key], "measured")
    if key in OPG_REFERENCE_ELEVATION_M:
        return Reference(OPG_REFERENCE_ELEVATION_M[key], "measured")
    resort = _RESORT_BY_XEMA.get(point_id)
    if resort is not None and (resort, model_id) in OPG_REFERENCE_ELEVATION_M:
        return Reference(OPG_REFERENCE_ELEVATION_M[(resort, model_id)], "inherited")
    return Reference(None, "unknown")


def factor(
    elevation_m: float,
    reference_m: int | None,
    *,
    per_100m: float = OPG_PER_100M,
    max_factor: float = OPG_MAX_FACTOR,
) -> float:
    """Multiplicative precipitation enhancement at `elevation_m`.

    1.0 at or below the saturation elevation (where the model still resolves the
    gradient itself — correcting there would double-count), and 1.0 when there is
    no reference at all. Capped so a large extrapolation cannot fabricate snow.
    """
    if reference_m is None or elevation_m <= reference_m:
        return 1.0
    return min(1.0 + per_100m * (elevation_m - reference_m) / 100.0, max_factor)


def point_factor(point_id: str, model_id: str, elevation_m: float, **kw) -> float:
    """Factor for a (point, model, elevation), resolving the reference first."""
    return factor(elevation_m, resolve_reference(point_id, model_id).elevation_m, **kw)


# --- applying the correction ------------------------------------------------

def scale_series(values: Sequence[float | None], f: float) -> list:
    """Scale an hourly series, preserving None (missing stays missing)."""
    return [None if v is None else v * f for v in values]


def scale_buckets(buckets: Sequence[tuple[str, float]], f: float, ndigits: int = 1) -> list:
    """Scale bucketed (start, cm) pairs — the verification-side application.
    Equivalent to scaling the hourly precipitation: both the derived snow ratio
    and the native snow/rain partition are linear in precipitation at fixed
    temperature, and the temperature is untouched by an OPG correction."""
    return [(start, round(cm * f, ndigits)) for start, cm in buckets]


# --- detecting saturation from a multi-band run -----------------------------

@dataclass(frozen=True)
class Detection:
    reference_m: int | None
    decidable: bool  # False = the run was too dry to tell (all-zero bands match)


def detect_reference(
    precip_by_elevation: dict[int, Sequence[float | None]],
    min_total_mm: float = OPG_WET_BUCKET_MM * 4,
) -> Detection:
    """Saturation elevation implied by ONE run's per-band precipitation series.

    Two bands whose series are identical share a grid cell; the reference is the
    lowest elevation that is identical to the band above it. A pair whose lower
    band is dry proves nothing (all-zero series trivially match), so it is
    skipped — and a *negative* verdict ("no saturation") is only decidable when
    every pair was wet enough to check: otherwise the run simply never saw the
    band where saturation might start. Undecided is not "no saturation".
    """
    elevations = sorted(precip_by_elevation)
    checked = 0
    for lo, hi in zip(elevations, elevations[1:]):
        lo_series = list(precip_by_elevation[lo])
        if sum(v or 0.0 for v in lo_series) < min_total_mm:
            continue
        checked += 1
        if lo_series == list(precip_by_elevation[hi]):
            return Detection(lo, True)
    return Detection(None, checked == max(len(elevations) - 1, 0) and checked > 0)


def reference_drift(station_id: str, model_id: str, detection: Detection) -> str | None:
    """Alert text when a run's measured saturation contradicts the config table.

    Open-Meteo silently changing its grid or cell selection would invalidate the
    correction; this is the same drift-alert discipline the Meteocat anchors use.
    """
    if not detection.decidable:
        return None
    configured = resolve_reference(station_id, model_id)
    if configured.source == "measured" and configured.elevation_m == detection.reference_m:
        return None
    if configured.source == "measured":
        return (f"OPG reference drift at {station_id}/{model_id}: config says "
                f"{configured.elevation_m}, this run measures {detection.reference_m}")
    return (f"OPG reference unmeasured at {station_id}/{model_id}: this run "
            f"measures {detection.reference_m}")


def reference_rows(station_id: str, run_time_utc: str, detections: dict[str, Detection]) -> list:
    """Store the per-run detection so grid drift is visible in history.
    Undecidable runs write nothing (missing stays missing); "no saturation" is a
    real finding and is stored as a NULL-valued row, distinct from absent."""
    rows = []
    for model_id, det in detections.items():
        if not det.decidable:
            continue
        value = None if det.reference_m is None else float(det.reference_m)
        rows.append(store.Row("openmeteo", station_id, run_time_utc, run_time_utc,
                              f"{DIAG_REFERENCE}.{model_id}", value))
    return rows


# --- fitting the gradient from XEMA peak/valley pairs -----------------------

@dataclass(frozen=True)
class GaugePair:
    resort: str
    high: str
    valley: str
    dz_m: int


def gauge_pairs() -> list[GaugePair]:
    """Candidate (high, valley) XEMA station pairs, one per resort combination.

    Not every station carries a gauge (Z1 Bonaigua and Z2 Boí serve neither wind
    nor precipitation — docs/notes/xema-truth-stations.md), so a pair may simply
    have no data; `fit_gradient` reports that as n_buckets = 0 rather than
    inventing a gradient.
    """
    pairs = []
    for resort in sorted({x.resort for x in XEMA_STATIONS if x.resort}):
        highs = [x for x in XEMA_STATIONS if x.resort == resort and x.role == "high"]
        valleys = [x for x in XEMA_STATIONS if x.resort == resort and x.role == "valley"]
        for high in highs:
            for valley in valleys:
                pairs.append(GaugePair(resort, high.codi, valley.codi,
                                       high.altitude_m - valley.altitude_m))
    return pairs


@dataclass(frozen=True)
class GradientFit:
    pair: GaugePair
    n_buckets: int
    n_undercatch_corrected: int
    total_high_mm: float
    total_valley_mm: float
    per_100m_weighted: float | None  # from the totals (precipitation-weighted)
    per_100m_median: float | None    # median of the per-bucket estimates


def _corrected_precip(bucket) -> tuple[float, bool]:
    """A bucket's gauge precipitation, undercatch-corrected when wind and
    temperature are available. Without them the raw catch is used and the bucket
    is counted as uncorrected — the high, exposed station undercatches most, so
    an uncorrected fit is a LOWER bound on the true gradient."""
    if bucket.wind_mean_ms is None or bucket.temp_mean_c is None:
        return bucket.precip_mm, False
    return bucket.precip_mm / catch_efficiency(bucket.wind_mean_ms, bucket.temp_mean_c), True


def fit_gradient(
    conn,
    pair: GaugePair,
    start_utc: str,
    end_utc: str,
    *,
    min_bucket_mm: float = OPG_WET_BUCKET_MM,
    max_temp_c: float | None = None,
) -> GradientFit:
    """Fit a precipitation-elevation gradient from one high/valley gauge pair.

    Only 6 h buckets complete at BOTH stations and wet at the valley one are
    used; each contributes (P_high/P_valley − 1) per 100 m of separation. The
    weighted number (from the totals) is the headline — it is dominated by the
    big events the correction exists for; the median is the robustness check.
    `max_temp_c` restricts the fit to the cold regime the snow column cares
    about (e.g. 0.0 for sub-freezing buckets at the high station).
    """
    high = {b.bucket_start_utc: b for b in station_truth_b(conn, pair.high, start_utc, end_utc)}
    valley = {b.bucket_start_utc: b for b in station_truth_b(conn, pair.valley, start_utc, end_utc)}
    total_hi = total_lo = 0.0
    per_bucket: list[float] = []
    n_corrected = 0
    for start, hb in sorted(high.items()):
        vb = valley.get(start)
        if vb is None or hb.precip_mm is None or vb.precip_mm is None:
            continue
        if not (hb.complete and vb.complete) or vb.precip_mm < min_bucket_mm:
            continue
        if max_temp_c is not None and (hb.temp_mean_c is None or hb.temp_mean_c > max_temp_c):
            continue
        hi_mm, hi_ok = _corrected_precip(hb)
        lo_mm, lo_ok = _corrected_precip(vb)
        if lo_mm <= 0:
            continue
        total_hi += hi_mm
        total_lo += lo_mm
        per_bucket.append((hi_mm / lo_mm - 1.0) / (pair.dz_m / 100.0))
        n_corrected += int(hi_ok and lo_ok)
    weighted = (total_hi / total_lo - 1.0) / (pair.dz_m / 100.0) if total_lo > 0 else None
    return GradientFit(
        pair, len(per_bucket), n_corrected, total_hi, total_lo,
        weighted, statistics.median(per_bucket) if per_bucket else None,
    )


def fit_all(conn, start_utc: str, end_utc: str, **kw) -> list[GradientFit]:
    return [fit_gradient(conn, pair, start_utc, end_utc, **kw) for pair in gauge_pairs()]


# --- go/no-go gate (ROADMAP §4 S1.3) ----------------------------------------

def wet_pairs(pairs, min_cm: float = OPG_WET_BUCKET_CM) -> list:
    """Buckets with snow on either side — the ones the go/no-go is measured on.
    Dry buckets are scored identically by both columns (factor × 0 = 0) and would
    only dilute the MAE difference the threshold is set against."""
    return [p for p in pairs if p.truth_cm >= min_cm or p.forecast_cm >= min_cm]


@dataclass(frozen=True)
class GateResult:
    column: str
    stations: tuple[str, ...]
    n_wet: int
    base: dict
    opg: dict
    mae_gain: float | None      # fraction improved: (base − opg)/base
    passed: bool
    reasons: tuple[str, ...]


def evaluate_gate(
    pairs,
    *,
    min_mae_gain: float = OPG_GATE_MIN_MAE_GAIN,
    min_cm: float = OPG_WET_BUCKET_CM,
) -> dict:
    """Score every base column against its OPG variant on wet buckets.

    The roadmap's threshold: cm MAE on the AFFECTED points improves ≥10 %,
    "without flipping bias sign on the band below". Points where the factor is
    1.0 are untouched by construction (no variant rows are written for them), so
    the substantive risk is overshoot at the corrected points — the sign check is
    applied there: an under-predicting column that starts over-predicting has
    overshot, and the roadmap says revert.
    """
    by_column: dict[str, dict] = {}
    for p in pairs:
        by_column.setdefault(p.column, {})[(p.station, p.run_utc, p.valid_bucket_utc)] = p
    results = {}
    for column, items in sorted(by_column.items()):
        if base_column(column) is not None:
            continue  # this loop iterates base columns; variants are looked up
        variant = by_column.get(opg_column(column), {})
        # Apples to apples: the same buckets on both sides, at the points the
        # variant covers. Wetness is judged on EITHER column — the correction
        # can lift a bucket over the threshold, and scoring it on one side only
        # would compare two different samples.
        keys = sorted(k for k in variant if k in items
                      and (items[k].truth_cm >= min_cm
                           or items[k].forecast_cm >= min_cm
                           or variant[k].forecast_cm >= min_cm))
        base_items = [items[k] for k in keys]
        opg_items = [variant[k] for k in keys]
        stations = tuple(sorted({p.station for p in opg_items}))
        base_mx = verify.bucket_metrics(base_items)
        opg_mx = verify.bucket_metrics(opg_items)
        reasons: list[str] = []
        gain = None
        orphans = [k for k in variant if k not in items]
        if orphans:
            # a variant bucket with no plain counterpart means the two writers
            # disagree about run/bucket keys — the comparison would be bogus
            reasons.append(f"{len(orphans)} OPG buckets have no base-column counterpart")
        if not opg_items:
            reasons.append("no OPG-affected wet buckets in the window")
        elif base_mx["mae"] is None or opg_mx["mae"] is None or base_mx["mae"] == 0:
            reasons.append("no cm-scorable buckets (all phase_only or zero-MAE baseline)")
        else:
            gain = (base_mx["mae"] - opg_mx["mae"]) / base_mx["mae"]
            if gain < min_mae_gain:
                reasons.append(f"cm MAE gain {gain:+.1%} < required {min_mae_gain:.0%}")
            if base_mx["bias"] is not None and opg_mx["bias"] is not None \
                    and base_mx["bias"] < 0 <= opg_mx["bias"]:
                reasons.append(f"bias flipped sign ({base_mx['bias']:+.2f} → "
                               f"{opg_mx['bias']:+.2f} cm): overshoot")
        results[column] = GateResult(column, stations, len(opg_items), base_mx, opg_mx,
                                     gain, not reasons, tuple(reasons))
    return results


def _fmt(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def gate_markdown(results: dict) -> str:
    out = ["# S1.3 OPG go/no-go", "",
           f"threshold: cm MAE −≥{OPG_GATE_MIN_MAE_GAIN:.0%} on wet buckets "
           f"(≥{OPG_WET_BUCKET_CM} cm), no bias-sign flip", "",
           "| column | points | n wet | MAE base | MAE opg | gain | bias base | bias opg | verdict |",
           "|---|---|---|---|---|---|---|---|---|"]
    for column, r in sorted(results.items()):
        out.append(
            f"| {column} | {', '.join(r.stations) or '—'} | {r.n_wet} | "
            f"{_fmt(r.base['mae'])} | {_fmt(r.opg['mae'])} | "
            f"{f'{r.mae_gain:+.1%}' if r.mae_gain is not None else '—'} | "
            f"{_fmt(r.base['bias'])} | {_fmt(r.opg['bias'])} | "
            f"{'PASS' if r.passed else 'no-go'} |")
    out.append("")
    for column, r in sorted(results.items()):
        for reason in r.reasons:
            out.append(f"- **{column}**: {reason}")
    return "\n".join(out)


# --- CLI --------------------------------------------------------------------

def _factors_table() -> str:
    lines = [f"OPG_ENABLED={config.OPG_ENABLED}  gradient={OPG_PER_100M:+.1%}/100 m  "
             f"cap=×{OPG_MAX_FACTOR}", ""]
    for station in STATIONS:
        for spec in MODELS:
            ref = resolve_reference(station.id, spec.id)
            factors = " ".join(
                f"{elev}m×{point_factor(station.id, spec.id, elev):.3f}"
                for _band, elev in station.bands)
            lines.append(f"{station.id:11s} {spec.column:9s} ref="
                         f"{str(ref.elevation_m):>5s} ({ref.source:9s}) {factors}")
    lines.append("")
    for x in XEMA_STATIONS:
        if not x.snow_truth:
            continue
        for spec in MODELS:
            ref = resolve_reference(x.codi, spec.id)
            lines.append(f"{x.codi + ' ' + x.name:26.26s} {spec.column:9s} ref="
                         f"{str(ref.elevation_m):>5s} ({ref.source:9s}) "
                         f"{x.altitude_m}m×{point_factor(x.codi, spec.id, x.altitude_m):.3f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m minipirineu.opg",
        description="Orographic precipitation gradient (S1.3): factors, fit and gate.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("factors", help="print the correction factors currently in force")
    fit = sub.add_parser("fit", help="fit the gradient from XEMA peak/valley gauge pairs")
    fit.add_argument("start", help="ISO-UTC window start, e.g. 2024-11-01T00:00:00Z")
    fit.add_argument("end", help="ISO-UTC window end (half-open)")
    fit.add_argument("--max-temp-c", type=float, default=None,
                     help="restrict to buckets at/below this high-station temperature")
    gate = sub.add_parser("gate", help="score the OPG columns against the base ones")
    gate.add_argument("start", help="ISO-UTC window start")
    gate.add_argument("end", help="ISO-UTC window end (half-open)")
    gate.add_argument("--out-md", type=Path, help="write the verdict here")
    args = ap.parse_args(argv)

    if args.cmd == "factors":
        print(_factors_table())
        return 0

    conn = store.connect(Archive.from_env().root / "verification.sqlite")
    if args.cmd == "fit":
        print(f"{'pair':22s} {'Δz':>6s} {'n':>5s} {'corr':>5s} {'Σhigh':>9s} "
              f"{'Σvalley':>9s} {'%/100m w':>9s} {'%/100m med':>11s}")
        for f in fit_all(conn, args.start, args.end, max_temp_c=args.max_temp_c):
            label = f"{f.pair.resort} {f.pair.high}/{f.pair.valley}"
            print(f"{label:22.22s} {f.pair.dz_m:6d} {f.n_buckets:5d} "
                  f"{f.n_undercatch_corrected:5d} {f.total_high_mm:9.1f} "
                  f"{f.total_valley_mm:9.1f} "
                  f"{(f'{f.per_100m_weighted * 100:+.2f}' if f.per_100m_weighted is not None else '—'):>9s} "
                  f"{(f'{f.per_100m_median * 100:+.2f}' if f.per_100m_median is not None else '—'):>11s}")
        return 0

    stations = [x.codi for x in XEMA_STATIONS if x.snow_truth]
    results = evaluate_gate(verify.build_pairs(conn, stations, args.start, args.end))
    text = gate_markdown(results)
    if args.out_md:
        args.out_md.write_text(text)
        print(f"wrote {args.out_md}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
