"""truth-B + quality gates + merged truth (S0.4b/T7).

truth-A (truth.py) reads fresh snow from the ultrasonic snow-depth sensor. This
module builds an *independent* estimate from the heated precipitation gauge
(var 35): correct the gauge's wind undercatch (Kochendorfer et al. 2017 /
WMO-SPICE, using 10 m wind var 30), split solid vs liquid, and divide the solid
water-equivalent by a fresh-snow density (Hedstrom & Pomeroy 1998, anchored on
Helfricht et al. 2018's 68 ± 9 kg/m³). Then A and B are merged under quality
gates: a windy bucket is excluded (blowing snow corrupts both sensors), a
melt/rain-on-snow bucket is flagged phase-only, and a wild A/B disagreement is
excluded. Design + literature anchors: docs/adr/0004-truth-pipeline.md.

Everything is a pure function over series or the verification store, so the same
code runs on backtest and live pairs (ADR-0003). Missing stays missing: an
uncoverable bucket is None/excluded, never a fabricated 0.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from minipirineu.config import (
    BUCKET_HOURS,
    FRESH_SNOW_DENSITY_D0,
    FRESH_SNOW_DENSITY_D1,
    FRESH_SNOW_DENSITY_D2,
    FRESH_SNOW_DENSITY_MAX,
    FRESH_SNOW_DENSITY_MIN,
    GATE_AB_ABS_CM,
    GATE_AB_FRAC,
    GATE_MELT_T_C,
    GATE_WIND_MEAN_MS,
    TRUTH_MAX_STEP_MIN,
    TRUTHB_RAIN_ALL_T_C,
    TRUTHB_SNOW_ALL_T_C,
    UNDERCATCH_A,
    UNDERCATCH_B,
    UNDERCATCH_C,
    UNDERCATCH_CE_FLOOR,
    UNDERCATCH_WIND_CAP_MS,
    WIND_10M_TO_GAUGE,
)
from minipirineu.truth import (
    SOURCE,
    TruthBucket,
    _bucket_start,
    _fmt,
    despike,
    parse_stamp,
    station_truth_a,
)

PRECIP_SLUG = "obs.precipitacio"
WIND_SLUG = "obs.vent_velocitat"
GUST_SLUG = "obs.vent_ratxa"
TEMP_SLUG = "obs.temperatura"
SNOW_DEPTH_SLUG = "obs.gruix_neu"


# --- physics ----------------------------------------------------------------

def catch_efficiency(
    wind_ms: float,
    temp_c: float,
    *,
    a: float = UNDERCATCH_A,
    b: float = UNDERCATCH_B,
    c: float = UNDERCATCH_C,
    floor: float = UNDERCATCH_CE_FLOOR,
    wind_cap: float = UNDERCATCH_WIND_CAP_MS,
    wind_factor: float = WIND_10M_TO_GAUGE,
) -> float:
    """Kochendorfer et al. (2017) unshielded catch efficiency in [floor, 1].

    `wind_ms` is 10 m wind; it is reduced to gauge height and capped to the SPICE
    fit range before the transfer function. CE=1 at no wind; colder/windier lower
    it. Adjusted precip = gauge / CE.
    """
    u = min(max(wind_ms, 0.0), wind_cap) * wind_factor
    ce = math.exp(-a * u * (1.0 - math.atan(b * temp_c) + c))
    return max(min(ce, 1.0), floor)


def new_snow_density(
    temp_c: float,
    *,
    d0: float = FRESH_SNOW_DENSITY_D0,
    d1: float = FRESH_SNOW_DENSITY_D1,
    d2: float = FRESH_SNOW_DENSITY_D2,
    lo: float = FRESH_SNOW_DENSITY_MIN,
    hi: float = FRESH_SNOW_DENSITY_MAX,
) -> float:
    """Fresh-snow density ρ(T)=D0+D1·exp(T/D2) [kg/m³] (Hedstrom & Pomeroy 1998),
    clamped to a physical range. Cold asymptote D0≈68 = Helfricht's mean."""
    return min(max(d0 + d1 * math.exp(temp_c / d2), lo), hi)


def snow_fraction(
    temp_c: float,
    *,
    t_snow: float = TRUTHB_SNOW_ALL_T_C,
    t_rain: float = TRUTHB_RAIN_ALL_T_C,
) -> float:
    """Solid fraction of precip: 1 at/below t_snow, 0 at/above t_rain, linear
    between. A coarse split for the gauge truth only (fine phase is Stage 1)."""
    if temp_c <= t_snow:
        return 1.0
    if temp_c >= t_rain:
        return 0.0
    return (t_rain - temp_c) / (t_rain - t_snow)


def gauge_fresh_snow_cm(precip_mm: float, wind_ms: float, temp_c: float, **kw) -> float:
    """Gauge precip (mm) → fresh-snow depth (cm): undercatch-correct, keep the
    solid fraction, divide by fresh-snow density. ≥0 by construction."""
    swe = precip_mm / catch_efficiency(wind_ms, temp_c) * snow_fraction(temp_c)
    return swe * 100.0 / new_snow_density(temp_c, **kw)


# --- per-bucket gauge features ----------------------------------------------

@dataclass(frozen=True)
class BucketB:
    bucket_start_utc: str
    fresh_snow_cm: float | None  # truth-B; None when gauge/wind/temp incomplete
    precip_mm: float | None      # raw gauge sum (pre-undercatch), for diagnostics
    wind_mean_ms: float | None
    gust_max_ms: float | None
    temp_mean_c: float | None
    delta_hs_cm: float | None    # snow-depth change across the bucket (melt gate)
    complete: bool               # timeline covers the bucket edge to edge


def _covers(times: list[datetime], start: datetime, end: datetime, step_max_s: float) -> bool:
    """True iff sorted reading times tile [start, end): first at start, no gap
    over step_max, last within one step of the end (forward-labeled coverage)."""
    if not times or times[0] != start:
        return False
    for prev, cur in zip(times, times[1:]):
        if (cur - prev).total_seconds() > step_max_s:
            return False
    return (end - times[-1]).total_seconds() <= step_max_s


def _mean(vals: list[float | None]) -> float | None:
    present = [v for v in vals if v is not None]
    return statistics.fmean(present) if present else None


def _max(vals: list[float | None]) -> float | None:
    present = [v for v in vals if v is not None]
    return max(present) if present else None


def _precip_sum(vals: list[float | None], time_complete: bool) -> float | None:
    """Bucket precip total, but only when every step is present: a missing gauge
    reading is unknown precip, so the bucket total is None, never a partial sum."""
    if not time_complete or any(v is None for v in vals):
        return None
    return sum(vals)


def _delta_hs(depths: list[float | None]) -> float | None:
    present = [d for d in depths if d is not None]
    return present[-1] - present[0] if len(present) >= 2 else None


def compute_truth_b(
    times: list[str],
    precip_mm: list[float | None],
    wind_ms: list[float | None],
    gust_ms: list[float | None],
    temp_c: list[float | None],
    gruix_cm: list[float | None],
    *,
    step_max_min: float = TRUTH_MAX_STEP_MIN,
    bucket_hours: int = BUCKET_HOURS,
) -> list[BucketB]:
    """Gauge-based fresh-snow truth per 6h UTC bucket from a station's series.

    All lists are aligned to one 30-min timeline (`times` ISO-UTC, forward-
    labeled). truth-B is computed only where gauge, wind and temperature are all
    available for a fully covered bucket; otherwise fresh_snow_cm is None.
    """
    step_max_s = step_max_min * 60
    depths = despike(list(gruix_cm))
    stamps = [parse_stamp(t) for t in times]
    groups: dict[datetime, list[int]] = {}
    for i, dt in enumerate(stamps):
        groups.setdefault(_bucket_start(dt, bucket_hours), []).append(i)

    out: list[BucketB] = []
    for b in sorted(groups):
        idx = groups[b]
        bt = [stamps[i] for i in idx]
        complete = _covers(bt, b, b + timedelta(hours=bucket_hours), step_max_s)
        precip = _precip_sum([precip_mm[i] for i in idx], complete)
        wind = _mean([wind_ms[i] for i in idx])
        temp = _mean([temp_c[i] for i in idx])
        gust = _max([gust_ms[i] for i in idx])
        dhs = _delta_hs([depths[i] for i in idx])
        fresh = (
            round(gauge_fresh_snow_cm(precip, wind, temp), 1)
            if precip is not None and wind is not None and temp is not None
            else None
        )
        out.append(BucketB(_fmt(b), fresh, precip, wind, gust, temp, dhs, complete))
    return out


# --- A/B gates + merge ------------------------------------------------------

@dataclass(frozen=True)
class MergedTruth:
    bucket_start_utc: str
    truth_cm: float | None       # merged fresh-snow truth; None if excluded/missing
    method: str                  # "A+B" | "A" | "B" | "none"
    flags: tuple[str, ...]       # e.g. ("phase_only",), ("unconfirmed",)
    excluded: str | None         # None | "wind" | "ab_divergence" | "incomplete"


def _melt_signature(f: BucketB) -> bool:
    """Air above freezing, pack losing depth, gauge still catching → melt /
    rain-on-snow: truth-A reads ~0 while the gauge accumulates, so cm is only a
    lower bound and the bucket is phase-scorable, not a clean cm truth."""
    return (
        f.temp_mean_c is not None and f.temp_mean_c > GATE_MELT_T_C
        and f.delta_hs_cm is not None and f.delta_hs_cm < 0
        and f.precip_mm is not None and f.precip_mm > 0
    )


def _merge_bucket(a: float | None, f: BucketB) -> MergedTruth:
    """One bucket's merge decision. `a` is truth-A cm (None if incomplete)."""
    start = f.bucket_start_utc
    b = f.fresh_snow_cm
    # Wind gate first: sustained wind redistributes snow, corrupting both the
    # sonde ΔHS and the gauge catch — the whole bucket is untrustworthy.
    if f.wind_mean_ms is not None and f.wind_mean_ms > GATE_WIND_MEAN_MS:
        return MergedTruth(start, None, "none", (), "wind")
    # Melt / rain-on-snow: A and B are *expected* to disagree, so skip the
    # divergence gate and flag it phase-only (cm a lower bound).
    if _melt_signature(f):
        cm = a if a is not None else b
        method = "A" if a is not None else ("B" if b is not None else "none")
        return MergedTruth(start, cm, method, ("phase_only",), None)
    if a is None:
        # No snow-depth truth: fall back to the gauge alone if it exists.
        if b is None:
            return MergedTruth(start, None, "none", (), "incomplete")
        return MergedTruth(start, b, "B", ("gauge_only",), None)
    if b is None:
        # No gauge/wind (e.g. Z1 reports no wind): truth-A stands, unconfirmed.
        return MergedTruth(start, a, "A", ("unconfirmed",), None)
    # Both present: cross-check. A wild disagreement excludes the bucket.
    if abs(a - b) > max(GATE_AB_ABS_CM, GATE_AB_FRAC * max(a, b)):
        return MergedTruth(start, None, "none", (), "ab_divergence")
    return MergedTruth(start, a, "A+B", (), None)


def merge_truth(a_buckets: list[TruthBucket], b_buckets: list[BucketB]) -> list[MergedTruth]:
    """Merge truth-A and truth-B buckets under the quality gates (ADR-0004).

    truth-A supplies the reported cm (the direct snow measurement); truth-B and
    the wind fields gate it. Buckets present in either input are emitted, in
    time order.
    """
    a_by = {t.bucket_start_utc: (t.fresh_snow_cm if t.complete else None) for t in a_buckets}
    b_by = {f.bucket_start_utc: f for f in b_buckets}
    out = []
    for start in sorted(set(a_by) | set(b_by)):
        # A truth-A bucket with no gauge series at all merges as unconfirmed.
        f = b_by.get(start) or BucketB(start, None, None, None, None, None, None, False)
        out.append(_merge_bucket(a_by.get(start), f))
    return out


def exclusion_stats(merged: list[MergedTruth]) -> dict[str, int]:
    """Sanity report: bucket counts by disposition, for the % excluded summary."""
    stats = {"total": len(merged), "usable": 0, "excluded": 0, "phase_only": 0}
    for m in merged:
        if m.excluded:
            stats["excluded"] += 1
            stats[m.excluded] = stats.get(m.excluded, 0) + 1
        elif m.truth_cm is not None:
            stats["usable"] += 1
        if "phase_only" in m.flags:
            stats["phase_only"] += 1
    return stats


# --- verification-store facing ----------------------------------------------

def load_gauge_series(conn, station: str, start_utc: str, end_utc: str):
    """Pivot a station's gauge/wind/gust/temp/depth obs onto one 30-min
    timeline over [start, end). ISO `...Z` stamps sort chronologically."""
    slugs = (PRECIP_SLUG, WIND_SLUG, GUST_SLUG, TEMP_SLUG, SNOW_DEPTH_SLUG)
    rows = conn.execute(
        f"""SELECT valid_time_utc, variable, value FROM verification_values
            WHERE source=? AND station=? AND variable IN ({','.join('?' * len(slugs))})
              AND valid_time_utc >= ? AND valid_time_utc < ?
            ORDER BY valid_time_utc""",
        (SOURCE, station, *slugs, start_utc, end_utc),
    ).fetchall()
    by_time: dict[str, dict] = {}
    for valid, variable, value in rows:
        by_time.setdefault(valid, {})[variable] = value
    times = sorted(by_time)
    cols = {slug: [by_time[t].get(slug) for t in times] for slug in slugs}
    return times, cols


def station_truth_b(conn, station: str, start_utc: str, end_utc: str, **kwargs) -> list[BucketB]:
    """truth-B buckets for one station straight from the verification store."""
    times, cols = load_gauge_series(conn, station, start_utc, end_utc)
    return compute_truth_b(
        times, cols[PRECIP_SLUG], cols[WIND_SLUG], cols[GUST_SLUG],
        cols[TEMP_SLUG], cols[SNOW_DEPTH_SLUG], **kwargs
    )


def station_merged_truth(conn, station: str, start_utc: str, end_utc: str, **kwargs) -> list[MergedTruth]:
    """Merged, gated fresh-snow truth for one station from the store — the truth
    that verify.py (T8) scores against."""
    a = station_truth_a(conn, station, start_utc, end_utc, **kwargs)
    b = station_truth_b(conn, station, start_utc, end_utc)
    return merge_truth(a, b)
