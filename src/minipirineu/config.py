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
    # "derived": snowfall estimated from precipitation + band temperature via the
    #   dry-bulb taper (AROME HD serves no snowfall in any form — validated
    #   2026-07, see docs/notes/snowfall-semantics.md).
    # "derived_wetbulb": same, but the rain/snow partition is keyed on Stull
    #   wet-bulb (T + RH) — the S1.1 promotion (docs/notes/wetbulb-partition.md),
    #   which beat the dry-bulb baseline on the go/no-go.
    snowfall_source: str
    # Short column id under which this model's snowfall is scored in the
    # verification store (`fx.snowfall_cm.<column>`, T8/T9). Single source of
    # truth so backtest (T9) and live (T11) write the same column names.
    column: str


@dataclass(frozen=True)
class DerivedColumn:
    """A scored-only snowfall column derived from a fetched model with an
    alternate partition — NOT a model to fetch (S1.1). Kept out of MODELS so the
    fetch/render loops are untouched: it is scored side by side in the backtest
    (T9) and live loop (T11) but never rendered until it beats the frozen
    baseline (ADR-0003, verification-first gate)."""

    column: str        # scored under `fx.snowfall_cm.<column>` (verify.py)
    label: str
    from_model: str    # ModelSpec.id whose precip/temp/RH series feed the derivation
    partition: str     # "wetbulb" — which aggregate derivation to apply


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
    ModelSpec("meteofrance_arome_france_hd", "AROME HD 1.3 km",
              snowfall_source="derived_wetbulb", column="arome_hd"),
    ModelSpec("meteofrance_arome_france", "AROME 2.5 km",
              snowfall_source="native", column="arome_25"),
)

# Scored-only reference columns (never rendered). After the S1.1 promotion
# (2026-07-30) the rendered HD column `arome_hd` IS the wet-bulb derivation;
# `arome_hd_dry` retains the pre-promotion dry-bulb behaviour so the live loop
# can keep confirming the improvement against the frozen (dry-bulb) baseline —
# ROADMAP §1 "priors to confirm live". See docs/notes/wetbulb-partition.md.
DERIVED_COLUMNS: tuple[DerivedColumn, ...] = (
    DerivedColumn("arome_hd_dry", "AROME HD (bulbo seco, referencia)",
                  from_model="meteofrance_arome_france_hd", partition="drybulb"),
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

# Wet-bulb partition (S1.1): same 0.45 cold ratio, but the rain/snow taper is
# keyed on Stull wet-bulb Tw (from T + RH) instead of dry-bulb T — dry air lets
# snow survive above 0 °C, which the T taper misses. The breakpoints are
# literature priors (all-snow ≤0 °C Tw, all-rain ≥~1.5 °C Tw; Pyrenees air-T
# threshold ≈1 °C, ROADMAP S1.1) to be calibrated on the backtest (Slice B).
WETBULB_T_FULL_C = 0.0   # all-snow at/below this wet-bulb °C
WETBULB_T_ZERO_C = 1.5   # all-rain at/above this wet-bulb °C
# Stull (2011) is fitted for RH in [5, 99] %; clamp before evaluating so extreme
# inputs stay in the empirical range rather than diverging.
WETBULB_RH_MIN = 5.0
WETBULB_RH_MAX = 99.0

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
    latitude: float    # from the XEMA metadata dataset yqwd-vj5e (probe 2026-07-29)
    longitude: float   # forecasts are re-fetched at these points (T9), not at
    #                    the resort band points — apples to apples with truth.
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
    XemaStation("Z1", "Bonaigua", 2262, 42.64691, 0.98486, "high", "baqueira", True),
    XemaStation("YN", "Vielha - Elipòrt", 1029, 42.69737, 0.80197, "valley", "baqueira", False),
    # Boí Taüll
    XemaStation("Z2", "Boí", 2537, 42.46603, 0.88403, "high", "boi-taull", True),
    XemaStation("CT", "el Pont de Suert", 824, 42.39809, 0.74364, "valley", "boi-taull", False),
    # La Molina
    XemaStation("Z9", "Cadí Nord - Prat d'Aguiló", 2145, 42.29265, 1.71498, "high", "la-molina", True),
    XemaStation("ZD", "la Tosa d'Alp", 2478, 42.32213, 1.89716, "high", "la-molina", False),
    XemaStation("DP", "Das - Aeròdrom", 1096, 42.38603, 1.86639, "valley", "la-molina", False),
    # Archive-wide: high Pyrenees EMAs reporting snow depth, near the resorts.
    # Backfilled for var 38 only; available if the truth set ever needs them.
    XemaStation("Z3", "Malniu", 2229, 42.46605, 1.77850, "high", None, False),
    XemaStation("Z5", "Certascan", 2398, 42.70030, 1.27200, "high", None, False),
    XemaStation("Z7", "Espot", 2519, 42.53410, 1.05474, "high", None, False),
    XemaStation("ZE", "el Port del Comte", 2288, 42.19084, 1.53213, "high", None, False),
    XemaStation("DG", "Núria", 1971, 42.39848, 2.15517, "high", None, False),
)


# --- truth-A: fresh snow from snow-depth increments (S0.4a/T6) ---------------
#
# Coefficients live here (not hardcoded in truth.py) so they can be re-fitted
# against documented storms without touching the algorithm. Anchors and the
# two-layer settling design are in docs/adr/0004-truth-pipeline.md.

# Anderson (1976) destructive-metamorphism settling of the NEW-snow layer:
#   S(T) = C3 · exp(C4 · T)  [fraction per second], T = snow temp °C (≤ 0).
# C3 ≈ 1 %/h at 0 °C; colder snow settles slower. Confirmed against Helfricht
# et al. (2018, HESS 22, 2655) who use exactly these on sub-daily automated HS.
SETTLING_C3_PER_S = 2.777e-6
SETTLING_C4_PER_C = 0.04
# A layer settles (counts as "new") for this long, then joins the old pack,
# which is treated as non-settling: a seasonal pack (ρ≈300–400) has Anderson
# density factor exp(−0.046·(ρ−150)) ≈ 1e-4, i.e. negligible settling.
NEW_SNOW_AGE_H = 24
# Snow-surface temperature proxy = min(air_T, 0). When the air-T obs is missing
# for a step, fall back to this (settling still happens physically).
SETTLE_DEFAULT_T_C = -2.0

# Hampel despike of the 30-min HS series: replace a point deviating more than
# HAMPEL_N_SIGMAS scaled MADs from its centered-window median. Removes isolated
# ultrasonic spikes without flattening a real snowfall step; MAD=0 flags nothing.
HAMPEL_WINDOW = 5          # samples (odd), ~±1 h at 30-min resolution
HAMPEL_N_SIGMAS = 4.0
# Sensor-noise floor (cm) for the Hampel scale: on a flat window MAD=0, so a
# bare MAD test can neither flag a real spike nor is it robust. This floor makes
# the spike test absolute (spike ≫ floor is caught) while a ±0.5 cm jitter isn't.
HAMPEL_MIN_SCALE_CM = 0.5

# After despiking, a centered moving average (Helfricht et al. 2018 smooth the
# ultrasonic HS the same way) over this many samples damps the ±1–3 cm 30-min
# jitter that would otherwise be summed as fabricated snow. Wider = less noise
# but more smear of a sharp real step across a bucket edge; 5 ≈ ±1 h. Set to 1
# to disable (the unit tests isolate the increment logic that way).
TRUTH_SMOOTH_WINDOW = 5

# Verification buckets are 6 h in UTC (BUCKET_HOURS), aligned to 00/06/12/18.
# A gap between consecutive readings longer than this marks the affected
# bucket incomplete (truth None, never 0). Nominal step is 30 min; 90 tolerates
# a single dropout without crossing into "unknown accumulation".
TRUTH_MAX_STEP_MIN = 90


# --- truth-B: gauge-based fresh snow + A/B quality gates (S0.4b/T7) ----------
#
# Independent of truth-A: heated precip gauge (var 35) → wind-undercatch
# correction → fresh-snow density → cm, used to cross-check the snow-depth
# truth. Coefficients live here (not in truth_b.py) so they can be re-fitted
# against documented storms without touching the algorithm. Literature anchors
# and the gate design are in docs/adr/0004-truth-pipeline.md.

# Kochendorfer et al. (2017, HESS 21, 1973), the WMO-SPICE universal transfer
# function for an UNSHIELDED gauge (adopted in Kochendorfer et al. 2020):
#   CE = exp(−a · U · (1 − arctan(b · T) + c))
# with U the wind at gauge height (m/s) and T air temperature (°C). Adjusted
# precip = gauge / CE. At U=0, CE=1; colder and windier both lower the catch.
UNDERCATCH_A = 0.0785
UNDERCATCH_B = 0.729
UNDERCATCH_C = 0.407
# The SPICE fit is trustworthy to ~7.2 m/s gauge-height wind; past it CE (and so
# 1/CE) runs away, but those buckets are already dropped by the gust gate. The
# floor keeps the correction finite regardless.
UNDERCATCH_CE_FLOOR = 0.30
UNDERCATCH_WIND_CAP_MS = 7.2
# XEMA wind is at 10 m; the transfer function wants gauge-height (~2 m) wind.
# Log-profile reduction over open snow (z0 ≈ 0.01 m): ln(2/z0)/ln(10/z0) ≈ 0.767.
WIND_10M_TO_GAUGE = 0.767

# Fresh-snow density ρ(T) = D0 + D1·exp(T/D2) [kg/m³], Hedstrom & Pomeroy (1998,
# Hydrol. Process. 12, 1611). Its cold asymptote D0 = 67.9 kg/m³ coincides with
# Helfricht et al. (2018) high-mountain new-snow mean 68 ± 9 (high-altitude new
# snow falls cold), tying truth-B's density anchor to the same source truth-A
# uses. cm of snow = SWE_mm · 100 / ρ.
FRESH_SNOW_DENSITY_D0 = 67.9
FRESH_SNOW_DENSITY_D1 = 51.3
FRESH_SNOW_DENSITY_D2 = 2.6
FRESH_SNOW_DENSITY_MIN = 50.0   # physical floor for new-snow density
FRESH_SNOW_DENSITY_MAX = 200.0  # wet/dense new snow near 0 °C

# Coarse snow/rain split for the GAUGE truth only (kept independent of the
# forecast's own taper — the product-grade wet-bulb phase is Stage 1/S1.1). The
# solid fraction tapers linearly from 1 at/below T_SNOW to 0 at/above T_RAIN
# (air T, °C; the Pyrenees air-T snow threshold is ~1 °C, ROADMAP S1.1).
TRUTHB_SNOW_ALL_T_C = 0.5
TRUTHB_RAIN_ALL_T_C = 2.0

# A/B quality gates (ADR-0004). Sustained wind redistributes snow — blowing it
# off the ultrasonic sensor and past/into the gauge — so both ΔHS and gauge
# catch stop tracking true fall: the bucket is excluded. Gated on MEAN 10 m wind
# (var 30), the transport-relevant signal, not the peak gust (var 50): a single
# gust does not corrupt a 6 h accumulation, and gusts stay high in mild weather.
# 6 m/s is the dry-snow drifting onset (Li & Pomeroy 1997; roadmap's 6–8 band),
# conservative on purpose — verification prefers fewer, cleaner truth buckets.
# (The empirical calibration is in the T7 note of docs/adr/0004-truth-pipeline.md:
# the windy Cadí Nord storm of 2025-03-09 showed gust-max gating discarding 58 %
# of buckets and mislabelling melt buckets as wind.)
GATE_WIND_MEAN_MS = 6.0
# A and B are independent; a wild disagreement means at least one is wrong
# (rimed sensor releasing a catch-up, gauge freeze, blowing snow). Threshold is
# max(absolute cm, fraction of the larger estimate) — generous, because both
# methods carry real uncertainty and the gate should catch gross conflicts only.
GATE_AB_ABS_CM = 3.0
GATE_AB_FRAC = 0.6
# Melt / rain-on-snow signature: air above freezing, pack losing depth, gauge
# still catching. cm becomes a lower bound and the bucket is phase-scorable only
# (verify.py routes phase_only pairs to event metrics, not cm MAE).
GATE_MELT_T_C = 0.0


# --- verify.py metric engine (S0.5/T8) --------------------------------------
#
# One scoring path for backtest (T10) and live (T11) — the same code, no
# knowledge of which (ADR-0003). Metric spec: docs/ROADMAP.md §1.

# Dead band (ADR-0003): a forecast this close to the obs counts as a hit — the
# ±1–2 cm sensor floor is not chased. Hit iff |fx − obs| ≤ max(abs, frac·obs).
DEAD_BAND_ABS_CM = 2.0
DEAD_BAND_FRAC = 0.20
# Event thresholds: a 6 h bucket is a "snow event" at/above EVENT_BUCKET_CM; a
# "snow day" is ≥ SNOW_DAY_CM over 24 h (roadmap §1).
EVENT_BUCKET_CM = 1.0
SNOW_DAY_CM = 1.0

# S1.1 go/no-go: phase skill is judged on "marginal" buckets only — those whose
# bucket-mean forecast band temperature is within ±MARGINAL_T_C of 0 °C, where
# the rain/snow call is genuinely in doubt and the wet-bulb driver can help.
MARGINAL_T_C = 2.0


# --- Previous Runs backtest fetch (S0.6a/T9) --------------------------------
#
# Forecast side of the frozen baseline: fixed-lead AROME series from the
# Previous Runs API, re-fetched at XEMA truth-station points and scored by the
# same verify.py (T8) as the live loop. Findings that fix the parameters below
# are in docs/notes/previous-runs-coverage.md (probe 2026-07-29).

# Lead (hours) each `_previous_dayN` series is labelled with. AROME serves ONLY
# previous_day1 (~24 h): previous_day2 (~48 h) is beyond its ~51 h horizon and
# comes back entirely null (probe 2026-07-29). day2 is kept here so the fetcher
# is horizon-agnostic — its empty series simply produce no rows for AROME, and a
# longer-horizon model (ECMWF IFS, S2.3) would populate it.
PREV_LEAD_H: dict[int, float] = {1: 24.0, 2: 48.0}
# The lead days actually requested by the backtest backfill. day2 is omitted for
# AROME (always null); T10 scores a 24 h-lead baseline. Re-add 2 for IFS later.
BACKTEST_LEAD_DAYS: tuple[int, ...] = (1,)

# Archive floor of the Previous Runs API for AROME previous_day1 (both models),
# UTC. Before this instant the series is null; the backfill should not request
# earlier windows (they cost quota and return nothing). Probe 2026-07-29.
PREVIOUS_RUNS_AROME_START_UTC = "2024-01-19T12:00:00Z"

# Budget guard for the one-off backfill. Open-Meteo weights a call by how much
# data it returns; we approximate cost in "call units" as
# ceil(days / CALL_UNIT_DAYS) · n_series and refuse a plan whose total exceeds
# the cap, so the ~10 000/day non-commercial ceiling is never approached. The
# real backtest (3 snow-truth stations × ~14 months) is a few hundred units.
CALL_UNIT_DAYS = 14.0
BACKTEST_DAILY_CALL_UNIT_CAP = 5000
