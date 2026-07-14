"""The band elevations are confirmed in the brief; these tests pin them."""

from minipirineu.config import METEOCAT_ZONE_BY_STATION, MODELS, STATIONS

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
