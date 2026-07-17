"""Gated-model degradation contract (S2.3): an experimental column may go
missing — rendered as "—", never as 0 — but can never fail the AROME ingest."""

from datetime import datetime

import pytest

from minipirineu import ingest_openmeteo as ingest
from minipirineu.config import MODELS, STATIONS

DEFAULT_SPECS = [spec for spec in MODELS if not spec.gated]
GATED_SPECS = [spec for spec in MODELS if spec.gated]
# 15:00 local → first bucket 12–18 is inside the fixture's 24h of data
NOW = datetime(2026, 1, 10, 15, 0)


def parsed_fixture(model_ids, snowfall=0.4):
    times = [f"2026-01-10T{h:02d}:00" for h in range(24)]
    return {
        "time": times,
        "grid_elevation_m": 2000.0,
        "models": {
            model_id: {
                "snowfall_cm": [snowfall] * 24,
                "precipitation_mm": [1.0] * 24,
                "temperature_c": [-3.0] * 24,
            }
            for model_id in model_ids
        },
    }


def test_gated_entry_available_carries_flag_and_buckets():
    spec = GATED_SPECS[0]
    entry = ingest.gated_entry(spec, parsed_fixture([spec.id]), NOW)
    assert entry["gated"] is True
    assert "unavailable" not in entry
    assert entry["intervals"] and entry["total_snowfall_cm"] > 0


def test_gated_entry_when_fetch_failed_is_unavailable():
    entry = ingest.gated_entry(GATED_SPECS[0], None, NOW)
    assert entry["gated"] is True and entry["unavailable"] is True
    assert entry["intervals"] == []
    assert entry["total_snowfall_cm"] is None  # "—" downstream, never 0


def test_gated_native_all_null_snowfall_is_unavailable_not_zero(capsys):
    # live precip + dead snowfall would otherwise sum to a fake 0-cm column
    spec = GATED_SPECS[0]
    entry = ingest.gated_entry(spec, parsed_fixture([spec.id], snowfall=None), NOW)
    assert entry["unavailable"] is True
    assert entry["total_snowfall_cm"] is None
    assert "unavailable" in capsys.readouterr().err


def test_gated_model_missing_from_response_is_unavailable():
    spec = GATED_SPECS[0]
    other = parsed_fixture([GATED_SPECS[1].id])
    assert ingest.gated_entry(spec, other, NOW)["unavailable"] is True


def test_default_native_all_null_still_fails_loudly():
    arome25 = next(s for s in DEFAULT_SPECS if s.snowfall_source == "native")
    series = parsed_fixture([arome25.id], snowfall=None)["models"][arome25.id]
    with pytest.raises(ValueError, match="all null"):
        ingest.snowfall_series(arome25, series)


def make_snapshot():
    interval = {
        "start": "2026-01-10T12:00",
        "end": "2026-01-10T18:00",
        "snowfall_cm": 1.0,
        "precipitation_mm": 2.0,
        "temperature_c": -3.0,
    }
    stations = []
    for station in STATIONS:
        bands = []
        for band, elevation_m in station.bands:
            models = []
            for spec in MODELS:
                if spec.gated:
                    models.append(ingest.unavailable_entry(spec))
                else:
                    models.append(
                        {
                            "model": spec.id,
                            "label": spec.label,
                            "snowfall_source": spec.snowfall_source,
                            "gated": False,
                            "intervals": [dict(interval)],
                            "total_snowfall_cm": 1.0,
                            "total_precipitation_mm": 2.0,
                            "effective_horizon_h": 6,
                        }
                    )
            bands.append(
                {
                    "band": band,
                    "elevation_m": elevation_m,
                    "grid_elevation_m": float(elevation_m),
                    "models": models,
                }
            )
        stations.append(
            {
                "id": station.id,
                "name": station.name,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "bands": bands,
            }
        )
    return {
        "schema": ingest.SCHEMA,
        "fetched_at": "2026-01-10T14:00:00+00:00",
        "timezone": "Europe/Madrid",
        "stations": stations,
    }


def test_validate_accepts_all_gated_models_unavailable():
    ingest.validate(make_snapshot())  # must not raise


def test_validate_rejects_empty_intervals_on_default_models():
    snapshot = make_snapshot()
    snapshot["stations"][0]["bands"][0]["models"][0]["intervals"] = []
    with pytest.raises(ValueError, match="no intervals"):
        ingest.validate(snapshot)


def test_validate_rejects_unavailable_marker_on_default_models():
    snapshot = make_snapshot()
    arome_hd = snapshot["stations"][0]["bands"][0]["models"][0]
    arome_hd["unavailable"] = True
    arome_hd["intervals"] = []
    with pytest.raises(ValueError, match="unavailable"):
        ingest.validate(snapshot)


def test_validate_rejects_half_filled_unavailable_gated_model():
    snapshot = make_snapshot()
    gated = snapshot["stations"][0]["bands"][0]["models"][-1]
    assert gated["unavailable"] is True
    gated["intervals"] = [
        {
            "start": "2026-01-10T12:00",
            "end": "2026-01-10T18:00",
            "snowfall_cm": 1.0,
            "precipitation_mm": 2.0,
            "temperature_c": -3.0,
        }
    ]
    with pytest.raises(ValueError, match="unavailable"):
        ingest.validate(snapshot)
