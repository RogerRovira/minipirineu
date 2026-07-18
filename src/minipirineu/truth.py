"""truth-A: fresh snow (cm) from XEMA snow-depth increments (S0.4a/T6).

Snow depth on the ground (var 38, `obs.gruix_neu`) is NOT new snow: between
snowfalls the pack settles, so raw depth increases underestimate what fell. This
recovers per-6h-bucket fresh snow by despiking and smoothing the 30-min series,
adding back a two-layer Anderson-style settling correction *during accumulation*,
and summing positive increments into UTC buckets. Design + literature anchors (Anderson
1976, Helfricht et al. 2018) are in docs/adr/0004-truth-pipeline.md.

Everything here is a pure function over series or the verification store, so it
runs with the same code on backtest and live pairs (ADR-0003). Missing stays
missing: an incomplete bucket is None, never a fabricated 0.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from minipirineu.config import (
    BUCKET_HOURS,
    HAMPEL_MIN_SCALE_CM,
    HAMPEL_N_SIGMAS,
    HAMPEL_WINDOW,
    NEW_SNOW_AGE_H,
    SETTLE_DEFAULT_T_C,
    SETTLING_C3_PER_S,
    SETTLING_C4_PER_C,
    TRUTH_MAX_STEP_MIN,
    TRUTH_SMOOTH_WINDOW,
    XEMA_SNOW_DEPTH_VAR,
    XEMA_VARIABLES,
)

SOURCE = "xema"
SNOW_DEPTH_SLUG = f"obs.{XEMA_VARIABLES[XEMA_SNOW_DEPTH_VAR]}"  # obs.gruix_neu
TEMP_SLUG = "obs.temperatura"
_MAD_TO_SIGMA = 1.4826  # scales MAD to a normal-consistent std estimate


@dataclass(frozen=True)
class TruthBucket:
    bucket_start_utc: str        # "2024-02-01T06:00:00Z", 6h-aligned in UTC
    fresh_snow_cm: float | None  # None when the bucket isn't fully covered
    n_steps: int                 # 30-min increments assigned to the bucket
    complete: bool               # readings span the whole bucket, no big gaps


def parse_stamp(stamp: str) -> datetime:
    """`...Z` (or offset) ISO string → timezone-aware UTC datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- despike (Hampel) -------------------------------------------------------

def despike(
    depths: list[float | None],
    window: int = HAMPEL_WINDOW,
    n_sigmas: float = HAMPEL_N_SIGMAS,
    min_scale: float = HAMPEL_MIN_SCALE_CM,
) -> list[float | None]:
    """Replace one-sample outliers with their centered-window median.

    A sustained snowfall step is signal (the window median moves with it) and
    survives; an isolated spike deviates from a window still dominated by the
    baseline. The `min_scale` floor keeps a flat window (MAD=0) from either
    dividing by zero or flagging normal jitter. None gaps pass through.
    """
    n = len(depths)
    half = window // 2
    out = list(depths)
    for i, x in enumerate(depths):
        if x is None:
            continue
        w = [v for v in depths[max(0, i - half):i + half + 1] if v is not None]
        if len(w) < 2:
            continue
        med = statistics.median(w)
        scale = max(_MAD_TO_SIGMA * statistics.median([abs(v - med) for v in w]), min_scale)
        if abs(x - med) > n_sigmas * scale:
            out[i] = med
    return out


def smooth(depths: list[float | None], window: int = TRUTH_SMOOTH_WINDOW) -> list[float | None]:
    """Centered moving average over non-None values (Helfricht et al. 2018).

    Damps the 30-min ultrasonic jitter that would otherwise be summed as
    fabricated snow. window=1 is a no-op; None gaps pass through unaveraged.
    """
    if window <= 1:
        return list(depths)
    half = window // 2
    out: list[float | None] = []
    for i, x in enumerate(depths):
        if x is None:
            out.append(None)
            continue
        w = [v for v in depths[max(0, i - half):i + half + 1] if v is not None]
        out.append(sum(w) / len(w))
    return out


# --- settling ---------------------------------------------------------------

def _step_fraction(temp_c: float | None, step_s: float, c3: float, c4: float, t_default: float) -> float:
    """Fraction of a young layer that settles over one step. Anderson (1976)
    destructive metamorphism S = c3·exp(c4·T) integrated over the step; snow
    temperature is the air temp capped at 0 °C, or a default when absent."""
    t_snow = min(temp_c, 0.0) if temp_c is not None else t_default
    return 1.0 - math.exp(-c3 * math.exp(c4 * t_snow) * step_s)


def settling_rate_per_step(t_snow_c: float, step_seconds: float) -> float:
    """Public settling fraction for a step at the configured coefficients."""
    return _step_fraction(t_snow_c, step_seconds, SETTLING_C3_PER_S, SETTLING_C4_PER_C, SETTLE_DEFAULT_T_C)


def _age_and_retire(young: list[list[float]], dt_s: float, new_age_s: float) -> None:
    """Age each young layer; once past NEW_SNOW_AGE it joins the (non-settling)
    old pack — modeled simply by dropping it from the settling set."""
    for layer in young:
        layer[1] += dt_s
    young[:] = [layer for layer in young if layer[1] <= new_age_s]


def _reduce_from_top(young: list[list[float]], amount: float) -> None:
    """Remove `amount` cm from the youngest snow down (melt/erosion/settling on a
    non-accumulating step), so sum(young) tracks observed depth. Never negative."""
    amount = max(0.0, amount)
    for layer in reversed(young):
        take = min(layer[0], amount)
        layer[0] -= take
        amount -= take
        if amount <= 1e-12:
            break
    young[:] = [layer for layer in young if layer[0] > 1e-9]


def _increments(times, depths, temps, *, step_max_s, new_age_s, c3, c4, t_default):
    """Walk (time, despiked depth, temp) → per-step tuples
    (start_dt, end_dt, hn_cm_or_None, gap_ok). hn is settling-corrected new snow
    (≥0); the add-back applies only on accumulating steps (ΔHS>0), matching
    Helfricht's "compaction correction during snowfall"."""
    incs = []
    young: list[list[float]] = []  # [[thickness_cm, age_s], ...], youngest last
    prev_t = prev_h = None
    for t, h, temp in zip(times, depths, temps, strict=True):
        if h is None:                        # a missing reading breaks continuity
            prev_t = prev_h = None
            young = []
            continue
        if prev_h is None:
            prev_t, prev_h = t, h            # seed; no increment yet
            continue
        dt_s = (t - prev_t).total_seconds()
        gap_ok = 0 < dt_s <= step_max_s
        _age_and_retire(young, dt_s, new_age_s)
        delta = h - prev_h
        if gap_ok and delta > 0:
            r = _step_fraction(temp, dt_s, c3, c4, t_default)
            settle = sum(layer[0] for layer in young) * r
            for layer in young:
                layer[0] *= (1.0 - r)
            hn = delta + settle
            young.append([hn, 0.0])
            incs.append((prev_t, t, hn, True))
        elif gap_ok:
            _reduce_from_top(young, prev_h - h)
            incs.append((prev_t, t, 0.0, True))
        else:                                # gap: accumulation unknown, reset
            young = []
            incs.append((prev_t, t, None, False))
        prev_t, prev_h = t, h
    return incs


# --- bucketing --------------------------------------------------------------

def _bucket_start(dt: datetime, hours: int) -> datetime:
    return dt.replace(hour=dt.hour - dt.hour % hours, minute=0, second=0, microsecond=0)


def _covers_bucket(items, start: datetime, end: datetime) -> bool:
    """True iff the increments assigned to a bucket span it edge to edge with no
    oversized gap: a reading at the start, contiguous steps, a reading at/after
    the end. A partly-covered edge bucket is therefore incomplete (→ None)."""
    goods = sorted(items)
    if not goods or any(not ok for *_, ok in goods):
        return False
    if goods[0][0] != start:
        return False
    cursor = start
    for st, en, _hn, _ok in goods:
        if st != cursor:
            return False
        cursor = en
    return cursor >= end


def _bucketize(incs, hours: int) -> list[TruthBucket]:
    groups: dict[datetime, list] = {}
    for start_dt, end_dt, hn, ok in incs:
        groups.setdefault(_bucket_start(start_dt, hours), []).append((start_dt, end_dt, hn, ok))
    buckets = []
    for b in sorted(groups):
        items = groups[b]
        complete = _covers_bucket(items, b, b + timedelta(hours=hours))
        fresh = round(sum(hn for *_, hn, ok in items if ok and hn is not None), 1) if complete else None
        buckets.append(TruthBucket(_fmt(b), fresh, len(items), complete))
    return buckets


def compute_truth_a(
    times: list[str],
    gruix_cm: list[float | None],
    temp_c: list[float | None] | None = None,
    *,
    window: int = HAMPEL_WINDOW,
    n_sigmas: float = HAMPEL_N_SIGMAS,
    min_scale: float = HAMPEL_MIN_SCALE_CM,
    smooth_window: int = TRUTH_SMOOTH_WINDOW,
    new_age_h: float = NEW_SNOW_AGE_H,
    c3: float = SETTLING_C3_PER_S,
    c4: float = SETTLING_C4_PER_C,
    t_default: float = SETTLE_DEFAULT_T_C,
    step_max_min: float = TRUTH_MAX_STEP_MIN,
    bucket_hours: int = BUCKET_HOURS,
) -> list[TruthBucket]:
    """Fresh-snow truth per 6h UTC bucket from a station's HS series.

    `times` are ISO-UTC reading stamps (forward-labeled: 11:00 covers
    11:00–11:30), `gruix_cm` the snow depth, `temp_c` the air temperature for the
    settling temperature dependence (optional). Series need not be gap-free.
    """
    depths = smooth(despike(list(gruix_cm), window, n_sigmas, min_scale), smooth_window)
    temps = list(temp_c) if temp_c is not None else [None] * len(depths)
    stamps = [parse_stamp(t) for t in times]
    incs = _increments(
        stamps, depths, temps,
        step_max_s=step_max_min * 60, new_age_s=new_age_h * 3600,
        c3=c3, c4=c4, t_default=t_default,
    )
    return _bucketize(incs, bucket_hours)


# --- verification-store facing ----------------------------------------------

def load_snow_series(conn, station: str, start_utc: str, end_utc: str):
    """Read a station's snow-depth and temperature obs from the store over
    [start, end), pivoted onto one 30-min timeline. ISO `...Z` stamps sort
    lexically as they do chronologically, so the range filter is a string range."""
    rows = conn.execute(
        """SELECT valid_time_utc, variable, value FROM verification_values
           WHERE source=? AND station=? AND variable IN (?, ?)
             AND valid_time_utc >= ? AND valid_time_utc < ?
           ORDER BY valid_time_utc""",
        (SOURCE, station, SNOW_DEPTH_SLUG, TEMP_SLUG, start_utc, end_utc),
    ).fetchall()
    by_time: dict[str, dict] = {}
    for valid, variable, value in rows:
        by_time.setdefault(valid, {})[variable] = value
    times = sorted(by_time)
    gruix = [by_time[t].get(SNOW_DEPTH_SLUG) for t in times]
    temp = [by_time[t].get(TEMP_SLUG) for t in times]
    return times, gruix, temp


def station_truth_a(conn, station: str, start_utc: str, end_utc: str, **kwargs) -> list[TruthBucket]:
    """truth-A buckets for one station straight from the verification store."""
    times, gruix, temp = load_snow_series(conn, station, start_utc, end_utc)
    return compute_truth_a(times, gruix, temp, **kwargs)
