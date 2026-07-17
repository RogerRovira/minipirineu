"""CLI: read data/*.json → write the static site (site/index.html + css).

Rendering must always succeed with whatever data exists: each source file is
optional, and a missing/broken source renders as a visible placeholder while
the rest of the page stays intact (independent ingestions, feature 4).
"""

import html
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from minipirineu.config import STALE_AFTER_H
from minipirineu.render_meteocat import DAY_ABBREV, render_meteocat

TEMPLATE_PATH = Path("templates/index.html.tmpl")
STYLE_PATH = Path("assets/style.css")
DATA_DIR = Path("data")
SITE_DIR = Path("site")

# The temperature row shows the native model's take (AROME 2.5); the derived
# HD snow already encodes HD's own temperature.
TEMP_MODEL = "meteofrance_arome_france"


def load_source(path: Path) -> dict | None:
    """A source that is missing or unreadable renders as absent, never crashes."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_hours(fetched_at_iso: str, now_utc: datetime) -> float:
    fetched = datetime.fromisoformat(fetched_at_iso)
    return (now_utc - fetched).total_seconds() / 3600


def age_label(hours: float) -> str:
    if hours < 1:
        return f"hace {int(hours * 60)} min"
    return f"hace {int(hours)} h"


def freshness_badge(source_key: str, label: str, data: dict | None, now_utc: datetime) -> str:
    """Per-source freshness chip. Render-time class covers the common case;
    the inline JS in the template recomputes it client-side so staleness shows
    even if the cron itself stops regenerating the page."""
    if data is None:
        return f'<span class="badge missing">{html.escape(label)}: sin datos</span>'
    stale_after = STALE_AFTER_H[source_key]
    hours = age_hours(data["fetched_at"], now_utc)
    cls = "stale" if hours > stale_after else "fresh"
    return (
        f'<span class="badge {cls}" data-fetched-at="{html.escape(data["fetched_at"])}"'
        f' data-stale-after-h="{stale_after}">'
        f'{html.escape(label)}: <span class="age">{age_label(hours)}</span></span>'
    )


def interval_columns(station: dict) -> list[tuple[str, str]]:
    """Union of interval starts across the station's models → (start, header)."""
    starts = {}
    for band in station["bands"]:
        for model in band["models"]:
            for iv in model["intervals"]:
                starts[iv["start"]] = iv["end"]
    columns = []
    for start in sorted(starts):
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(starts[start])
        columns.append((start, f"{DAY_ABBREV[s.weekday()]} {s.hour:02d}–{e.hour:02d}"))
    return columns


def fmt_snow(value) -> str:
    if value is None:
        return "—"
    return "0" if value == 0 else f"{value:.1f}"


def fmt_temp(value) -> str:
    return "—" if value is None else f"{value:+.0f}°"


def snow_cells(model: dict, columns: list[tuple[str, str]]) -> str:
    # the 48h total leads so it stays visible on a phone without scrolling
    by_start = {iv["start"]: iv for iv in model["intervals"]}
    cells = [f'<td class="total">{fmt_snow(model["total_snowfall_cm"])}</td>']
    for start, _ in columns:
        iv = by_start.get(start)
        value = iv["snowfall_cm"] if iv else None
        cls = ' class="snow-some"' if value else ""
        cells.append(f"<td{cls}>{fmt_snow(value)}</td>")
    return "".join(cells)


def temp_cells(model: dict, columns: list[tuple[str, str]]) -> str:
    by_start = {iv["start"]: iv for iv in model["intervals"]}
    cells = ["<td></td>"]
    for start, _ in columns:
        iv = by_start.get(start)
        cells.append(f"<td>{fmt_temp(iv['temperature_c'] if iv else None)}</td>")
    return "".join(cells)


def model_row_label(model: dict) -> str:
    note = ' <span class="model-note">*</span>' if model["snowfall_source"] == "derived" else ""
    return f'{html.escape(model["label"])}{note}'


def render_station(station: dict) -> str:
    columns = interval_columns(station)
    headers = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
    rows = []
    # high band on top, like a mountain
    for band in reversed(station["bands"]):
        n_rows = len(band["models"]) + 1
        cota = (
            f'<td class="cota" rowspan="{n_rows}">{html.escape(band["band"])}'
            f'<br>{band["elevation_m"]} m</td>'
        )
        for i, model in enumerate(band["models"]):
            rows.append(
                "<tr>"
                + (cota if i == 0 else "")
                + f'<td class="cota">{model_row_label(model)}</td>'
                + snow_cells(model, columns)
                + "</tr>"
            )
        temp_model = next(m for m in band["models"] if m["model"] == TEMP_MODEL)
        rows.append(
            '<tr class="temp">'
            + '<td class="cota">T en cota</td>'
            + temp_cells(temp_model, columns)
            + "</tr>"
        )
    return (
        f'<section><h2>{html.escape(station["name"])}</h2><div class="table-wrap">'
        f'<table><thead><tr><th class="cota">Cota</th><th class="cota">cm nieve</th>'
        f'<th>Σ 48h</th>{headers}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def render_openmeteo(data: dict | None) -> str:
    if data is None:
        return (
            '<div class="placeholder">Sin datos de modelo (Open-Meteo): '
            "la última ingesta no está disponible.</div>"
        )
    return "\n".join(render_station(station) for station in data["stations"])


def render_page(
    openmeteo: dict | None,
    meteocat: dict | None,
    now_utc: datetime,
    template_text: str,
) -> str:
    sources = "\n".join(
        (
            freshness_badge("openmeteo", "Modelos AROME", openmeteo, now_utc),
            freshness_badge("meteocat", "Meteocat", meteocat, now_utc),
        )
    )
    return Template(template_text).substitute(
        sources_html=sources,
        stations_html=render_openmeteo(openmeteo),
        meteocat_html=render_meteocat(meteocat),
        generated_label=now_utc.strftime("%Y-%m-%d %H:%M UTC"),
    )


def main(data_dir: Path = DATA_DIR, site_dir: Path = SITE_DIR) -> int:
    now_utc = datetime.now(timezone.utc)
    page = render_page(
        load_source(data_dir / "openmeteo.json"),
        load_source(data_dir / "meteocat.json"),
        now_utc,
        TEMPLATE_PATH.read_text(encoding="utf-8"),
    )
    site_dir.mkdir(parents=True, exist_ok=True)
    # explicit utf-8: the page declares charset=utf-8 and must not depend on
    # the platform's default encoding (Windows: cp1252, which lacks Σ)
    (site_dir / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(STYLE_PATH, site_dir / "style.css")
    print(f"wrote {site_dir}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
