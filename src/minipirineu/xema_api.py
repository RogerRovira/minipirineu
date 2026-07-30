"""XEMA API (meteo.cat) near-real-time observations: client + pure parser (S0.8/T11).

The Socrata open data (`xema_opendata.py`) is the historical truth; this is the
near-real-time feed for freshness and Stage 2 obs-anchoring. Readings carry their
own UTC timestamps, so re-fetching a day is idempotent — an observation's run IS
its reading instant (readings-as-run_time: run_time == valid_time). A later
validated value simply upserts over an earlier provisional one. Archive-before-
parse (ADR-0002): the caller archives raw bytes before any decode.

Endpoint (X-Api-Key auth), one call per (variable, day):

    /xema/v1/variables/mesurades/{var}/{Y}/{M}/{D}[?codiEstacio={codi}]

Two response shapes, both handled — the values are identical reading-for-reading
to the open data (pinned by scripts/record_xema_parity.py):

- all-stations (no codiEstacio, the quota-efficient live path):
    [{"codi": <station>, "variables": [{"codi": <var>, "lectures": [...]}]}, ...]
- one-station (codiEstacio, the parity fixtures):
    {"codi": <var>, "lectures": [...]}

Each `lectura` is {"data": ISO-UTC, "valor", "estat", "baseHoraria"}; `data` may
omit seconds (e.g. "2026-02-01T00:00Z"), which _norm_stamp canonicalises.
"""

import json
import os
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from minipirineu.config import XEMA_VARIABLES
from minipirineu.store import Row

SOURCE = "xema"  # a reading is a reading — same store source as the open data,
#                  so truth.py / verify.py read both without knowing the origin.
BASE = "https://api.meteo.cat/xema/v1"


def make_session() -> requests.Session:
    key = os.environ.get("METEOCAT_API_KEY")
    if not key:
        raise RuntimeError("METEOCAT_API_KEY is not set (.env locally, secret in CI)")
    session = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["X-Api-Key"] = key
    return session


def build_url(var: str, day) -> str:
    return f"{BASE}/variables/mesurades/{var}/{day:%Y}/{day:%m}/{day:%d}"


def fetch(session: requests.Session, var: str, day, codi: str | None = None,
          timeout: int = 60) -> bytes:
    """Raw response BYTES for one variable-day. `codi` filters to one station
    (parity checks); omit it for the all-stations live ingest. The caller
    archives the bytes byte-faithful (ADR-0002) before any json.loads."""
    params = {"codiEstacio": codi} if codi else None
    resp = session.get(build_url(var, day), params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.content


# --- parsing ----------------------------------------------------------------

def _norm_stamp(data: str) -> str:
    """API `data` → the store's `%Y-%m-%dT%H:%M:%SZ` UTC form. The API drops
    seconds ("2026-02-01T00:00Z"); this restores them so the stamp is
    byte-identical to the open-data timestamps and pairs on the same instant."""
    dt = datetime.fromisoformat(data.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _var_rows(station: str, var_code, lectures) -> list[Row]:
    """One station+variable's `lectures` → obs Rows. A variable we don't map is
    skipped; a reading with no `valor` is missing (never a fabricated 0)."""
    slug = XEMA_VARIABLES.get(str(var_code))
    if slug is None:
        return []
    variable = f"obs.{slug}"
    rows: list[Row] = []
    for lect in lectures:
        valor = lect.get("valor")
        if valor is None:
            continue
        ts = _norm_stamp(lect["data"])
        rows.append(Row(SOURCE, station, ts, ts, variable, float(valor)))
    return rows


def parse_all_stations(raw: bytes, target_codis) -> list[Row]:
    """All-stations payload (a list) → obs Rows for the stations in
    `target_codis` only (the network returns ~all XEMA EMAs; we keep ours)."""
    targets = set(target_codis)
    rows: list[Row] = []
    for entry in json.loads(raw):
        station = entry.get("codi")
        if station not in targets:
            continue
        for var in entry.get("variables", []):
            rows += _var_rows(station, var.get("codi"), var.get("lectures", []))
    return rows


def parse_station(raw: bytes, station: str) -> list[Row]:
    """One-station (codiEstacio) flat payload → obs Rows. The flat body carries
    the variable code, not the station, so the caller names the station it
    requested."""
    body = json.loads(raw)
    return _var_rows(station, body.get("codi"), body.get("lectures", []))
