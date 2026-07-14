"""Project constants: stations, elevation bands, models, freshness thresholds.

Band elevations per station are confirmed in the project brief
(MiniPrevi_PiriNeu.md); do not change them without updating the brief.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    latitude: float
    longitude: float
    # (band name, reference elevation in m), ordered low to high
    bands: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    # "native": the model serves the snowfall variable directly.
    # "derived": snowfall estimated from precipitation + band temperature
    #   (AROME HD serves no snowfall in any form — validated 2026-07,
    #   see docs/notes/snowfall-semantics.md).
    snowfall_source: str


STATIONS: tuple[Station, ...] = (
    Station(
        id="baqueira",
        name="Baqueira",
        latitude=42.698,
        longitude=0.931,
        bands=(("baja", 1500), ("media", 2000), ("alta", 2600)),
    ),
    Station(
        id="boi-taull",
        name="Boí Taüll",
        latitude=42.470,
        longitude=0.885,
        bands=(("baja", 2000), ("media", 2400), ("alta", 2750)),
    ),
    Station(
        id="la-molina",
        name="La Molina",
        latitude=42.337,
        longitude=1.948,
        bands=(("baja", 1700), ("media", 2100), ("alta", 2500)),
    ),
)

# AROME models served by Open-Meteo, in display order. The brief requires
# requesting these explicitly (never best_match).
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("meteofrance_arome_france_hd", "AROME HD 1.3 km", snowfall_source="derived"),
    ModelSpec("meteofrance_arome_france", "AROME 2.5 km", snowfall_source="native"),
)

# Derived snowfall (models without native snowfall): cm of snow per mm of
# hourly precipitation as a function of band temperature. Fitted against
# AROME 2.5's native snowfall over Dec 2025 – Feb 2026 at four station/band
# combos (docs/notes/snowfall-semantics.md): full ratio when cold, linear
# taper to zero through the mixed rain/snow range. A flat 0.7 cm/mm below
# +1 °C — the naive 7:1 rule — overestimated totals by ~55%.
DERIVED_SNOW_RATIO_MAX = 0.45  # cm snow per mm water at/below T_FULL
DERIVED_SNOW_T_FULL_C = -2.0
DERIVED_SNOW_T_ZERO_C = 1.0

TIMEZONE = "Europe/Madrid"
FORECAST_HOURS = 48
BUCKET_HOURS = 6

# Hours since fetched_at after which a source's data must look stale.
# Open-Meteo refreshes every 6h (one missed run + slack); Meteocat twice a day.
STALE_AFTER_H = {"openmeteo": 7, "meteocat": 26}

# Station -> Meteocat "predicció de muntanya" zone. Zone codes are an open
# question in the brief, to be confirmed in milestone 3.
METEOCAT_ZONE_BY_STATION: dict[str, str | None] = {
    "baqueira": None,  # expected: Aran - Franja Nord
    "boi-taull": None,
    "la-molina": None,
}
