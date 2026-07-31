"""CLI: render the verification "how wrong were we" page (S0.7/T11).

Reads the frozen baseline report (docs/verification/baseline-*.json, T10) and,
when it exists, a trailing live report (same verify.py schema), and renders
site/verificacion.html: per-column cm error and event skill with the frozen
baseline beside the trailing live scores. The same metric engine (verify.py,
T8) sits behind both numbers — that symmetry is the whole point (ADR-0003).

Missing renders as missing: an absent live report (no live pairs yet) shows "—"
across the live cells, never 0. Attribution for Open-Meteo (CC-BY 4.0) and
Meteocat is mandatory and baked into the template.
"""

import argparse
import html
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from minipirineu import opg
from minipirineu.config import MODELS

TEMPLATE_PATH = Path("templates/verificacion.html.tmpl")
STYLE_PATH = Path("assets/style.css")
BASELINE_DIR = Path("docs/verification")
SITE_DIR = Path("site")

# column id → human label. AROME columns come from config (single source of
# truth); other columns (Meteocat, later IFS/HARMONIE) fall back to their id
# prettified, so a new column renders sanely before it gets a label here.
COLUMN_LABELS = {m.column: m.label for m in MODELS}
COLUMN_LABELS.setdefault("meteocat", "Meteocat (muntanya)")

# (report key, header) for each table, in display order.
BUCKET_METRICS = (("n_cm", "n"), ("mae", "MAE cm"), ("bias", "sesgo cm"),
                  ("pod", "POD"), ("far", "FAR"), ("csi", "CSI"))
DAY_METRICS = (("n_cm", "n"), ("mae", "MAE cm"), ("pod", "POD"),
               ("far", "FAR"), ("csi", "CSI"))


def column_label(column: str) -> str:
    return COLUMN_LABELS.get(column, column.replace("_", " "))


def _fmt(value) -> str:
    """None → em dash (missing, never 0); floats to 2 dp; ints as-is."""
    if value is None:
        return "—"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def _metric_cells(metrics: dict | None, live: dict | None, key: str) -> str:
    base_v = metrics.get(key) if metrics else None
    live_v = live.get(key) if live else None
    return f'<td>{_fmt(base_v)}</td><td class="live">{_fmt(live_v)}</td>'


def is_candidate(column: str) -> bool:
    """True for columns that exist only to be gated, not to be read by a skier.

    S1.3 writes an OPG-corrected variant beside every affected column so the
    go/no-go can score them side by side (`python -m minipirineu.opg gate`).
    Those rows stay out of the public page: they have no frozen-baseline
    counterpart, so every "base" cell would be an em dash, and publishing an
    ungated candidate is what ADR-0003 exists to prevent. They remain in the
    machine report and in the store.
    """
    return opg.base_column(column) is not None


def metric_table(title: str, metrics, base_by_col: dict, live_by_col: dict) -> str:
    """One table: a row per column, each metric as a (baseline, live) pair."""
    columns = sorted(c for c in set(base_by_col) | set(live_by_col) if not is_candidate(c))
    if not columns:
        return (f'<section><h2>{html.escape(title)}</h2>'
                '<p class="placeholder">Sin columnas todavía.</p></section>')
    head = "".join(f'<th colspan="2">{html.escape(label)}</th>' for _, label in metrics)
    sub = "".join('<th>base</th><th class="live">live</th>' for _ in metrics)
    rows = []
    for col in columns:
        cells = "".join(
            _metric_cells(base_by_col.get(col), live_by_col.get(col), key)
            for key, _ in metrics
        )
        rows.append(f'<tr><td class="col">{html.escape(column_label(col))}</td>{cells}</tr>')
    return (
        f'<section><h2>{html.escape(title)}</h2><div class="table-wrap"><table>'
        f'<thead><tr><th rowspan="2" class="col">Columna</th>{head}</tr>'
        f'<tr>{sub}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _by_column(report: dict | None, section: str) -> dict:
    """A report's `<section>.by_column` map, or {} when the report is absent."""
    return ((report or {}).get(section, {}) or {}).get("by_column", {}) or {}


def render_verification_page(
    baseline: dict | None,
    live: dict | None,
    now_utc: datetime,
    template_text: str,
    *,
    baseline_ref: str = "",
) -> str:
    """Baseline + live verify reports → the full HTML page. Either may be None;
    a None side renders as "—" so a fresh deploy with no live data still shows
    the frozen baseline (missing is missing, never 0)."""
    tables = "\n".join((
        metric_table("6 h por bucket",
                     BUCKET_METRICS, _by_column(baseline, "bucket_6h"), _by_column(live, "bucket_6h")),
        metric_table("Días de nieve (24 h ≥ 1 cm)",
                     DAY_METRICS, _by_column(baseline, "snow_day_24h"), _by_column(live, "snow_day_24h")),
    ))
    base_label = (f'{baseline["n_pairs"]} pares · {html.escape(baseline_ref)}'
                  if baseline else "sin baseline")
    live_label = (f'{live["n_pairs"]} pares' if live else "sin datos live todavía")
    return Template(template_text).substitute(
        tables_html=tables,
        baseline_label=base_label,
        live_label=live_label,
        generated_label=now_utc.strftime("%Y-%m-%d %H:%M UTC"),
    )


def _load(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def latest_baseline(baseline_dir: Path = BASELINE_DIR) -> Path | None:
    found = sorted(baseline_dir.glob("baseline-*.json"))
    return found[-1] if found else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the verification page (T11).")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="frozen baseline JSON (default: newest docs/verification/baseline-*.json)")
    ap.add_argument("--live", type=Path, default=None,
                    help="trailing live report JSON (optional; absent → live shown as missing)")
    ap.add_argument("--window", default="pooled",
                    help="which baseline window to show (default: pooled)")
    ap.add_argument("--site-dir", type=Path, default=SITE_DIR)
    args = ap.parse_args(argv)

    baseline_path = args.baseline or latest_baseline()
    baseline_doc = _load(baseline_path)
    # the baseline JSON nests reports per window; the page shows one window
    baseline_report = None
    if baseline_doc:
        baseline_report = baseline_doc.get("windows", {}).get(args.window, {}).get("verify")
    baseline_ref = baseline_path.name if baseline_path else ""

    page = render_verification_page(
        baseline_report, _load(args.live), datetime.now(timezone.utc),
        TEMPLATE_PATH.read_text(encoding="utf-8"), baseline_ref=baseline_ref,
    )
    args.site_dir.mkdir(parents=True, exist_ok=True)
    (args.site_dir / "verificacion.html").write_text(page, encoding="utf-8")
    if STYLE_PATH.exists():
        shutil.copy(STYLE_PATH, args.site_dir / "style.css")
    print(f"wrote {args.site_dir}/verificacion.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
