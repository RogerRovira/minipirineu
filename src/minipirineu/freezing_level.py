"""Freezing level (isozero) derived from pressure-level temperatures.

No Météo-France model serves freezing_level_height via Open-Meteo (validated
M1), so the isozero is DERIVED: scan the pressure levels ground -> up and
linearly interpolate the altitude of the 0 °C crossing, using the model's own
geopotential heights. Ported from PiriNeu freezing_level.py with the edge-case
semantics UNCHANGED (hard-won, do not "fix"):

- whole column above 0 °C  -> None (no freezing level in range);
- whole column below 0 °C  -> lowest level's height, capped=True;
- inversions (several crossings) -> the LOWEST crossing, conservative for
  snow-line purposes; n_crossings records the inversion — and is PERSISTED
  to the store (the one fix on port: PiriNeu discarded it).

A diagnostic for verification only (stage-0 gate): never rendered.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from minipirineu import openmeteo
from minipirineu.config import TIMEZONE
from minipirineu.store import Row

AROME_25 = "meteofrance_arome_france"  # the only model serving pressure levels
_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class FreezingLevel:
    height_m: Optional[float]   # metres above sea level
    capped: bool = False        # True when the whole column was below 0 °C
    n_crossings: int = 0        # >1 signals an inversion was present


def derive_freezing_level(temps: Sequence[Optional[float]],
                          heights: Sequence[Optional[float]]) -> FreezingLevel:
    """temps/heights ordered ground -> up along openmeteo.PRESSURE_LEVELS."""
    pairs = [(t, h) for t, h in zip(temps, heights)
             if t is not None and h is not None]
    if len(pairs) < 2:
        return FreezingLevel(None)
    if all(t <= 0 for t, _ in pairs):
        # freezing level at/below the lowest model level: cap at terrain
        return FreezingLevel(pairs[0][1], capped=True)
    if all(t > 0 for t, _ in pairs):
        return FreezingLevel(None)
    crossings = []
    for (t_lo, h_lo), (t_hi, h_hi) in zip(pairs, pairs[1:]):
        if (t_lo > 0) != (t_hi > 0) and t_lo != t_hi:
            frac = t_lo / (t_lo - t_hi)
            crossings.append(h_lo + frac * (h_hi - h_lo))
    if not crossings:
        return FreezingLevel(None)
    return FreezingLevel(min(crossings), n_crossings=len(crossings))


def _to_utc(local_iso: str) -> str:
    local = datetime.fromisoformat(local_iso).replace(tzinfo=ZoneInfo(TIMEZONE))
    return local.astimezone(timezone.utc).strftime(_ISO_UTC)


def freezing_rows(station_id: str, raw: dict, run_time_utc: str) -> list[Row]:
    """Store rows (height, capped, n_crossings) per hour with a derived level.

    Hours without a result (warm column, missing levels, HD's all-null
    profile) yield no rows — missing stays missing. Times arrive local
    (Europe/Madrid, the API's timezone param) and are stored UTC.
    """
    rows: list[Row] = []
    profiles = openmeteo.pressure_profiles(raw, AROME_25)
    for local_iso, (temps, heights) in zip(raw["hourly"]["time"], profiles):
        level = derive_freezing_level(temps, heights)
        if level.height_m is None:
            continue
        valid = _to_utc(local_iso)
        rows.append(Row("openmeteo", station_id, run_time_utc, valid,
                        "derived.freezing_level_m", level.height_m))
        rows.append(Row("openmeteo", station_id, run_time_utc, valid,
                        "derived.freezing_level_capped", float(level.capped)))
        rows.append(Row("openmeteo", station_id, run_time_utc, valid,
                        "derived.freezing_level_n_crossings", float(level.n_crossings)))
    return rows
