"""HTML for the Meteocat muntanya column (milestone 3).

Consumes data/meteocat.json (minipirineu/meteocat/v1, built by
ingest_meteocat): per zone, per day, four 6h blocks of coded variables plus
the 24h accumulation BINS. Codes map to official labels in meteocat_labels;
an unknown code renders as the raw code and missing values as an em dash —
this column must never crash the page (independent ingestions, feature 4).
"""

import html
from datetime import datetime

from minipirineu import meteocat_labels as labels

DAY_ABBREV = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

_ATTRIBUTION = (
    '<p class="attribution">Font: Servei Meteorològic de Catalunya '
    '(<a href="https://www.meteo.cat">meteo.cat</a>), predicció de muntanya '
    "del Pirineu per zones.</p>"
)


def _row(label: str, cells: list[str]) -> str:
    tds = "".join(f"<td>{html.escape(cell)}</td>" for cell in cells)
    return f'<tr><td class="cota">{label}</td>{tds}</tr>'


def _cota(value) -> str:
    return labels.MISSING if value is None else f"{int(value)} m"


def _day_html(day: dict) -> str:
    when = datetime.fromisoformat(day["date"])
    blocks = day["blocks"]
    header = "".join(f"<th>{b['start']:02d}–{b['start'] + 6:02d}</th>" for b in blocks)
    rows = (
        _row("Cielo", [labels.label(labels.CEL, b["cel"]) for b in blocks])
        + _row("Prob. precip", [labels.label(labels.PROBABILITAT, b["probabilitat"]) for b in blocks])
        + _row("Cota nieve", [_cota(b["cota_m"]) for b in blocks])
    )
    totals = (f"Nieve 24h: {labels.label(labels.ACUMULACIO_NEU, day['acumulacio_neu'])}"
              f" · Precipitación 24h: {labels.label(labels.ACUMULACIO, day['acumulacio'])}")
    return (
        f'<div class="mc-day"><h4>{DAY_ABBREV[when.weekday()]} '
        f"{when.day:02d}/{when.month:02d}</h4>"
        f'<div class="table-wrap"><table><thead><tr><th class="cota"></th>'
        f"{header}</tr></thead><tbody>{rows}</tbody></table></div>"
        f'<p class="mc-totals">{html.escape(totals)}</p></div>'
    )


def _zone_html(zone: dict) -> str:
    stations = ", ".join(zone["stations"])
    days = "".join(_day_html(day) for day in zone["days"])
    return (
        f'<h3>{html.escape(zone["zone_name"])} <span class="model-note">'
        f"({html.escape(stations)})</span></h3>{days}"
    )


def render_meteocat(data: dict | None) -> str:
    title = "Predicció de muntanya (Meteocat)"
    if data is None:
        return (f"<section><h2>{title}</h2>"
                '<div class="placeholder">Sin datos del Meteocat.</div></section>')
    zones = "".join(_zone_html(zone) for zone in data["zones"])
    return f"<section><h2>{title}</h2>{zones}{_ATTRIBUTION}</section>"
