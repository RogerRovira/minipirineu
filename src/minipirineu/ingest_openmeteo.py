"""CLI: fetch AROME forecasts for all stations × bands → data/openmeteo.json.

Failure contract (feature 4 of the brief): any error — network, schema
surprise, validation — leaves the previous JSON untouched and exits
non-zero, so stale data stays visibly stale instead of being replaced by
garbage or half-written files.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from minipirineu import aggregate, openmeteo
from minipirineu.config import MODELS, STATIONS, TIMEZONE

SCHEMA = "minipirineu/openmeteo/v1"
DEFAULT_OUT = Path("data/openmeteo.json")

MAX_SNOWFALL_CM_6H = 200
MAX_PRECIPITATION_MM_6H = 300
TEMPERATURE_RANGE_C = (-45, 45)


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def snowfall_series(spec, series: dict) -> list:
    """Pick the model's snowfall: native output, or derived from precip + temp.

    A "native" model whose snowfall comes back all-null means Open-Meteo
    changed what it serves — fail loudly rather than quietly derive.
    """
    if spec.snowfall_source == "native":
        if not any(v is not None for v in series["snowfall_cm"]):
            raise ValueError(f"{spec.id}: native snowfall is all null")
        return series["snowfall_cm"]
    return aggregate.derive_snowfall(series["precipitation_mm"], series["temperature_c"])


def build_snapshot(session: requests.Session, now_local: datetime) -> dict:
    stations_out = []
    for station in STATIONS:
        bands_out = []
        for band, elevation_m in station.bands:
            raw = openmeteo.fetch(session, station, elevation_m)
            parsed = openmeteo.parse_response(raw)
            models_out = []
            for spec in MODELS:
                series = parsed["models"][spec.id]
                agg = aggregate.to_buckets(
                    parsed["time"],
                    snowfall_series(spec, series),
                    series["precipitation_mm"],
                    series["temperature_c"],
                    now_local,
                )
                models_out.append(
                    {
                        "model": spec.id,
                        "label": spec.label,
                        "snowfall_source": spec.snowfall_source,
                        **agg,
                    }
                )
            bands_out.append(
                {
                    "band": band,
                    "elevation_m": elevation_m,
                    "grid_elevation_m": parsed["grid_elevation_m"],
                    "models": models_out,
                }
            )
        stations_out.append(
            {
                "id": station.id,
                "name": station.name,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "bands": bands_out,
            }
        )
    return {
        "schema": SCHEMA,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": TIMEZONE,
        "stations": stations_out,
    }


def validate(snapshot: dict) -> None:
    """Reject malformed snapshots before they can replace last-good data."""
    stations = snapshot["stations"]
    if len(stations) != len(STATIONS):
        raise ValueError(f"expected {len(STATIONS)} stations, got {len(stations)}")
    for station in stations:
        expected_bands = dict(next(s.bands for s in STATIONS if s.id == station["id"]))
        if [b["band"] for b in station["bands"]] != list(expected_bands):
            raise ValueError(f"{station['id']}: unexpected bands")
        for band in station["bands"]:
            if len(band["models"]) != len(MODELS):
                raise ValueError(f"{station['id']}/{band['band']}: missing models")
            for model in band["models"]:
                where = f"{station['id']}/{band['band']}/{model['model']}"
                if not model["intervals"]:
                    raise ValueError(f"{where}: no intervals")
                for iv in model["intervals"]:
                    snow, precip, temp = (
                        iv["snowfall_cm"],
                        iv["precipitation_mm"],
                        iv["temperature_c"],
                    )
                    if snow is not None and not 0 <= snow <= MAX_SNOWFALL_CM_6H:
                        raise ValueError(f"{where}: snowfall_cm out of range: {snow}")
                    if precip is not None and not 0 <= precip <= MAX_PRECIPITATION_MM_6H:
                        raise ValueError(f"{where}: precipitation_mm out of range: {precip}")
                    if temp is not None and not (
                        TEMPERATURE_RANGE_C[0] <= temp <= TEMPERATURE_RANGE_C[1]
                    ):
                        raise ValueError(f"{where}: temperature_c out of range: {temp}")


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main(out_path: Path = DEFAULT_OUT) -> int:
    now_local = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    try:
        with make_session() as session:
            snapshot = build_snapshot(session, now_local)
        validate(snapshot)
    except Exception as exc:
        print(f"openmeteo ingest FAILED, keeping previous {out_path}: {exc}", file=sys.stderr)
        return 1
    atomic_write_json(out_path, snapshot)
    n_calls = sum(len(s.bands) for s in STATIONS)
    print(f"wrote {out_path} ({n_calls} API calls, fetched_at {snapshot['fetched_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT))
