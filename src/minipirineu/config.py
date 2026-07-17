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
    # Gated (ROADMAP S2.3): inter-family contrast columns hidden behind
    # ?modelos=todos until a scored winter month shows MAE ≤ AROME 2.5's.
    # Fetched in a separate request and allowed to degrade to "unavailable"
    # so an experimental column can never take down the AROME data.
    gated: bool = False


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

# Models served by Open-Meteo, in display order: first the default AROME
# pair (the brief requires requesting these explicitly — never best_match),
# then the gated inter-family columns (S2.3), all with native snowfall,
# probed live over Baqueira on 2026-07-18 (docs/notes/gated-model-columns.md).
# "ecmwf_ifs" is the 9 km IFS HRES — the exact model the brief's deferred
# column asked for — not the 25 km ecmwf_ifs025.
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("meteofrance_arome_france_hd", "AROME HD 1.3 km", snowfall_source="derived"),
    ModelSpec("meteofrance_arome_france", "AROME 2.5 km", snowfall_source="native"),
    ModelSpec("knmi_harmonie_arome_europe", "HARMONIE KNMI", snowfall_source="native", gated=True),
    ModelSpec("dmi_harmonie_arome_europe", "HARMONIE DMI", snowfall_source="native", gated=True),
    ModelSpec("ecmwf_ifs", "IFS 9 km (ECMWF)", snowfall_source="native", gated=True),
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

# Station -> Meteocat pronostic Pirineu zone id (the endpoint's OWN 7-zone
# scheme; ids 1,3,4,5,6,7,8 — NOT the map's "Zona 1..7" numbering).
# CONFIRMED 2026-07-17: the user double-checked the official zone map; see
# docs/notes/meteocat-pronostic-semantics.md. All zones are archived anyway,
# so a revision would need no re-ingest.
METEOCAT_ZONE_BY_STATION: dict[str, int] = {
    "baqueira": 1,   # Vessant nord Pirineu occidental
    "boi-taull": 5,  # Vessant sud Pirineu occidental
    "la-molina": 6,  # Prepirineu oriental
}


@dataclass(frozen=True)
class MeteocatAnchor:
    """A pronostic pics/refugis forecast point whose isozero and upper winds
    track a resort's massif. Primaries are the resort peaks themselves;
    secondaries add isozero coverage for winter verification. Fetched ONE per
    day on a quota rotation (Predicció plan: 100 calls/month)."""

    codi: str
    station_id: str  # which resort's massif this anchor tracks
    name: str        # storage station name for verification rows
    primary: bool


# Selection ported from PiriNeu (2026-07-13 thermal-anchor selection).
METEOCAT_ANCHORS: tuple[MeteocatAnchor, ...] = (
    MeteocatAnchor("77954ad7", "baqueira", "baqueira", True),       # Cap de Vaquèira
    MeteocatAnchor("962535ca", "baqueira", "marimanya", False),     # Tuc de Marimanya
    MeteocatAnchor("b65b37e8", "baqueira", "airoto", False),        # Airoto
    MeteocatAnchor("8245e5c9", "baqueira", "gerdar", False),        # Refugi del Gerdar
    MeteocatAnchor("246d5775", "boi-taull", "boi_taull", True),     # Pica de Cerví
    MeteocatAnchor("6e5cedc5", "boi-taull", "filia", False),        # Pic de Filià
    MeteocatAnchor("a4d20c1f", "boi-taull", "corronco", False),     # Lo Corronco
    MeteocatAnchor("4d04de5e", "la-molina", "la_molina", True),     # La Tosa d'Alp
    MeteocatAnchor("5bb98db1", "la-molina", "puigllancada", False), # Puigllançada
    MeteocatAnchor("a9f7eb3a", "la-molina", "pere_carne", False),   # Refugi Pere Carné
)

# Canonical pics-metadades coordinates for the PRIMARY anchors (fetched
# 2026-07-12 in PiriNeu, re-confirmed on the 2026-07-17 fixtures). Ingest
# alerts if the metadades ever drift from these (a silent renumbering would
# corrupt the anchor->resort assignment).
METEOCAT_ANCHOR_COORDS: dict[str, tuple[float, float]] = {
    "77954ad7": (42.6918885, 0.9742379),  # Cap de Vaquèira
    "246d5775": (42.4529432, 0.8792261),  # Pica de Cerví
    "4d04de5e": (42.3205751, 1.8926609),  # La Tosa d'Alp
}
