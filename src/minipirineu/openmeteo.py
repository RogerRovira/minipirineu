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
HOURLY_VARS = ("snowfall", "precipitation", "temperature_2m")
# The default AROME pair and the gated inter-family trio (S2.3) travel in
# separate requests: one bad gated model id turns the whole HTTP request into
# a 400, and that must never be able to poison the AROME fetch.
DEFAULT_MODEL_IDS = tuple(spec.id for spec in MODELS if not spec.gated)
GATED_MODEL_IDS = tuple(spec.id for spec in MODELS if spec.gated)


def build_params(station, elevation_m: int, model_ids: tuple = DEFAULT_MODEL_IDS) -> dict:
    """Query params for one station/band.

    models= is always explicit: best_match would silently substitute a global
    model, which is the exact failure mode this project exists to avoid. The
    elevation parameter drives Open-Meteo's downscaling to the band's height.
    """
    return {
        "latitude": station.latitude,
        "longitude": station.longitude,
        "elevation": elevation_m,
        "models": ",".join(model_ids),
        "hourly": ",".join(HOURLY_VARS),
        "timezone": TIMEZONE,
        # 3 local days always cover now+48h regardless of the run hour
        "forecast_days": 3,
    }


def fetch(
    session: requests.Session,
    station,
    elevation_m: int,
    model_ids: tuple = DEFAULT_MODEL_IDS,
    timeout: int = 30,
) -> dict:
    resp = session.get(API_URL, params=build_params(station, elevation_m, model_ids), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_response(raw: dict, model_ids: tuple = DEFAULT_MODEL_IDS) -> dict:
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
    for model_id in model_ids:
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
