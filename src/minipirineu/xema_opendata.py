"""Socrata XEMA open-data client (S0.3/T5): SoQL query builder + parser.

Dataset `nzvn-apee` on analisi.transparenciacatalunya.cat — semi-hourly
(`codi_base` = "SH") XEMA readings, 2009→present, quota-free. Two semantics,
both pinned by a live probe on 2026-07-18 (docs/notes/xema-truth-stations.md):

- `data_lectura` is **UTC** — the solar-irradiance (var 36) daily peak lands at
  11:00, matching Catalonia's ~11:56 UTC solar noon (local CEST would peak near
  14:00). Stored normalized to an explicit `...Z`.
- Readings are **forward-labeled**: an 11:00 value covers 11:00–11:30. We store
  the label as-is; bucketing (T6/T7) interprets it. So the observation's
  reading time is both its run and valid time in the store.

Everything except `fetch_page()` is a pure function over decoded JSON, so the
parser is testable against recorded fixtures without network access.
"""

import json
import math
import os

import requests

from minipirineu.config import XEMA_VARIABLES
from minipirineu.store import Row

DATASET = "nzvn-apee"
SOQL_URL = f"https://analisi.transparenciacatalunya.cat/resource/{DATASET}.json"
SOURCE = "xema"
BASE_SEMIHOURLY = "SH"  # the semi-hourly base; see build_query
# Socrata caps a page at 50000 rows; an app token only raises throttling limits.
PAGE_LIMIT = 50000
APP_TOKEN_ENV = "SOCRATA_APP_TOKEN"

_SELECT = "codi_estacio,codi_variable,data_lectura,valor_lectura,codi_base"
# Deterministic total order so $offset paging can't skip or repeat a row.
_ORDER = "data_lectura,codi_estacio,codi_variable,codi_base"


def _in_list(values) -> str:
    """SoQL IN list of quoted literals. Inputs are our own fixed station and
    variable codes (never user input), so simple quoting is safe here."""
    return ",".join(f"'{v}'" for v in values)


def build_query(
    station_codes,
    variable_codes,
    start_iso: str,
    end_iso: str,
    limit: int = PAGE_LIMIT,
    offset: int = 0,
) -> dict:
    """SoQL params for one page of a [start_iso, end_iso) window.

    The window is half-open on purpose: adjacent month chunks share no reading,
    so a backfill can't double-store a boundary timestamp.

    Filtered to the semi-hourly base (`codi_base = 'SH'`): the dataset also
    carries an hourly base (`HO`) and a few corrupt base values, and our store
    key does not include the base — so without this filter two readings for the
    same station/variable/instant could collapse into one. All 12 configured
    stations are SH-only across the full 2009→ history (probe 2026-07-18), so
    this drops nothing real while making the single-row-per-instant contract
    structural instead of coincidental.
    """
    where = (
        f"codi_estacio in ({_in_list(station_codes)}) "
        f"and codi_variable in ({_in_list(variable_codes)}) "
        f"and codi_base = '{BASE_SEMIHOURLY}' "
        f"and data_lectura >= '{start_iso}' and data_lectura < '{end_iso}'"
    )
    return {
        "$select": _SELECT,
        "$where": where,
        "$order": _ORDER,
        "$limit": limit,
        "$offset": offset,
    }


def request_headers() -> dict:
    """A Socrata app token (optional) only lifts anonymous rate limits; the
    data is public. Read from env so CI can inject it without code changes."""
    token = os.environ.get(APP_TOKEN_ENV)
    return {"X-App-Token": token} if token else {}


def fetch_page(session: requests.Session, params: dict, timeout: int = 60) -> bytes:
    """Return the raw response bytes for one page — NOT parsed, so the caller
    can archive them before anything interprets them (ADR-0002)."""
    resp = session.get(SOQL_URL, params=params, headers=request_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.content


def normalize_timestamp(data_lectura: str) -> str:
    """`2026-02-01T12:00:00.000` → `2026-02-01T12:00:00Z` (UTC, forward-labeled).

    Drops Socrata's floating-point milliseconds (always .000 at semi-hourly
    resolution) and marks the zone explicitly, without shifting the label.
    """
    stamp = data_lectura.split(".", 1)[0]
    return f"{stamp}Z"


def _as_float(value) -> float | None:
    """Missing stays missing: blanks and non-numeric readings become None,
    never 0 — a fabricated 0 cm of snow would be a false observation.

    Non-finite values (`"NaN"`, `"inf"`) parse as floats but are not real
    readings and, worse, poison the store: NaN != NaN would break the
    idempotent upsert's value comparison. They collapse to None too.
    """
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_rows(json_rows, variables: dict = XEMA_VARIABLES) -> list[Row]:
    """Decoded open-data rows → store rows (`obs.<slug>`).

    An observation's reading time is stored as both run and valid time (it is
    an observation, not a forecast). Rows whose variable code we don't map are
    skipped defensively, so loosening a query can't inject unnamed variables.
    """
    rows: list[Row] = []
    for obs in json_rows:
        slug = variables.get(str(obs.get("codi_variable")))
        if slug is None:
            continue
        station = obs.get("codi_estacio")
        valid = obs.get("data_lectura")
        if not station or not valid:
            continue  # a row missing its station or timestamp is unusable, not
            #            a crash — one malformed record can't sink a backfill
        ts = normalize_timestamp(valid)
        rows.append(
            Row(
                source=SOURCE,
                station=station,
                run_time_utc=ts,
                valid_time_utc=ts,
                variable=f"obs.{slug}",
                value=_as_float(obs.get("valor_lectura")),
            )
        )
    return rows


def parse_payload(raw: bytes, variables: dict = XEMA_VARIABLES) -> list[Row]:
    """Archive-faithful entry point: decode raw page bytes, then parse."""
    return parse_rows(json.loads(raw), variables)
