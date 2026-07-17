"""One-off: record real Meteocat pronostic payloads as test fixtures (T0.3).

Run locally in a shell where the API key is set (either variable name works):

    $env:METEOCAT_API_KEY = "..."   # or $env:API_KEY
    python scripts/record_meteocat_fixtures.py

Exactly 5 quota calls on a fresh run (Predicció plan, 100/month): pics + refugis
metadades, zones for today + tomorrow, and one pic (Cap de Vaquèira, resolved
from the metadades). Files that already exist are NOT refetched, so re-running
after a partial failure only burns the missing calls. Payload bytes are saved
exactly as served — fixtures must be byte-faithful, never reformatted.

Also attempts the official symbols catalog (simbols.json — code→label mapping
for cel/tempesta/…). That endpoint belongs to the separate "Referència" plan;
if the key isn't subscribed to it the script just warns and finishes fine.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from minipirineu.envfile import load_env

BASE = "https://api.meteo.cat/pronostic/v1"
SIMBOLS_URL = "https://api.meteo.cat/referencia/v1/simbols"  # Referència plan
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "meteocat"
BAQUEIRA_PIC_CODI = "77954ad7"  # Cap de Vaquèira (PiriNeu METEOCAT_ANCHORS)
TIMEZONE = "Europe/Madrid"

calls_made = 0


def api_key() -> str:
    load_env()  # local .env (gitignored); real env vars win
    key = os.environ.get("METEOCAT_API_KEY") or os.environ.get("API_KEY") or ""
    if not key or "your_actual" in key.lower():
        sys.exit("set METEOCAT_API_KEY (or API_KEY) to the real Meteocat key first")
    return key


def fetch(session: requests.Session, path: str) -> bytes:
    global calls_made
    resp = session.get(f"{BASE}{path}", timeout=30)
    calls_made += 1
    if resp.status_code != 200:
        sys.exit(f"HTTP {resp.status_code} for {path}: {resp.text[:200]}")
    return resp.content


def record(session: requests.Session, name: str, path: str) -> bytes:
    """Fetch BASE+path into fixtures/<name> unless it already exists."""
    target = FIXTURES_DIR / name
    if target.exists():
        print(f"  {name}: already recorded, skipping (0 calls)")
        return target.read_bytes()
    body = fetch(session, path)
    target.write_bytes(body)
    print(f"  {name}: {len(body)} bytes (1 call)")
    return body


def pic_slug(metadades: bytes) -> str:
    for item in json.loads(metadades):
        if item.get("codi") == BAQUEIRA_PIC_CODI:
            return item["slug"]
    sys.exit(f"codi {BAQUEIRA_PIC_CODI} missing from pics metadades — check the codi")


def date_path(day: datetime) -> str:
    return f"{day.year}/{day.month:02d}/{day.day:02d}"


def record_simbols(session: requests.Session) -> None:
    """Best-effort: the Referència plan may not be on this key (then: 403)."""
    target = FIXTURES_DIR / "simbols.json"
    if target.exists():
        print("  simbols.json: already recorded, skipping (0 calls)")
        return
    resp = session.get(SIMBOLS_URL, timeout=30)
    if resp.status_code != 200:
        print(f"  simbols.json: HTTP {resp.status_code} — key not subscribed to "
              f"the Referència plan? Skipped (labels stay as raw codes).")
        return
    target.write_bytes(resp.content)
    print(f"  simbols.json: {len(resp.content)} bytes (1 call, Referència plan)")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo(TIMEZONE))
    with requests.Session() as session:
        session.headers["X-Api-Key"] = api_key()
        pics_meta = record(session, "pics_metadades.json", "/pirineu/pics/metadades")
        record(session, "refugis_metadades.json", "/pirineu/refugis/metadades")
        record(session, "zones_today.json", f"/pirineu/{date_path(today)}")
        record(session, "zones_tomorrow.json",
               f"/pirineu/{date_path(today + timedelta(days=1))}")
        slug = pic_slug(pics_meta)
        record(session, "pic_baqueira.json",
               f"/pirineu/pics/{slug}/{date_path(today)}")
        record_simbols(session)
    print(f"done: {calls_made} Predicció quota calls used, fixtures in {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
