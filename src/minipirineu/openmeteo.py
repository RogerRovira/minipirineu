"""Open-Meteo Forecast API: request building and response parsing.

Everything except fetch() is a pure function over decoded JSON, so parsing
is testable against recorded fixtures without network access.

Variable availability was validated against the live API in milestone 1
(docs/notes/snowfall-semantics.md): AROME 2.5 serves snowfall natively
(cm per hour, ~7:1 snow/water ratio); AROME HD serves only surface
precipitation and temperature, so its snowfall is derived downstream.
Neither Météo-France model serves freezing_level_height — per-band
temperature_2m (elevation-downscaled) plays that role instead.
"""

import requests

from minipirineu.config import MODELS, TIMEZONE

API_URL = "https://api.open-meteo.com/v1/forecast"
# The rendered trio — everything the page consumes, and nothing else.
HOURLY_VARS = ("snowfall", "precipitation", "temperature_2m")
# Wide vars (T4, "archive wide publish narrow"): fetched and archived raw for
# Stage 1–2 (wet-bulb partition, undercatch, lagged blends) but NOT rendered.
SURFACE_WIDE_VARS = ("relative_humidity_2m", "wind_speed_10m", "wind_gusts_10m")
PRESSURE_LEVELS = (1000, 925, 850, 700, 600, 500)  # hPa, ordered ground -> up
_PRESSURE_KINDS = ("temperature", "relative_humidity", "geopotential_height")
PRESSURE_WIDE_VARS = tuple(
    f"{kind}_{level}hPa" for level in PRESSURE_LEVELS for kind in _PRESSURE_KINDS
)
ALL_HOURLY_VARS = HOURLY_VARS + SURFACE_WIDE_VARS + PRESSURE_WIDE_VARS
MODEL_IDS = tuple(spec.id for spec in MODELS)


def build_params(station, elevation_m: int) -> dict:
    """Query params for one station/band.

    models= is always explicit: best_match would silently substitute a global
    model, which is the exact failure mode this project exists to avoid. The
    elevation parameter drives Open-Meteo's downscaling to the band's height.
    """
    return {
        "latitude": station.latitude,
        "longitude": station.longitude,
        "elevation": elevation_m,
        "models": ",".join(MODEL_IDS),
        "hourly": ",".join(ALL_HOURLY_VARS),
        "timezone": TIMEZONE,
        # 3 local days always cover now+48h regardless of the run hour
        "forecast_days": 3,
    }


def fetch(session: requests.Session, station, elevation_m: int, timeout: int = 30) -> bytes:
    """Raw response BYTES: the caller archives them byte-faithful (ADR-0002)
    before any json.loads — a parser bug must never lose the payload."""
    resp = session.get(API_URL, params=build_params(station, elevation_m), timeout=timeout)
    resp.raise_for_status()
    return resp.content


def parse_response(raw: dict) -> dict:
    """Normalize a multi-model response into per-model hourly series.

    When several models are requested, Open-Meteo suffixes each hourly
    variable with the model id (e.g. snowfall_meteofrance_arome_france_hd).
    Hours beyond a model's horizon — and variables a model doesn't serve at
    all, like AROME HD's snowfall — come back as null and are kept as None;
    downstream code must never turn them into 0.
    """
    hourly = raw["hourly"]
    times = hourly["time"]
    models = {}
    for model_id in MODEL_IDS:
        series = {}
        for var in HOURLY_VARS:
            values = hourly[f"{var}_{model_id}"]
            if len(values) != len(times):
                raise ValueError(f"hourly series length mismatch for {var}_{model_id}")
            series[var] = values
        models[model_id] = {
            "snowfall_cm": series["snowfall"],
            "precipitation_mm": series["precipitation"],
            "temperature_c": series["temperature_2m"],
        }
    return {
        "time": times,
        "grid_elevation_m": raw.get("elevation"),
        "models": models,
    }


def pressure_profiles(raw: dict, model_id: str) -> list[tuple[list, list]]:
    """Per-hour (temps, heights) along PRESSURE_LEVELS, ground -> up.

    Lenient by design: a model without pressure levels (AROME HD — key
    present but all null, validated on the live API) or a missing key yields
    all-None profiles, which derive_freezing_level treats as no data.
    """
    hourly = raw["hourly"]
    n = len(hourly["time"])
    temps = [hourly.get(f"temperature_{level}hPa_{model_id}") or [None] * n
             for level in PRESSURE_LEVELS]
    heights = [hourly.get(f"geopotential_height_{level}hPa_{model_id}") or [None] * n
               for level in PRESSURE_LEVELS]
    return [([t[i] for t in temps], [h[i] for h in heights]) for i in range(n)]
