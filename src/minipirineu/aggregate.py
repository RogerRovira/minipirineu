"""Derive and aggregate hourly model series into the 6h intervals of the table."""

from datetime import datetime, timedelta

from minipirineu.config import (
    BUCKET_HOURS,
    DERIVED_SNOW_RATIO_MAX,
    DERIVED_SNOW_T_FULL_C,
    DERIVED_SNOW_T_ZERO_C,
    FORECAST_HOURS,
    WETBULB_T_FULL_C,
    WETBULB_T_ZERO_C,
)
from minipirineu.wetbulb import stull_wet_bulb


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


def wetbulb_snow_ratio(
    wet_bulb_c: float,
    *,
    t_full: float = WETBULB_T_FULL_C,
    t_zero: float = WETBULB_T_ZERO_C,
) -> float:
    """cm of snow per mm of precipitation as a function of wet-bulb temperature.

    Same shape and cold ratio as snow_ratio, but the rain/snow taper is keyed on
    Stull wet-bulb Tw rather than dry-bulb T (S1.1). The 0.45 cold ratio is a
    physical water→depth property and does not change with the phase driver; only
    the transition breakpoints move onto the wet-bulb axis. The breakpoints are
    kwargs (defaulting to config) so the S1.1 calibration can sweep them without
    mutating global state — the coefficient-as-kwarg idiom truth_b already uses.
    """
    if wet_bulb_c <= t_full:
        return DERIVED_SNOW_RATIO_MAX
    if wet_bulb_c >= t_zero:
        return 0.0
    return DERIVED_SNOW_RATIO_MAX * (t_zero - wet_bulb_c) / (t_zero - t_full)


def derive_snowfall_wetbulb(
    precipitation_mm: list,
    temperature_c: list,
    relative_humidity_pct: list,
    *,
    t_full: float = WETBULB_T_FULL_C,
    t_zero: float = WETBULB_T_ZERO_C,
) -> list:
    """Wet-bulb variant of derive_snowfall (S1.1): each hour partitions its own
    precipitation using the wet-bulb ratio from T and RH.

    Missing precip or temp → None (unknown, not zero), as in derive_snowfall.
    Missing RH alone is NOT fatal: the hour falls back to the dry-bulb snow_ratio
    (Tw undefined without humidity), so a humidity gap degrades to the old
    behaviour rather than nulling an otherwise-known snowfall hour.
    """
    derived = []
    for precip, temp, rh in zip(
        precipitation_mm, temperature_c, relative_humidity_pct, strict=True
    ):
        if precip is None or temp is None:
            derived.append(None)
            continue
        wet_bulb = stull_wet_bulb(temp, rh)
        ratio = (
            snow_ratio(temp)
            if wet_bulb is None
            else wetbulb_snow_ratio(wet_bulb, t_full=t_full, t_zero=t_zero)
        )
        derived.append(precip * ratio)
    return derived


# ModelSpec.snowfall_source → partition name for derive_column_snowfall. The one
# mapping used by every path (render, live scoring, backtest) so a derived model
# is partitioned the same everywhere (ADR-0003).
DERIVED_PARTITION = {"derived": "drybulb", "derived_wetbulb": "wetbulb"}


def derive_column_snowfall(
    partition: str,
    precipitation_mm: list,
    temperature_c: list,
    relative_humidity_pct: list,
) -> list:
    """Hourly snowfall (cm) for a derived challenger column (config.DerivedColumn).

    The single place a partition name maps to its derivation, shared by the live
    scoring path (live_forecast), the render path (ingest_openmeteo) and the
    backtest (previous_runs) so all write byte-identical cm for the same inputs
    (ADR-0003 symmetry). "drybulb" ignores RH (the pre-S1.1 taper).
    """
    if partition == "wetbulb":
        return derive_snowfall_wetbulb(precipitation_mm, temperature_c, relative_humidity_pct)
    if partition == "drybulb":
        return derive_snowfall(precipitation_mm, temperature_c)
    raise ValueError(f"unknown derived-column partition: {partition!r}")


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
