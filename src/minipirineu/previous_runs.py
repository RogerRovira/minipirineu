"""Previous Runs API client for the pre-winter backtest (S0.6a/T9).

Fixed-lead AROME forecast series (`{var}_previous_day{N}_{model}`) re-fetched at
XEMA truth-station points, aggregated into the same 6 h UTC buckets as the truth
(ADR-0004), and written as forecast rows for verify.py (T8). This is the forecast
side of the frozen baseline (ADR-0003) — the same rows the live loop (T11) writes,
so one scoring path covers backtest and live.

Findings that fix the parameters (endpoint, key structure, archive floor
2024-01-19T12:00Z, day2 empty for AROME → 24 h-lead only, HD snowfall null →
derived column) are in docs/notes/previous-runs-coverage.md, from the probe of
2026-07-29.

Everything except fetch() is a pure function over decoded JSON, so the parser is
testable against the recorded fixture without network. Missing stays missing: an
incomplete 6 h bucket produces no row, never a fabricated 0.
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

import requests

from minipirineu import aggregate
from minipirineu.config import (
    BACKTEST_LEAD_DAYS,
    BUCKET_HOURS,
    CALL_UNIT_DAYS,
    MODELS,
    PREV_LEAD_H,
)
from minipirineu.store import Row
from minipirineu.truth import parse_stamp
from minipirineu.verify import forecast_variable

API_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SOURCE = "openmeteo"  # store row source (build_pairs is source-agnostic, T8)
# The three series requested per lead day. HD returns snowfall all-null (M1); we
# ignore it and derive HD's column from precipitation + temperature instead.
BASE_VARS = ("temperature_2m", "precipitation", "snowfall")
MODEL_IDS = tuple(spec.id for spec in MODELS)


def previous_var(var: str, day: int) -> str:
    """`temperature_2m`, day 1 → `temperature_2m_previous_day1` (probe-confirmed)."""
    return f"{var}_previous_day{day}"


def build_params(
    latitude: float,
    longitude: float,
    elevation_m: int,
    start_date: str,
    end_date: str,
    lead_days=BACKTEST_LEAD_DAYS,
) -> dict:
    """Query params for one station point over [start_date, end_date] (inclusive).

    timezone=UTC so hourly stamps align with the UTC verification buckets; models=
    is always explicit (both AROME variants); elevation drives the temperature
    downscaling to the XEMA station's height.
    """
    hourly = [previous_var(v, d) for d in lead_days for v in BASE_VARS]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "elevation": elevation_m,
        "models": ",".join(MODEL_IDS),
        "hourly": ",".join(hourly),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "UTC",
    }


def fetch(
    session: requests.Session,
    latitude: float,
    longitude: float,
    elevation_m: int,
    start_date: str,
    end_date: str,
    lead_days=BACKTEST_LEAD_DAYS,
    timeout: int = 60,
) -> bytes:
    """Raw response BYTES; the caller archives them byte-faithful (ADR-0002)
    before any json.loads — a parser bug must never lose the payload."""
    params = build_params(latitude, longitude, elevation_m, start_date, end_date, lead_days)
    resp = session.get(API_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.content


# --- parsing ----------------------------------------------------------------

def _fmt_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hourly_series(hourly: dict, var: str, day: int, model_id: str):
    """The `{var}_previous_day{day}_{model_id}` list, or None if the key is
    absent. A present-but-all-null series (HD snowfall) is returned as-is and
    bucketizes to nothing — that is the intended 'missing is missing'.

    Reads ONLY the model-suffixed key. Open-Meteo suffixes hourly keys with the
    model id only when >1 model is requested; build_params always requests both
    AROME models, so the suffix is always present. Narrowing to a single model
    would make every lookup miss and silently yield 0 rows — see the
    single-model caveat in docs/notes/previous-runs-coverage.md."""
    return hourly.get(f"{previous_var(var, day)}_{model_id}")


def bucketize(times: list[str], values: list, hours: int = BUCKET_HOURS) -> list[tuple[str, float]]:
    """Sum an hourly UTC series into 6 h buckets aligned to 00/06/12/18.

    Only buckets fully covered (all `hours` consecutive hours present and
    non-null) are returned, as (bucket_start `...Z`, summed cm). A partial edge
    bucket or a null hole drops the whole bucket — never a partial total.
    """
    groups: dict[datetime, dict[int, float]] = defaultdict(dict)
    for t, v in zip(times, values, strict=True):
        dt = datetime.fromisoformat(t)  # naive UTC (timezone=UTC in the request)
        b_start = dt.replace(hour=dt.hour - dt.hour % hours, minute=0, second=0, microsecond=0)
        off = int((dt - b_start).total_seconds() // 3600)
        groups[b_start][off] = v
    out = []
    for b_start in sorted(groups):
        vals = groups[b_start]
        if len(vals) == hours and all(vals.get(o) is not None for o in range(hours)):
            out.append((_fmt_z(b_start), round(sum(vals[o] for o in range(hours)), 1)))
    return out


def column_buckets(hourly: dict, times: list[str], spec, day: int) -> list[tuple[str, float]]:
    """One model's snowfall column, as 6 h bucket cm. Native models bucket their
    own snowfall; derived models (AROME HD) recompute per-hour snowfall from
    precipitation + temperature via aggregate.derive_snowfall — the same rule the
    live column uses (import, don't duplicate)."""
    if spec.snowfall_source == "native":
        series = _hourly_series(hourly, "snowfall", day, spec.id)
        return bucketize(times, series) if series is not None else []
    precip = _hourly_series(hourly, "precipitation", day, spec.id)
    temp = _hourly_series(hourly, "temperature_2m", day, spec.id)
    if precip is None or temp is None:
        return []
    return bucketize(times, aggregate.derive_snowfall(precip, temp))


def to_forecast_rows(raw: dict, station_code: str, lead_days=BACKTEST_LEAD_DAYS) -> list[Row]:
    """Decoded response → forecast store rows, one per (model column, lead, 6 h
    bucket). valid_time = bucket start; run_time = valid_time − lead (24 h for
    day1); variable = `fx.snowfall_cm.<column>` (T8 convention). Empty series
    (AROME day2, HD snowfall) simply contribute no rows."""
    hourly = raw["hourly"]
    times = hourly["time"]
    rows: list[Row] = []
    for spec in MODELS:
        variable = forecast_variable(spec.column)
        for day in lead_days:
            lead_h = PREV_LEAD_H[day]
            for b_start, cm in column_buckets(hourly, times, spec, day):
                run = _fmt_z(parse_stamp(b_start) - timedelta(hours=lead_h))
                rows.append(Row(SOURCE, station_code, run, b_start, variable, cm))
    return rows


def parse_payload(raw: bytes, station_code: str, lead_days=BACKTEST_LEAD_DAYS) -> list[Row]:
    """Archive-faithful entry point: decode raw response bytes, then parse."""
    return to_forecast_rows(json.loads(raw), station_code, lead_days)


# --- budget guard -----------------------------------------------------------

def estimate_call_units(n_days: int, n_series: int, days_per_unit: float = CALL_UNIT_DAYS) -> int:
    """Approximate an Open-Meteo call's cost in 'call units'. Open-Meteo weights
    a request by how much data it returns; ceil(days / block) · series is a
    conservative proxy used only to keep the one-off backfill far below the
    ~10 000/day non-commercial ceiling."""
    return max(1, math.ceil(n_days / days_per_unit) * n_series)
