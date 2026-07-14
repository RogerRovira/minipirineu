"""Derive and aggregate hourly model series into the 6h intervals of the table."""

from datetime import datetime, timedelta

from minipirineu.config import (
    BUCKET_HOURS,
    DERIVED_SNOW_RATIO_MAX,
    DERIVED_SNOW_T_FULL_C,
    DERIVED_SNOW_T_ZERO_C,
    FORECAST_HOURS,
)


def snow_ratio(temperature_c: float) -> float:
    """cm of snow per mm of precipitation water at a given band temperature.

    Piecewise linear fit against AROME 2.5's own snowfall/precipitation
    partition (docs/notes/snowfall-semantics.md): full ratio in the cold,
    tapering to zero across the mixed rain/snow range near freezing.
    """
    if temperature_c <= DERIVED_SNOW_T_FULL_C:
        return DERIVED_SNOW_RATIO_MAX
    if temperature_c >= DERIVED_SNOW_T_ZERO_C:
        return 0.0
    return (
        DERIVED_SNOW_RATIO_MAX
        * (DERIVED_SNOW_T_ZERO_C - temperature_c)
        / (DERIVED_SNOW_T_ZERO_C - DERIVED_SNOW_T_FULL_C)
    )


def derive_snowfall(precipitation_mm: list, temperature_c: list) -> list:
    """Estimate hourly snowfall (cm) for models without a native snowfall output.

    Intra-model derivation, not an inter-model blend: each hour converts its
    own precipitation using the temperature-dependent ratio above. Hours where
    either input is missing stay None (unknown, not zero).
    """
    derived = []
    for precip, temp in zip(precipitation_mm, temperature_c, strict=True):
        if precip is None or temp is None:
            derived.append(None)
        else:
            derived.append(precip * snow_ratio(temp))
    return derived


def floor_to_bucket(dt: datetime) -> datetime:
    """Floor a local datetime to its containing 6h block (00/06/12/18)."""
    return dt.replace(hour=dt.hour - dt.hour % BUCKET_HOURS, minute=0, second=0, microsecond=0)


def to_buckets(
    times: list[str],
    snowfall_cm: list,
    precipitation_mm: list,
    temperature_c: list,
    now: datetime,
) -> dict:
    """Aggregate hourly series into up to 8 six-hour intervals covering now → +48h.

    Buckets align to local 00/06/12/18 blocks; the first is the block
    containing `now`, so its earliest hours are the model's take on the
    recent past. Snowfall and precipitation are summed, temperature averaged,
    over the hours with real values. Trailing all-null buckets (beyond the
    model's horizon) are dropped rather than rendered as 0, so missing data
    can never pass for "no snow"; a null gap *inside* the horizon is
    unexpected and raises.
    """
    if now.tzinfo is not None:
        raise ValueError("now must be naive local time (Europe/Madrid), like the API series")
    parsed = [datetime.fromisoformat(t) for t in times]
    start = floor_to_bucket(now)
    n_buckets = FORECAST_HOURS // BUCKET_HOURS

    intervals: list[dict | None] = []
    for i in range(n_buckets):
        b_start = start + timedelta(hours=i * BUCKET_HOURS)
        b_end = b_start + timedelta(hours=BUCKET_HOURS)
        idx = [j for j, t in enumerate(parsed) if b_start <= t < b_end]
        snow = [snowfall_cm[j] for j in idx if snowfall_cm[j] is not None]
        precip = [precipitation_mm[j] for j in idx if precipitation_mm[j] is not None]
        temp = [temperature_c[j] for j in idx if temperature_c[j] is not None]
        if not snow and not precip and not temp:
            intervals.append(None)
            continue
        intervals.append(
            {
                "start": b_start.isoformat(timespec="minutes"),
                "end": b_end.isoformat(timespec="minutes"),
                "snowfall_cm": round(sum(snow), 1) if snow else None,
                "precipitation_mm": round(sum(precip), 1) if precip else None,
                "temperature_c": round(sum(temp) / len(temp), 1) if temp else None,
            }
        )

    while intervals and intervals[-1] is None:
        intervals.pop()
    if any(interval is None for interval in intervals):
        raise ValueError("null gap inside the model horizon")

    kept = [interval for interval in intervals if interval is not None]
    return {
        "intervals": kept,
        "total_snowfall_cm": round(sum(iv["snowfall_cm"] or 0.0 for iv in kept), 1),
        "total_precipitation_mm": round(sum(iv["precipitation_mm"] or 0.0 for iv in kept), 1),
        "effective_horizon_h": len(kept) * BUCKET_HOURS,
    }
