"""Meteocat pronostic parsers, pinned against real recorded payloads.

Fixtures under tests/fixtures/meteocat/ were recorded live on 2026-07-17
(scripts/record_meteocat_fixtures.py) and are byte-faithful. The quirks they
pin — inconsistent franja names, string `valor`s, ~25-char zone names, absent
winter variables — were all first found the hard way in PiriNeu.
"""

import json
from pathlib import Path

import pytest

from minipirineu import meteocat
from minipirineu.config import METEOCAT_ANCHOR_COORDS, METEOCAT_ANCHORS

FIXTURES = Path(__file__).parent / "fixtures" / "meteocat"
RUN_TIME = "2026-07-17T08:00:00Z"


def load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_bytes())


# --- franja windows ----------------------------------------------------------

@pytest.mark.parametrize(
    ("franja", "expected"),
    [
        ({"nom": "24h", "idTipusFranja": 5}, (0, 24)),
        ({"nom": "00:00h - 06:00h", "idTipusFranja": 1}, (0, 6)),
        ({"nom": "06:00 - 12:00h", "idTipusFranja": 2}, (6, 6)),  # no h on start
        ({"nom": "garbage", "idTipusFranja": 3}, (12, 6)),  # falls back to id
        ({"nom": "garbage", "idTipusFranja": 99}, None),
        ({}, None),
    ],
)
def test_franja_window_handles_real_world_formats(franja, expected):
    assert meteocat.franja_window(franja) == expected


# --- zones -------------------------------------------------------------------

def test_parse_zones_real_fixture_rows():
    rows = meteocat.parse_zones(load("zones_today.json"), RUN_TIME, "2026-07-17")
    assert rows, "real payload must yield rows"
    stations = {r.station for r in rows}
    # the API's own 7-zone scheme; zone 2 does not exist in it
    assert stations == {f"zona_{i}" for i in (1, 3, 4, 5, 6, 7, 8)}
    assert all(r.source == "meteocat" for r in rows)
    assert all(r.run_time_utc == RUN_TIME for r in rows)
    assert all(isinstance(r.value, float) for r in rows)  # str '1' -> 1.0


def test_parse_zones_valid_time_and_window_suffix():
    rows = meteocat.parse_zones(load("zones_today.json"), RUN_TIME, "2026-07-17")
    variables = {r.variable for r in rows}
    # 6h blocks and the 24h summary stay distinct via the variable suffix.
    # Validated on the real payload: cel/probabilitat/tempesta/visibilitat
    # appear only in 6h franjes; acumulacio(-Neu) also in the 24h franja.
    assert "zonal.cel.6h" in variables
    assert "zonal.acumulacio.24h" in variables
    assert "zonal.cel.24h" not in variables
    valids = {r.valid_time_utc for r in rows if r.variable == "zonal.cel.6h"}
    assert valids == {f"2026-07-17T{h:02d}:00Z" for h in (0, 6, 12, 18)}
    day_rows = [r for r in rows if r.variable == "zonal.acumulacio.24h"]
    assert {r.valid_time_utc for r in day_rows} == {"2026-07-17T00:00Z"}


def test_parse_zones_skips_absent_valors():
    # summer payloads carry acumulacioNeu/cota with valor null (never 0)
    rows = meteocat.parse_zones(load("zones_today.json"), RUN_TIME, "2026-07-17")
    assert not [r for r in rows if "acumulacioNeu" in r.variable]
    assert not [r for r in rows if "cota" in r.variable]


def test_parse_zones_tolerates_junk_body():
    assert meteocat.parse_zones([], RUN_TIME, "2026-07-17") == []
    assert meteocat.parse_zones({"franjes": None}, RUN_TIME, "2026-07-17") == []


# --- zone-scheme drift -------------------------------------------------------

def test_zone_names_match_expected_on_real_payloads():
    assert meteocat.check_zone_names(load("zones_today.json")) == []
    assert meteocat.check_zone_names(load("zones_tomorrow.json")) == []


def test_zone_rename_and_unknown_id_are_reported():
    body = {
        "franjes": [
            {"zones": [{"idZona": 1, "nom": "Zona renombrada"},
                       {"idZona": 99, "nom": "Zona nova"}]}
        ]
    }
    problems = meteocat.check_zone_names(body)
    assert len(problems) == 2
    assert any("renamed" in p and "1" in p for p in problems)
    assert any("unknown" in p and "99" in p for p in problems)


# --- pics --------------------------------------------------------------------

def test_parse_pic_real_fixture_rows():
    rows = meteocat.parse_pic(load("pic_baqueira.json"), RUN_TIME, "baqueira")
    variables = {r.variable for r in rows}
    assert "pic.isozero.totes" in variables
    assert "pic.iso-10.totes" in variables
    # spaces in variable names become underscores; per-level variables exist
    assert "pic.direccio_vent.3000" in variables
    assert "pic.temperatura.1500" in variables
    valids = sorted({r.valid_time_utc for r in rows})
    assert len(valids) == 8  # three-hourly timesteps
    assert valids[0] == "2026-07-17T00:00Z"
    assert all(r.station == "baqueira" for r in rows)


def test_parse_pic_tolerates_junk_body():
    assert meteocat.parse_pic({"not": "a list"}, RUN_TIME, "x") == []
    assert meteocat.parse_pic([{"cotes": None}], RUN_TIME, "x") == []


# --- metadades ---------------------------------------------------------------

def _full_metadades() -> dict:
    mapping = meteocat.parse_metadades(load("pics_metadades.json"), "pics")
    mapping.update(meteocat.parse_metadades(load("refugis_metadades.json"), "refugis"))
    return mapping


def test_metadades_resolve_all_configured_anchors():
    mapping = _full_metadades()
    assert set(mapping) == {a.codi for a in METEOCAT_ANCHORS}
    assert mapping["77954ad7"]["slug"] == "cap-de-vaqueira"
    assert mapping["77954ad7"]["kind"] == "pics"
    assert mapping["a9f7eb3a"]["kind"] == "refugis"


def test_missing_anchor_codis_are_detected():
    mapping = _full_metadades()
    assert meteocat.missing_anchors(mapping) == set()
    del mapping["77954ad7"]
    assert meteocat.missing_anchors(mapping) == {"77954ad7"}


def test_primary_coords_match_canonical_on_real_metadades():
    assert meteocat.coord_drift(_full_metadades()) == []


def test_primary_coord_drift_is_reported():
    mapping = _full_metadades()
    mapping["4d04de5e"] = {**mapping["4d04de5e"], "lat": 43.0}
    problems = meteocat.coord_drift(mapping)
    assert len(problems) == 1
    assert "4d04de5e" in problems[0]


def test_anchor_coords_cover_exactly_the_primaries():
    primaries = {a.codi for a in METEOCAT_ANCHORS if a.primary}
    assert set(METEOCAT_ANCHOR_COORDS) == primaries


# --- qualitative fields for the rendered column -------------------------------

def test_zone_day_fields_from_24h_franja():
    # the 24h franja carries only accumulations (+ comentari); sky state and
    # probabilities live in the 6h blocks (validated on the real payload)
    fields = meteocat.zone_day_fields(load("zones_today.json"), 1)
    assert "acumulacio" in fields
    assert fields["acumulacioNeu"] is None  # summer: absent, never 0
    assert "cel" not in fields


def test_zone_block_fields_carry_sky_state_per_6h_block():
    blocks = meteocat.zone_block_fields(load("zones_today.json"), 1)
    assert set(blocks) == {0, 6, 12, 18}
    # raw codes, no interpretation (labels arrive with the simbols catalog)
    assert blocks[12]["cel"] is not None
    assert blocks[12]["probabilitat"] is not None


def test_zone_day_fields_unknown_zone_is_empty():
    assert meteocat.zone_day_fields(load("zones_today.json"), 99) == {}
    assert meteocat.zone_block_fields(load("zones_today.json"), 99) == {}
