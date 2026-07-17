import json
from pathlib import Path

import pytest

from minipirineu import openmeteo
from minipirineu.config import STATIONS

FIXTURES = Path(__file__).parent / "fixtures"
BAQUEIRA = STATIONS[0]


def test_build_params_is_explicit_and_complete():
    params = openmeteo.build_params(BAQUEIRA, 2000)
    assert params == {
        "latitude": 42.698,
        "longitude": 0.931,
        "elevation": 2000,
        "models": "meteofrance_arome_france_hd,meteofrance_arome_france",
        "hourly": "snowfall,precipitation,temperature_2m",
        "timezone": "Europe/Madrid",
        "forecast_days": 3,
    }


def test_best_match_never_appears():
    for station in STATIONS:
        for _, elevation in station.bands:
            assert "best_match" not in str(openmeteo.build_params(station, elevation))


def test_parse_recorded_response():
    raw = json.loads((FIXTURES / "openmeteo_baqueira_2000.json").read_text())
    parsed = openmeteo.parse_response(raw)

    assert set(parsed["models"]) == {
        "meteofrance_arome_france_hd",
        "meteofrance_arome_france",
    }
    n = len(parsed["time"])
    assert n >= 48
    for series in parsed["models"].values():
        for key in ("snowfall_cm", "precipitation_mm", "temperature_c"):
            assert len(series[key]) == n
        # every model serves precipitation and temperature over its horizon
        real_precip = [v for v in series["precipitation_mm"] if v is not None]
        assert real_precip and all(v >= 0 for v in real_precip)
        real_temp = [v for v in series["temperature_c"] if v is not None]
        assert real_temp and all(-45 <= v <= 45 for v in real_temp)

    # AROME 2.5 serves snowfall natively; AROME HD does not (validated live,
    # see docs/notes/snowfall-semantics.md) — nulls must survive parsing as None
    arome25 = parsed["models"]["meteofrance_arome_france"]
    assert any(v is not None for v in arome25["snowfall_cm"])
    arome_hd = parsed["models"]["meteofrance_arome_france_hd"]
    assert all(v is None for v in arome_hd["snowfall_cm"])

    # elevation echo confirms Open-Meteo applied the requested downscaling height
    assert parsed["grid_elevation_m"] == 2000


def test_build_params_gated_models_are_explicit():
    params = openmeteo.build_params(BAQUEIRA, 2000, openmeteo.GATED_MODEL_IDS)
    assert params["models"] == "knmi_harmonie_arome_europe,dmi_harmonie_arome_europe,ecmwf_ifs"
    assert "best_match" not in str(params)
    # everything except the model list matches the default request
    default = openmeteo.build_params(BAQUEIRA, 2000)
    assert {k: v for k, v in params.items() if k != "models"} == {
        k: v for k, v in default.items() if k != "models"
    }


def test_parse_recorded_gated_response():
    # recorded 2026-07-18: all three gated models serve every variable over
    # the first 48h at Baqueira/2000 m, snowfall included (native)
    raw = json.loads((FIXTURES / "openmeteo_gated_baqueira_2000.json").read_text())
    parsed = openmeteo.parse_response(raw, openmeteo.GATED_MODEL_IDS)

    assert set(parsed["models"]) == set(openmeteo.GATED_MODEL_IDS)
    n = len(parsed["time"])
    assert n >= 48
    for model_id, series in parsed["models"].items():
        for key in ("snowfall_cm", "precipitation_mm", "temperature_c"):
            assert len(series[key]) == n, (model_id, key)
        assert all(v is not None for v in series["snowfall_cm"][:48]), model_id
    assert parsed["grid_elevation_m"] == 2000


def test_parse_rejects_length_mismatch():
    hourly = {"time": ["2026-07-14T00:00", "2026-07-14T01:00"]}
    for model_id in ("meteofrance_arome_france_hd", "meteofrance_arome_france"):
        hourly[f"snowfall_{model_id}"] = [0.0, 0.0]
        hourly[f"precipitation_{model_id}"] = [0.0, 0.0]
        hourly[f"temperature_2m_{model_id}"] = [5.0, 5.0]
    hourly["snowfall_meteofrance_arome_france_hd"] = [0.0]  # truncated
    with pytest.raises(ValueError, match="length mismatch"):
        openmeteo.parse_response({"hourly": hourly})
