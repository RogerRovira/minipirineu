from datetime import datetime, timezone
from pathlib import Path

import pytest

from minipirineu.render import render_page

TEMPLATE = (Path(__file__).parent.parent / "templates" / "index.html.tmpl").read_text()
NOW = datetime(2026, 1, 10, 18, 30, tzinfo=timezone.utc)


def model(model_id, label, source, intervals):
    return {
        "model": model_id,
        "label": label,
        "snowfall_source": source,
        "intervals": intervals,
        "total_snowfall_cm": round(sum(iv["snowfall_cm"] or 0 for iv in intervals), 1),
        "total_precipitation_mm": 0.0,
        "effective_horizon_h": len(intervals) * 6,
    }


def interval(start, end, snow, temp):
    return {
        "start": start,
        "end": end,
        "snowfall_cm": snow,
        "precipitation_mm": 0.0,
        "temperature_c": temp,
    }


INTERVALS_HD = [
    interval("2026-01-10T18:00", "2026-01-11T00:00", 3.2, -2.5),
    interval("2026-01-11T00:00", "2026-01-11T06:00", 0.0, -4.0),
]
INTERVALS_25 = [
    interval("2026-01-10T18:00", "2026-01-11T00:00", 2.8, -2.1),
    interval("2026-01-11T00:00", "2026-01-11T06:00", 0.0, -3.9),
    # 2.5 reaches one bucket further than HD: union of columns, HD shows "—"
    interval("2026-01-11T06:00", "2026-01-11T12:00", 1.5, -5.0),
]

OPENMETEO = {
    "schema": "minipirineu/openmeteo/v1",
    "fetched_at": "2026-01-10T17:00:00+00:00",
    "timezone": "Europe/Madrid",
    "stations": [
        {
            "id": "baqueira",
            "name": "Baqueira",
            "latitude": 42.698,
            "longitude": 0.931,
            "bands": [
                {
                    "band": "baja",
                    "elevation_m": 1500,
                    "grid_elevation_m": 1500.0,
                    "models": [
                        model("meteofrance_arome_france_hd", "AROME HD 1.3 km", "derived", INTERVALS_HD),
                        model("meteofrance_arome_france", "AROME 2.5 km", "native", INTERVALS_25),
                    ],
                }
            ],
        }
    ],
}

METEOCAT = {
    "schema": "minipirineu/meteocat/v1",
    "fetched_at": "2026-01-10T07:00:00+00:00",
    "zones": [
        {
            "zone_id": "an",
            "zone_name": "Aran - Franja Nord",
            "stations": ["baqueira"],
            "days": [{"date": "2026-01-10", "summary": "Nevades febles al vessant nord."}],
        }
    ],
}


def render(openmeteo=OPENMETEO, meteocat=METEOCAT):
    return render_page(openmeteo, meteocat, NOW, TEMPLATE)


def test_full_page_golden_values():
    page = render()
    assert "Baqueira" in page
    assert "AROME HD 1.3 km" in page and "AROME 2.5 km" in page
    # snow values and per-row totals
    assert "3.2" in page and "2.8" in page
    assert '<td class="total">3.2</td>' in page
    assert '<td class="total">4.3</td>' in page
    # column headers: Spanish day + 6h range
    assert "sáb 18–00" in page
    # HD lacks the last 2.5 bucket → em dash, never a fake 0
    assert "—" in page
    # derived snow is visibly marked
    assert '<span class="model-note">*</span>' in page
    # temperature row present, from the native model (AROME 2.5: -2.1 → -2°)
    assert "T en cota" in page
    assert "<td>-2°</td>" in page


def test_freshness_badges_present_for_both_sources():
    page = render()
    assert page.count("data-fetched-at=") == 2
    assert 'class="badge fresh"' in page  # openmeteo, 1.5h old
    # meteocat 11.5h old but threshold 26h → fresh too
    assert 'class="badge stale"' not in page


def test_stale_source_looks_stale():
    old = dict(OPENMETEO, fetched_at="2026-01-09T17:00:00+00:00")
    page = render(openmeteo=old)
    assert 'class="badge stale"' in page


def test_missing_openmeteo_renders_placeholder_and_keeps_meteocat():
    page = render(openmeteo=None)
    assert "Sin datos de modelo" in page
    assert 'class="badge missing"' in page
    assert "Aran - Franja Nord" in page  # other source unaffected


def test_missing_meteocat_renders_placeholder_and_keeps_stations():
    page = render(meteocat=None)
    assert "Sin datos del Meteocat" in page
    assert "Baqueira" in page


def test_page_renders_with_no_data_at_all():
    page = render(openmeteo=None, meteocat=None)
    assert "MiniPiriNeu" in page
    assert page.count("sin datos") >= 1


def test_meteocat_zone_rendering():
    page = render()
    assert "Predicció de muntanya" in page
    assert "Nevades febles" in page
    assert "baqueira" in page


def test_snow_cells_highlighted_only_when_snowing():
    page = render()
    assert 'class="snow-some">3.2' in page
    assert 'class="snow-some">0<' not in page
