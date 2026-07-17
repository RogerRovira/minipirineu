"""CLI: Meteocat pronostic → datastore archive + store + data/meteocat.json.

Per effective run (one per day): zones for today + tomorrow (2 calls) and ONE
pics/refugis anchor on a date-keyed rotation (1 call); metadades only when the
archived copy is >30 days old (~2 calls/month). Total ≈ 95 of the Predicció
plan's 100 calls/month — the second daily cron slot is a pure retry: is_fresh
makes it a no-op when the first slot succeeded.

Archive-before-parse (ADR-0002): every payload is written raw to the
datastore BEFORE json.loads — the pronostic is a 3-day rolling window with no
upstream archive, so a parser bug must never lose data. Failure contract as
ingest_openmeteo: any error keeps the previous JSON and exits non-zero.
Anchors and metadades feed archive + store only; nothing of them renders.
"""

import gzip
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from minipirineu import meteocat, store
from minipirineu.archive import Archive, run_time_from_path
from minipirineu.config import (
    METEOCAT_ANCHORS,
    METEOCAT_ZONE_BY_STATION,
    STATIONS,
    TIMEZONE,
)
from minipirineu.envfile import load_env
from minipirineu.ingest_openmeteo import atomic_write_json
from minipirineu.meteocat_labels import ZONE_DISPLAY_NAMES

BASE = "https://api.meteo.cat/pronostic/v1"
SCHEMA = "minipirineu/meteocat/v1"
DEFAULT_OUT = Path("data/meteocat.json")
METADADES_MAX_AGE = timedelta(days=30)
# Above the ~6h gap between the two cron slots, below the ~18h overnight gap.
SKIP_IF_FRESHER_H = 8

_PRIMARIES = tuple(a for a in METEOCAT_ANCHORS if a.primary)
_SECONDARIES = tuple(a for a in METEOCAT_ANCHORS if not a.primary)
_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


class Ctx(NamedTuple):
    session: requests.Session
    archive: Archive
    conn: object  # sqlite3.Connection


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def make_session() -> requests.Session:
    key = os.environ.get("METEOCAT_API_KEY")
    if not key:
        raise RuntimeError("METEOCAT_API_KEY is not set (.env locally, secret in CI)")
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["X-Api-Key"] = key
    return session


def fetch(session: requests.Session, path: str) -> bytes:
    resp = session.get(f"{BASE}{path}", timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for {path}")
    return resp.content


def anchor_for_date(day: date):
    """The day's single anchor, keyed on the date: same-day retries hit the
    same anchor, and a dropped day only loses its own slot. Even ordinals walk
    the 3 primaries (each every 6 days), odd ordinals the 7 secondaries (each
    every 14 days) — 1 call/day total."""
    ordinal = day.toordinal()
    pool = _PRIMARIES if ordinal % 2 == 0 else _SECONDARIES
    return pool[(ordinal // 2) % len(pool)]


def _date_path(day: date) -> str:
    return f"{day.year}/{day.month:02d}/{day.day:02d}"


def _latest_archived(archive: Archive, name: str) -> tuple[bytes, datetime] | None:
    paths = sorted((archive.root / "raw" / "meteocat").rglob(f"*_{name}.gz"))
    if not paths:
        return None
    fetched = datetime.fromisoformat(run_time_from_path(paths[-1]))
    with gzip.open(paths[-1], "rb") as f:
        return f.read(), fetched


def load_or_refresh_metadades(session, archive: Archive, now_utc: datetime) -> dict:
    """codi -> {slug, kind, …} for the anchors; hits the API (2 calls) only
    when the archived copy is missing or older than 30 days."""
    bodies: dict[str, bytes] = {}
    for kind in ("pics", "refugis"):
        latest = _latest_archived(archive, f"{kind}_metadades.json")
        if latest is None or now_utc - latest[1] > METADADES_MAX_AGE:
            raw = fetch(session, f"/pirineu/{kind}/metadades")
            archive.store("meteocat", f"{kind}_metadades.json", raw, fetched_at=now_utc)
            bodies[kind] = raw
        else:
            bodies[kind] = latest[0]
    mapping: dict[str, dict] = {}
    for kind, raw in bodies.items():
        mapping.update(meteocat.parse_metadades(json.loads(raw), kind))
    for codi in sorted(meteocat.missing_anchors(mapping)):
        warn(f"anchor codi {codi} missing from metadades")
    for problem in meteocat.coord_drift(mapping):
        warn(problem)
    return mapping


def _ingest_zones(ctx: Ctx, day: date, now_utc: datetime) -> tuple[str, dict]:
    date_str = day.isoformat()
    raw = fetch(ctx.session, f"/pirineu/{_date_path(day)}")
    ctx.archive.store("meteocat", f"zones_{date_str}.json", raw, fetched_at=now_utc)
    body = json.loads(raw)
    for problem in meteocat.check_zone_names(body):
        warn(problem)
    rows = meteocat.parse_zones(body, now_utc.strftime(_ISO_UTC), date_str)
    store.upsert_rows(ctx.conn, rows)
    return date_str, body


def _ingest_anchor(ctx: Ctx, mapping: dict, now_utc: datetime) -> None:
    today = now_utc.astimezone(ZoneInfo(TIMEZONE)).date()
    anchor = anchor_for_date(today)
    info = mapping.get(anchor.codi)
    if info is None or not info.get("slug"):
        warn(f"anchor {anchor.name} ({anchor.codi}) unresolved; skipping its slot")
        return
    raw = fetch(ctx.session, f"/pirineu/{info['kind']}/{info['slug']}/{_date_path(today)}")
    ctx.archive.store("meteocat", f"pic_{anchor.name}.json", raw, fetched_at=now_utc)
    rows = meteocat.parse_pic(json.loads(raw), now_utc.strftime(_ISO_UTC), anchor.name)
    store.upsert_rows(ctx.conn, rows)


def _as_code(value) -> int | None:
    return None if value is None else int(value)


def _zone_stations() -> dict[int, list[str]]:
    names = {s.id: s.name for s in STATIONS}
    zones: dict[int, list[str]] = {}
    for station_id, zone_id in METEOCAT_ZONE_BY_STATION.items():
        zones.setdefault(zone_id, []).append(names[station_id])
    return zones


def _day_entry(body: dict, zone_id: int, date_str: str) -> dict:
    day_fields = meteocat.zone_day_fields(body, zone_id)
    blocks = [
        {"start": start, "cel": _as_code(fields.get("cel")),
         "probabilitat": _as_code(fields.get("probabilitat")),
         "cota_m": fields.get("cota")}
        for start, fields in sorted(meteocat.zone_block_fields(body, zone_id).items())
    ]
    return {"date": date_str, "blocks": blocks,
            "acumulacio": _as_code(day_fields.get("acumulacio")),
            "acumulacio_neu": _as_code(day_fields.get("acumulacioNeu"))}


def build_snapshot(days: list[tuple[str, dict]], fetched_at: str) -> dict:
    zones = [
        {"zone_id": zone_id,
         "zone_name": ZONE_DISPLAY_NAMES.get(zone_id, f"zona {zone_id}"),
         "stations": stations,
         "days": [_day_entry(body, zone_id, date_str) for date_str, body in days]}
        for zone_id, stations in sorted(_zone_stations().items())
    ]
    return {"schema": SCHEMA, "fetched_at": fetched_at, "zones": zones}


def validate(snapshot: dict) -> None:
    """Reject malformed snapshots before they can replace last-good data."""
    expected = sorted(set(METEOCAT_ZONE_BY_STATION.values()))
    if [z["zone_id"] for z in snapshot["zones"]] != expected:
        raise ValueError(f"expected zones {expected}, got {snapshot['zones']!r:.120}")
    for zone in snapshot["zones"]:
        for day in zone["days"]:
            if not day["blocks"]:
                raise ValueError(f"zone {zone['zone_id']} {day['date']}: no 6h blocks")


def is_fresh(path: Path, now_utc: datetime) -> bool:
    """True when the last run is recent enough that this slot is a retry, not
    a second daily fetch — the quota can't afford running twice."""
    try:
        fetched = datetime.fromisoformat(json.loads(path.read_text())["fetched_at"])
        return now_utc - fetched < timedelta(hours=SKIP_IF_FRESHER_H)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _ingest(session, archive: Archive, now_utc: datetime) -> dict:
    ctx = Ctx(session, archive, store.connect(archive.root / "verification.sqlite"))
    today = now_utc.astimezone(ZoneInfo(TIMEZONE)).date()
    days = [_ingest_zones(ctx, today + timedelta(days=n), now_utc) for n in (0, 1)]
    mapping = load_or_refresh_metadades(session, archive, now_utc)
    _ingest_anchor(ctx, mapping, now_utc)
    return build_snapshot(days, now_utc.isoformat(timespec="seconds"))


def main(out_path: Path = DEFAULT_OUT, now_utc: datetime | None = None,
         force: bool = False) -> int:
    load_env()
    now_utc = now_utc or datetime.now(timezone.utc)
    if not force and is_fresh(out_path, now_utc):
        print(f"{out_path} is <{SKIP_IF_FRESHER_H}h old; skipping (quota retry slot)")
        return 0
    try:
        archive = Archive.from_env()
        with make_session() as session:
            snapshot = _ingest(session, archive, now_utc)
        validate(snapshot)
    except Exception as exc:
        print(f"meteocat ingest FAILED, keeping previous {out_path}: {exc}", file=sys.stderr)
        return 1
    atomic_write_json(out_path, snapshot)
    print(f"wrote {out_path} (fetched_at {snapshot['fetched_at']})")
    return 0


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if a != "--force"]
    sys.exit(main(Path(_args[0]) if _args else DEFAULT_OUT,
                  force="--force" in sys.argv[1:]))
