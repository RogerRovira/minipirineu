"""Verification page render (S0.7/T11).

Golden-style checks over synthetic verify reports: the page shows baseline beside
live per column, maps column ids to human labels, and renders a missing side as
"—" (never a fabricated 0). No I/O beyond the template.
"""

from datetime import datetime, timezone
from pathlib import Path

from minipirineu import render_verification as rv

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
TEMPLATE = Path("templates/verificacion.html.tmpl").read_text(encoding="utf-8")


def _report(mae_6h, *, column="arome_25", n_pairs=200, snow=True):
    day = {column: {"n_cm": 50, "mae": 3.10, "pod": 0.32, "far": 0.04, "csi": 0.31}} if snow else {}
    return {
        "n_pairs": n_pairs,
        "bucket_6h": {"by_column": {
            column: {"n_cm": 100, "mae": mae_6h, "bias": -0.70, "pod": 0.33, "far": 0.23, "csi": 0.30},
        }},
        "snow_day_24h": {"by_column": day},
    }


def test_page_shows_baseline_and_live_side_by_side():
    page = rv.render_verification_page(_report(0.91), _report(1.15), NOW, TEMPLATE,
                                       baseline_ref="baseline-2026-07-29.json")
    assert "AROME 2.5 km" in page          # column id → human label
    assert "0.91" in page                  # baseline 6h MAE
    assert "1.15" in page                  # live 6h MAE
    assert "CC-BY" in page and "XEMA" in page  # mandatory attribution
    assert "baseline-2026-07-29.json" in page


def test_missing_live_renders_as_dash_not_zero():
    page = rv.render_verification_page(_report(0.91), None, NOW, TEMPLATE)
    assert "sin datos live todavía" in page
    assert "—" in page                     # live cells are missing
    # a genuine metric value still shows; nothing is fabricated as 0.00
    assert "0.91" in page and "0.00" not in page


def test_column_label_falls_back_for_unknown_column():
    assert rv.column_label("arome_hd") == "AROME HD 1.3 km"
    assert rv.column_label("meteocat") == "Meteocat (muntanya)"
    assert rv.column_label("ecmwf_ifs") == "ecmwf ifs"  # prettified fallback


def test_fmt_missing_is_dash():
    assert rv._fmt(None) == "—"
    assert rv._fmt(0.0) == "0.00"          # a real zero is shown, only None is —
    assert rv._fmt(50) == "50"


def test_empty_reports_render_without_crashing():
    page = rv.render_verification_page(None, None, NOW, TEMPLATE)
    assert "sin baseline" in page and "sin datos live todavía" in page
    assert "Sin columnas todavía" in page  # both tables empty → placeholder
