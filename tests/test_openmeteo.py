import json
from pathlib import Path

import pytest

from minipirineu import openmeteo
from minipirineu.config import STATIONS

FIXTURES = Path(__file__).parent / "fixtures"
BAQUEIRA = STATIONS[0]


def test_build_params_is_explicit_and_complete():
    params = openmeteo.build_params(BAQUEIRA, 2000)
    hourly = params.pop("hourly").split(",")
    assert params == {
        "latitude": 42.698,
        "longitude": 0.931,
        "elevation": 2000,
        "models": "meteofrance_arome_france_hd,meteofrance_arome_france",
        "timezone": "Europe/Madrid",
        "forecast_days": 3,
    }
    # rendered trio first (T4 must not change the page), then the wide vars
    assert hourly[:3] == ["snowfall", "precipitation", "temperature_2m"]
    for var in ("relative_humidity_2m", "wind_speed_10m", "wind_gusts_10m"):
        assert var in hourly
    for level in (1000, 925, 850, 700, 600, 500):
        for kind in ("temperature", "relative_humidity", "geopotential_height"):
            assert f"{kind}_{level}hPa" in hourly
    assert len(hourly) == len(set(hourly)) == 24


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


def wide_fixture() -> dict:
    return json.loads((FIXTURES / "openmeteo_wide_baqueira_1500.json").read_text())


def test_wide_response_parses_identically_for_rendered_vars():
    # the verification gate: widening the request must not change the page.
    # Recorded live 2026-07-17 with the full T4 variable set.
    wide = wide_fixture()
    narrow = {**wide, "hourly": {
        k: v for k, v in wide["hourly"].items()
        if not ("hPa" in k or "wind_" in k or "relative_humidity_2m" in k)
    }}
    assert openmeteo.parse_response(wide) == openmeteo.parse_response(narrow)


def test_pressure_profiles_serve_arome25_only():
    wide = wide_fixture()
    profiles = openmeteo.pressure_profiles(wide, "meteofrance_arome_france")
    assert len(profiles) == len(wide["hourly"]["time"])
    temps, heights = profiles[0]
    assert len(temps) == 6 and len(heights) == 6
    assert any(t is not None for t in temps)
    # heights ordered ground -> up wherever present
    real_heights = [h for h in heights if h is not None]
    assert real_heights == sorted(real_heights)
    # AROME HD serves no pressure levels (validated M1 and re-validated on
    # this fixture): all None, never fabricated
    hd = openmeteo.pressure_profiles(wide, "meteofrance_arome_france_hd")
    assert all(t is None for temps, _ in hd for t in temps)


def test_parse_rejects_length_mismatch():
    hourly = {"time": ["2026-07-14T00:00", "2026-07-14T01:00"]}
    for model_id in ("meteofrance_arome_france_hd", "meteofrance_arome_france"):
        hourly[f"snowfall_{model_id}"] = [0.0, 0.0]
        hourly[f"precipitation_{model_id}"] = [0.0, 0.0]
        hourly[f"temperature_2m_{model_id}"] = [5.0, 5.0]
    hourly["snowfall_meteofrance_arome_france_hd"] = [0.0]  # truncated
    with pytest.raises(ValueError, match="length mismatch"):
        openmeteo.parse_response({"hourly": hourly})
