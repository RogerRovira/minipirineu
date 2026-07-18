"""The band elevations are confirmed in the brief; these tests pin them."""

from minipirineu.config import (
    METEOCAT_ZONE_BY_STATION,
    MODELS,
    STATIONS,
    XEMA_SNOW_DEPTH_VAR,
    XEMA_STATIONS,
    XEMA_VARIABLES,
)

BRIEF_ELEVATIONS = {
    "baqueira": {"baja": 1500, "media": 2000, "alta": 2600},
    "boi-taull": {"baja": 2000, "media": 2400, "alta": 2750},
    "la-molina": {"baja": 1700, "media": 2100, "alta": 2500},
}


def test_band_elevations_match_brief():
    assert {s.id: dict(s.bands) for s in STATIONS} == BRIEF_ELEVATIONS


def test_bands_ordered_low_to_high():
    for station in STATIONS:
        elevations = [e for _, e in station.bands]
        assert elevations == sorted(elevations)
        assert [b for b, _ in station.bands] == ["baja", "media", "alta"]


def test_coordinates_are_in_the_catalan_pyrenees():
    for station in STATIONS:
        assert 42.2 <= station.latitude <= 42.9, station.id
        assert 0.7 <= station.longitude <= 2.1, station.id


def test_models_are_the_two_arome_variants():
    assert [spec.id for spec in MODELS] == [
        "meteofrance_arome_france_hd",
        "meteofrance_arome_france",
    ]


def test_snowfall_sources_match_validated_api_reality():
    # AROME HD serves no snowfall variable on Open-Meteo (M1 finding);
    # AROME 2.5 does. See docs/notes/snowfall-semantics.md.
    sources = {spec.id: spec.snowfall_source for spec in MODELS}
    assert sources == {
        "meteofrance_arome_france_hd": "derived",
        "meteofrance_arome_france": "native",
    }


def test_every_station_has_a_meteocat_zone_entry():
    assert set(METEOCAT_ZONE_BY_STATION) == {s.id for s in STATIONS}


# --- XEMA truth set (S0.3/T5) ------------------------------------------------

RESORT_IDS = {s.id for s in STATIONS}


def test_xema_stations_are_wellformed():
    codes = [s.codi for s in XEMA_STATIONS]
    assert len(codes) == len(set(codes)), "duplicate XEMA codes"
    for s in XEMA_STATIONS:
        assert s.role in {"high", "valley"}, s.codi
        assert s.resort is None or s.resort in RESORT_IDS, s.codi
        assert 800 <= s.altitude_m <= 2800, s.codi
        # only scored stations may be a scored snow truth
        assert not (s.snow_truth and s.resort is None), s.codi


def test_every_resort_has_a_high_snow_truth_station():
    # each resort must have a high-altitude station whose var 38 is scored,
    # or its snow forecast can never be verified
    for resort in RESORT_IDS:
        highs = [s for s in XEMA_STATIONS
                 if s.resort == resort and s.role == "high" and s.snow_truth]
        assert highs, resort


def test_cadi_nord_is_z9_and_la_molinas_snow_truth():
    # user decision 2026-07-17: Cadí Nord (Z9) is La Molina's high snow-depth
    # truth because ZD la Tosa serves no var 38 (probe 2026-07-18)
    z9 = next(s for s in XEMA_STATIONS if s.codi == "Z9")
    assert z9.resort == "la-molina" and z9.snow_truth
    zd = next(s for s in XEMA_STATIONS if s.codi == "ZD")
    assert zd.resort == "la-molina" and not zd.snow_truth


def test_archive_wide_stations_are_unscored():
    wide = [s for s in XEMA_STATIONS if s.resort is None]
    assert wide, "expected archive-wide snow-depth EMAs"
    assert all(not s.snow_truth for s in wide)


def test_xema_variables_cover_the_probed_set_without_phantom_nine():
    # var 9 was in an early roadmap draft but is not a real XEMA variable
    assert "9" not in XEMA_VARIABLES
    assert XEMA_SNOW_DEPTH_VAR == "38"
    assert XEMA_VARIABLES["38"] == "gruix_neu"
    # the phase/undercatch and band-T variables are all present
    assert {"30", "32", "35", "50"} <= set(XEMA_VARIABLES)
