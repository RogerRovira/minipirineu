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


# --- XEMA verification truth (S0.3/T5) --------------------------------------
#
# Ground-truth observations for scoring the forecasts, pulled quota-free from
# the Socrata open-data dataset `nzvn-apee` (semi-hourly XEMA readings). The
# station set and every variable/timestamp semantic below were resolved by a
# live probe on 2026-07-18; see docs/notes/xema-truth-stations.md.

# XEMA variable codes → storage slug. Stored as `obs.<slug>`. Var 9 (listed in
# an early roadmap draft) does NOT exist in XEMA's variable metadata and is
# dropped. Snow depth (38) is the fresh-snow truth driver; the rest feed the
# phase/undercatch gates (T6/T7) and the band-temperature check.
XEMA_VARIABLES: dict[str, str] = {
    "30": "vent_velocitat",  # 10 m wind speed [m/s]  (VV10)
    "31": "vent_direccio",   # 10 m wind direction [°] (DV10)
    "50": "vent_ratxa",      # 10 m wind gust [m/s]   (VVx10)
    "34": "pressio",         # atmospheric pressure [hPa] (P)
    "36": "irradiancia",     # global solar irradiance [W/m²] (RS)
    "32": "temperatura",     # air temperature [°C]   (T)
    "33": "humitat",         # relative humidity [%]  (HR)
    "35": "precipitacio",    # precipitation [mm]     (PPT)
    "38": "gruix_neu",       # snow depth on ground [cm] (GNEU) — truth driver
}
XEMA_SNOW_DEPTH_VAR = "38"


@dataclass(frozen=True)
class XemaStation:
    codi: str          # codi_estacio in the open data (its natural key)
    name: str
    altitude_m: int
    role: str          # "high" (near/above resort top) | "valley" (base town)
    resort: str | None  # resort id this station scores; None = archive-only
    snow_truth: bool   # its var-38 series is a SCORED fresh-snow truth


# Scored truth: two-to-three stations per resort (a high massif station and a
# valley one), plus the extra high-altitude snow-depth EMAs we archive but do
# NOT score ("archive wide, publish narrow"). All codes are backfilled; only
# `resort is not None` stations are scored, and only `snow_truth` var-38 series
# are the fresh-snow reference.
#
# La Molina's high snow-depth truth is Z9 Cadí Nord, not ZD la Tosa d'Alp: ZD
# sits at the resort but serves no var 38, which is exactly why the user added
# Cadí Nord on 2026-07-17. ZD is still scored for temperature/wind.
XEMA_STATIONS: tuple[XemaStation, ...] = (
    # Baqueira
    XemaStation("Z1", "Bonaigua", 2262, "high", "baqueira", True),
    XemaStation("YN", "Vielha - Elipòrt", 1029, "valley", "baqueira", False),
    # Boí Taüll
    XemaStation("Z2", "Boí", 2537, "high", "boi-taull", True),
    XemaStation("CT", "el Pont de Suert", 824, "valley", "boi-taull", False),
    # La Molina
    XemaStation("Z9", "Cadí Nord - Prat d'Aguiló", 2145, "high", "la-molina", True),
    XemaStation("ZD", "la Tosa d'Alp", 2478, "high", "la-molina", False),
    XemaStation("DP", "Das - Aeròdrom", 1096, "valley", "la-molina", False),
    # Archive-wide: high Pyrenees EMAs reporting snow depth, near the resorts.
    # Backfilled for var 38 only; available if the truth set ever needs them.
    XemaStation("Z3", "Malniu", 2229, "high", None, False),
    XemaStation("Z5", "Certascan", 2398, "high", None, False),
    XemaStation("Z7", "Espot", 2519, "high", None, False),
    XemaStation("ZE", "el Port del Comte", 2288, "high", None, False),
    XemaStation("DG", "Núria", 1971, "high", None, False),
)
