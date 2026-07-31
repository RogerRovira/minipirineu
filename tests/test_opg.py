"""Orographic precipitation gradient (S1.3).

Covers the four things that can go wrong with a forecast correction: it fires
where it must not (below the saturation elevation, or on an unmeasured point),
it silently changes the published page before its gate passes, it fabricates a
saturation verdict from a dry run, or its go/no-go rubber-stamps a change that
did not earn it. All offline: recorded fixtures + synthetic pairs/stores.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minipirineu import config, ingest_openmeteo, live_forecast, openmeteo, opg
from minipirineu import previous_runs, render_verification, store, verify
from minipirineu.archive import Archive
from minipirineu.verify import Pair

FIXTURES = Path(__file__).parent / "fixtures"
PREV_FIXTURE = FIXTURES / "previous_runs_arome_z1_20250201.json"
LIVE_FIXTURE = FIXTURES / "openmeteo_live_baqueira_2600.json"
WIDE_BYTES = (FIXTURES / "openmeteo_wide_baqueira_1500.json").read_bytes()
HD = "meteofrance_arome_france_hd"
A25 = "meteofrance_arome_france"


# --- the factor itself ------------------------------------------------------

class TestFactor:
    def test_no_reference_means_no_correction(self):
        assert opg.factor(2600, None) == 1.0

    def test_at_or_below_the_reference_is_untouched(self):
        # the model still resolves the gradient there; correcting would double-count
        assert opg.factor(2000, 2000) == 1.0
        assert opg.factor(1500, 2000) == 1.0

    def test_linear_above_the_reference(self):
        # +3 %/100 m × 600 m
        assert opg.factor(2600, 2000, per_100m=0.03) == pytest.approx(1.18)

    def test_capped_so_a_long_extrapolation_cannot_fabricate_snow(self):
        assert opg.factor(9000, 2000, per_100m=0.03, max_factor=1.35) == 1.35


class TestResolveReference:
    def test_resort_points_are_measured(self):
        ref = opg.resolve_reference("baqueira", A25)
        assert (ref.elevation_m, ref.source) == (2000, "measured")

    def test_a_measured_absence_of_saturation_is_still_measured(self):
        ref = opg.resolve_reference("boi-taull", A25)
        assert (ref.elevation_m, ref.source) == (None, "measured")

    def test_xema_points_inherit_their_resort_and_say_so(self):
        ref = opg.resolve_reference("Z1", A25)  # Bonaigua, Baqueira's truth station
        assert (ref.elevation_m, ref.source) == (2000, "inherited")

    def test_a_probed_station_value_wins_over_the_inherited_one(self, monkeypatch):
        monkeypatch.setitem(config.OPG_PROBED_STATION_ELEVATION_M, ("Z1", A25), 2300)
        ref = opg.resolve_reference("Z1", A25)
        assert (ref.elevation_m, ref.source) == (2300, "measured")
        assert opg.point_factor("Z1", A25, 2262) == 1.0  # now below its own reference

    def test_an_unknown_point_never_guesses(self):
        ref = opg.resolve_reference("XX", A25)
        assert (ref.elevation_m, ref.source) == (None, "unknown")
        assert opg.point_factor("XX", A25, 3000) == 1.0


def test_point_factor_only_fires_on_the_measured_saturating_bands():
    # Baqueira saturates at 2000 m for both models: only the 2600 m band moves
    assert opg.point_factor("baqueira", A25, 1500) == 1.0
    assert opg.point_factor("baqueira", A25, 2000) == 1.0
    assert opg.point_factor("baqueira", A25, 2600) == pytest.approx(1.18)
    # Boí Taüll resolves its own cells at every band → nothing moves
    assert opg.point_factor("boi-taull", A25, 2750) == 1.0
    # La Molina saturates for HD only
    assert opg.point_factor("la-molina", HD, 2500) == pytest.approx(1.12)
    assert opg.point_factor("la-molina", A25, 2500) == 1.0


class TestScaling:
    def test_series_scaling_preserves_missing(self):
        assert opg.scale_series([1.0, None, 2.0], 1.5) == [1.5, None, 3.0]

    def test_bucket_scaling_rounds_like_the_store_rows(self):
        assert opg.scale_buckets([("2025-02-01T00:00:00Z", 4.0)], 1.18) == \
            [("2025-02-01T00:00:00Z", 4.7)]


# --- detecting saturation from a run ----------------------------------------

class TestDetectReference:
    WET = [3.0, 3.0]

    def test_identical_wet_bands_reveal_a_shared_grid_cell(self):
        det = opg.detect_reference({1500: [1.0, 1.0], 2000: self.WET, 2600: self.WET})
        assert det == opg.Detection(2000, True)

    def test_lowest_saturating_band_wins(self):
        det = opg.detect_reference({1500: self.WET, 2000: self.WET, 2600: self.WET})
        assert det.reference_m == 1500

    def test_a_dry_run_decides_nothing(self):
        det = opg.detect_reference({1500: [0.0, 0.0], 2000: [0.0, 0.0], 2600: [0.0, 0.0]})
        assert det == opg.Detection(None, False)
        # ...and therefore writes no diagnostic row and raises no drift alert
        assert opg.reference_rows("baqueira", "2026-01-01T00:00:00Z", {A25: det}) == []
        assert opg.reference_drift("baqueira", A25, det) is None

    def test_no_saturation_is_a_real_verdict_when_every_pair_was_wet(self):
        det = opg.detect_reference({1500: [1.0, 1.0], 2000: [2.0, 2.0], 2600: [3.0, 3.0]})
        assert det == opg.Detection(None, True)

    def test_a_pair_too_dry_to_check_leaves_the_verdict_open(self):
        # the 2000→2600 pair is unreadable, so "no saturation" cannot be claimed
        det = opg.detect_reference({1500: [3.0, 3.0], 2000: [0.0, 0.0], 2600: [1.0, 0.0]})
        assert det == opg.Detection(None, False)


class TestReferenceDrift:
    def test_silent_when_the_run_confirms_the_configured_value(self):
        det = opg.Detection(2000, True)
        assert opg.reference_drift("baqueira", A25, det) is None

    def test_alerts_when_the_grid_stops_behaving_as_measured(self):
        det = opg.Detection(2600, True)
        msg = opg.reference_drift("baqueira", A25, det)
        assert msg and "2000" in msg and "2600" in msg

    def test_alerts_on_a_point_we_never_measured(self):
        msg = opg.reference_drift("XX", A25, opg.Detection(2000, True))
        assert msg and "unmeasured" in msg


def test_reference_rows_record_no_saturation_distinctly_from_absence():
    rows = opg.reference_rows("boi-taull", "2026-01-01T00:00:00Z", {
        A25: opg.Detection(None, True),      # decided: no saturation → NULL value
        HD: opg.Detection(2400, True),       # decided: saturates at 2400
    })
    by_var = {r.variable: r.value for r in rows}
    assert by_var[f"{opg.DIAG_REFERENCE}.{A25}"] is None
    assert by_var[f"{opg.DIAG_REFERENCE}.{HD}"] == 2400.0
    assert all(r.station == "boi-taull" for r in rows)


# --- the verification paths write the variant column ------------------------

def test_backtest_writes_the_opg_column_alongside_the_plain_one():
    raw = json.loads(PREV_FIXTURE.read_bytes())
    rows = previous_runs.to_forecast_rows(raw, "Z1")  # Bonaigua, 2262 m
    base = {(r.valid_time_utc): r.value for r in rows if r.variable == "fx.snowfall_cm.arome_25"}
    variant = {(r.valid_time_utc): r.value for r in rows
               if r.variable == "fx.snowfall_cm.arome_25_opg"}
    assert base and variant.keys() == base.keys()
    f = opg.point_factor("Z1", A25, 2262)
    assert f > 1.0
    for valid, cm in base.items():
        assert variant[valid] == pytest.approx(round(cm * f, 1))


def test_backtest_writes_no_variant_where_nothing_saturates():
    raw = json.loads(PREV_FIXTURE.read_bytes())
    # Z2 belongs to Boí Taüll, whose bands all resolve their own cell
    rows = previous_runs.to_forecast_rows(raw, "Z2")
    assert not [r for r in rows if r.variable.endswith("_opg")]


def test_live_pairing_writes_the_variant_only_when_told_the_point():
    plain = live_forecast.raw_to_rows(LIVE_FIXTURE.read_bytes(), "2026-07-20T09:00:40Z", "Z1")
    assert not [r for r in plain if r.variable.endswith("_opg")]

    rows = live_forecast.raw_to_rows(LIVE_FIXTURE.read_bytes(), "2026-07-20T09:00:40Z", "Z1",
                                     point_id="baqueira", elevation_m=2600)
    variants = {r.valid_time_utc: r.value for r in rows
                if r.variable == "fx.snowfall_cm.arome_25_opg"}
    base = {r.valid_time_utc: r.value for r in rows
            if r.variable == "fx.snowfall_cm.arome_25"}
    assert variants.keys() == base.keys()
    assert all(variants[v] == pytest.approx(round(base[v] * 1.18, 1)) for v in base)


# --- the published page stays put until the gate passes ---------------------

def _wet_fetch(session, station, elevation_m, timeout=30):
    """The recorded wide response, made rainy: the two upper bands of every
    station get an IDENTICAL precipitation series (as a saturating grid cell
    produces) and the lowest band a smaller one. Snow follows the precipitation
    so the correction is visible in the published cm too."""
    raw = json.loads(WIDE_BYTES)
    band_index = [e for _b, e in station.bands].index(elevation_m)
    mm = 1.0 if band_index == 0 else 2.0
    hourly = raw["hourly"]
    n = len(hourly["time"])
    for model_id in (A25, HD):
        hourly[f"precipitation_{model_id}"] = [mm] * n
        hourly[f"snowfall_{model_id}"] = [mm * 0.5] * n
    return json.dumps(raw).encode()


def _snapshot(tmp_path, monkeypatch, enabled: bool, fetch=_wet_fetch) -> dict:
    monkeypatch.setenv("MINIPIRINEU_DATA_DIR", str(tmp_path / f"ds{enabled}"))
    monkeypatch.setattr(openmeteo, "fetch", fetch)
    monkeypatch.setattr(config, "OPG_ENABLED", enabled)
    out = tmp_path / f"openmeteo-{enabled}.json"
    assert ingest_openmeteo.main(out, now_local=datetime(2026, 7, 17, 15, 0)) == 0
    return json.loads(out.read_text())


def _band(snapshot: dict, station_id: str, elevation_m: int) -> dict:
    station = next(s for s in snapshot["stations"] if s["id"] == station_id)
    return next(b for b in station["bands"] if b["elevation_m"] == elevation_m)


def test_disabled_opg_leaves_the_published_snapshot_untouched(tmp_path, monkeypatch):
    """ADR-0003: the correction must not reach the page before its gate passes."""
    off = _snapshot(tmp_path, monkeypatch, enabled=False)
    for station in off["stations"]:
        for band in station["bands"]:
            assert all("opg_factor" not in m for m in band["models"])


def test_enabling_opg_scales_only_the_saturating_bands(tmp_path, monkeypatch):
    off = _snapshot(tmp_path, monkeypatch, enabled=False)
    on = _snapshot(tmp_path, monkeypatch, enabled=True)

    low_off, low_on = _band(off, "baqueira", 1500), _band(on, "baqueira", 1500)
    assert low_off == low_on  # at/below the reference: byte-identical

    high_off, high_on = _band(off, "baqueira", 2600), _band(on, "baqueira", 2600)
    for before, after in zip(high_off["models"], high_on["models"]):
        assert after["opg_factor"] == 1.18
        assert before["total_precipitation_mm"] > 0  # the fixture is actually wet
        for iv_off, iv_on in zip(before["intervals"], after["intervals"]):
            assert iv_on["precipitation_mm"] == round(iv_off["precipitation_mm"] * 1.18, 1)
            assert iv_on["snowfall_cm"] == round(iv_off["snowfall_cm"] * 1.18, 1)
            assert iv_on["temperature_c"] == iv_off["temperature_c"]  # T is untouched

    # nothing saturates at Boí Taüll → untouched even with the flag on
    assert _band(off, "boi-taull", 2750) == _band(on, "boi-taull", 2750)


def test_ingest_records_the_saturation_verdict_and_alerts_on_drift(tmp_path, monkeypatch, capsys):
    _snapshot(tmp_path, monkeypatch, enabled=False)
    conn = store.connect(tmp_path / "dsFalse" / "verification.sqlite")
    rows = conn.execute(
        "SELECT station, variable, value FROM verification_values WHERE variable LIKE ?",
        (opg.DIAG_REFERENCE + "%",)).fetchall()
    assert {r[0] for r in rows} == {"baqueira", "boi-taull", "la-molina"}
    assert {r[1] for r in rows} == {f"{opg.DIAG_REFERENCE}.{A25}", f"{opg.DIAG_REFERENCE}.{HD}"}
    # the synthetic run saturates at each station's MIDDLE band
    by_station = {(r[0], r[1]): r[2] for r in rows}
    assert by_station[("baqueira", f"{opg.DIAG_REFERENCE}.{A25}")] == 2000.0
    assert by_station[("boi-taull", f"{opg.DIAG_REFERENCE}.{A25}")] == 2400.0
    # ...which contradicts Boí Taüll's measured "no saturation" → visible alert
    assert "OPG reference drift at boi-taull" in capsys.readouterr().err


def test_a_dry_run_writes_no_saturation_verdict(tmp_path, monkeypatch):
    """The recorded fixture is a dry July run: nothing about saturation can be
    read from it, and nothing is recorded (missing stays missing)."""
    def dry_fetch(session, station, elevation_m, timeout=30):
        return WIDE_BYTES

    _snapshot(tmp_path, monkeypatch, enabled=False, fetch=dry_fetch)
    conn = store.connect(tmp_path / "dsFalse" / "verification.sqlite")
    n = conn.execute("SELECT COUNT(*) FROM verification_values WHERE variable LIKE ?",
                     (opg.DIAG_REFERENCE + "%",)).fetchone()[0]
    assert n == 0


# --- the go/no-go gate ------------------------------------------------------

def _pair(column: str, fx: float, truth: float, i: int = 0, station: str = "Z1",
          phase_only: bool = False) -> Pair:
    valid = f"2025-02-0{1 + i // 4}T{6 * (i % 4):02d}:00:00Z"
    return Pair(column, station, "baqueira", "2025-01-31T12:00:00Z", valid,
                24.0, fx, truth, phase_only)


def _ab(forecasts, opg_forecasts, truths) -> list[Pair]:
    pairs = []
    for i, (fx, ofx, obs) in enumerate(zip(forecasts, opg_forecasts, truths)):
        pairs.append(_pair("arome_25", fx, obs, i))
        pairs.append(_pair("arome_25_opg", ofx, obs, i))
    return pairs


class TestGate:
    def test_wet_buckets_are_the_ones_scored(self):
        wet = opg.wet_pairs([_pair("arome_25", 0.0, 0.0), _pair("arome_25", 0.0, 3.0, 1),
                             _pair("arome_25", 4.0, 0.0, 2)])
        assert [p.truth_cm for p in wet] == [3.0, 0.0]

    def test_a_real_improvement_passes(self):
        # under-prediction halved: MAE 4.0 → 2.0, bias stays negative
        result = opg.evaluate_gate(_ab([6.0] * 8, [8.0] * 8, [10.0] * 8))["arome_25"]
        assert result.passed and result.mae_gain == pytest.approx(0.5)
        assert result.stations == ("Z1",)
        assert result.base["mae"] == 4.0 and result.opg["mae"] == 2.0

    def test_a_small_improvement_is_a_no_go(self):
        # MAE 4.0 → 3.8: a real gain, below the 10 % the roadmap asks for
        result = opg.evaluate_gate(_ab([6.0] * 8, [6.2] * 8, [10.0] * 8))["arome_25"]
        assert not result.passed
        assert any("MAE gain" in r for r in result.reasons)

    def test_overshoot_is_a_no_go_even_when_mae_improves(self):
        # 2 cm under → 1 cm over: MAE halves, but the bias sign flipped
        result = opg.evaluate_gate(_ab([8.0] * 8, [11.0] * 8, [10.0] * 8))["arome_25"]
        assert not result.passed
        assert result.mae_gain == pytest.approx(0.5)
        assert any("overshoot" in r for r in result.reasons)

    def test_no_affected_points_is_a_no_go_not_a_pass(self):
        result = opg.evaluate_gate([_pair("arome_25", 1.0, 5.0)])["arome_25"]
        assert not result.passed and result.n_wet == 0
        assert "no OPG-affected wet buckets in the window" in result.reasons

    def test_the_comparison_is_restricted_to_the_affected_points(self):
        # Z2 has no variant; its buckets must not dilute either side of the gate
        pairs = _ab([6.0] * 4, [8.0] * 4, [10.0] * 4)
        pairs += [_pair("arome_25", 0.0, 9.0, i, station="Z2") for i in range(4)]
        result = opg.evaluate_gate(pairs)["arome_25"]
        assert result.stations == ("Z1",)
        assert result.base["n_cm"] == 4 and result.base["mae"] == 4.0

    def test_a_bucket_the_correction_lifts_over_the_threshold_is_scored_on_both_sides(self):
        # base 0.4 cm is "dry", its corrected 0.6 cm is "wet": dropping it from
        # the base side only would compare two different samples
        pairs = [_pair("arome_25", 0.4, 0.0), _pair("arome_25_opg", 0.6, 0.0)]
        result = opg.evaluate_gate(pairs)["arome_25"]
        assert result.n_wet == 1
        assert result.base["n_cm"] == result.opg["n_cm"] == 1

    def test_a_variant_bucket_with_no_base_counterpart_is_refused(self):
        pairs = [_pair("arome_25", 6.0, 10.0, 0), _pair("arome_25_opg", 8.0, 10.0, 0),
                 _pair("arome_25_opg", 8.0, 10.0, 1)]  # orphan: no base row at i=1
        result = opg.evaluate_gate(pairs)["arome_25"]
        assert not result.passed
        assert any("no base-column counterpart" in r for r in result.reasons)

    def test_markdown_states_the_verdict(self):
        text = opg.gate_markdown(opg.evaluate_gate(_ab([6.0] * 8, [8.0] * 8, [10.0] * 8)))
        assert "PASS" in text and "arome_25" in text


def test_gate_reads_the_columns_the_backtest_writes(tmp_path):
    """End to end on the store: the rows T9 writes are exactly what the gate
    reads back, so a column-naming drift between the two cannot go unnoticed."""
    conn = store.connect(tmp_path / "v.sqlite")
    raw = json.loads(PREV_FIXTURE.read_bytes())
    store.upsert_rows(conn, previous_runs.to_forecast_rows(raw, "Z1"))
    t = datetime(2025, 2, 1, tzinfo=timezone.utc)
    rows = []
    while t < datetime(2025, 2, 3, tzinfo=timezone.utc):
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows += [store.Row("xema", "Z1", ts, ts, s, v) for s, v in (
            ("obs.gruix_neu", 80.0), ("obs.temperatura", -5.0), ("obs.precipitacio", 0.0),
            ("obs.vent_velocitat", 1.0), ("obs.vent_ratxa", 2.0))]
        t += timedelta(minutes=30)
    store.upsert_rows(conn, rows)

    pairs = verify.build_pairs(conn, ["Z1"], "2025-02-01T00:00:00Z", "2025-02-03T00:00:00Z")
    results = opg.evaluate_gate(pairs)
    assert set(results) == {"arome_25", "arome_hd"}  # base columns only
    # truth here is a flat 0 cm pack while the fixture forecasts snow, so the
    # correction can only make the over-prediction worse — and must be refused.
    for column, result in results.items():
        assert result.n_wet > 0, column
        assert not result.passed and result.mae_gain < 0


# --- fitting the gradient from XEMA gauge pairs -----------------------------

def _gauge_rows(station: str, mm_per_reading: float, hours: int = 6,
                start: str = "2025-02-01T00:00:00Z") -> list[store.Row]:
    """A station's 30-min obs over `hours`: steady gauge catch, calm and cold so
    the undercatch correction is ~1 and the fit reads the ratio directly."""
    t = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    stop = t + timedelta(hours=hours)
    rows = []
    while t < stop:
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows += [store.Row("xema", station, ts, ts, s, v) for s, v in (
            ("obs.precipitacio", mm_per_reading), ("obs.temperatura", -5.0),
            ("obs.vent_velocitat", 0.0), ("obs.vent_ratxa", 0.0),
            ("obs.gruix_neu", 50.0))]
        t += timedelta(minutes=30)
    return rows


def test_fit_recovers_a_known_peak_valley_gradient(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    pair = next(p for p in opg.gauge_pairs() if (p.high, p.valley) == ("Z9", "DP"))
    # high station catches 1.5× the valley's over Δz = 1049 m
    store.upsert_rows(conn, _gauge_rows("Z9", 1.5))
    store.upsert_rows(conn, _gauge_rows("DP", 1.0))

    fit = opg.fit_gradient(conn, pair, "2025-02-01T00:00:00Z", "2025-02-02T00:00:00Z")
    assert fit.n_buckets == 1 and fit.n_undercatch_corrected == 1
    expected = 0.5 / (pair.dz_m / 100.0)  # +50 % spread over 1049 m
    assert fit.per_100m_weighted == pytest.approx(expected, rel=1e-6)
    assert fit.per_100m_median == pytest.approx(expected, rel=1e-6)


def test_fit_reports_no_data_rather_than_a_gradient(tmp_path):
    """Z1 and Z2 carry no gauge at all — the fit must say so, not invent one."""
    conn = store.connect(tmp_path / "v.sqlite")
    pair = next(p for p in opg.gauge_pairs() if p.high == "Z1")
    fit = opg.fit_gradient(conn, pair, "2025-02-01T00:00:00Z", "2025-02-02T00:00:00Z")
    assert fit.n_buckets == 0
    assert fit.per_100m_weighted is None and fit.per_100m_median is None


def test_fit_skips_dry_buckets(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    pair = next(p for p in opg.gauge_pairs() if (p.high, p.valley) == ("Z9", "DP"))
    store.upsert_rows(conn, _gauge_rows("Z9", 0.0))
    store.upsert_rows(conn, _gauge_rows("DP", 0.0))
    fit = opg.fit_gradient(conn, pair, "2025-02-01T00:00:00Z", "2025-02-02T00:00:00Z")
    assert fit.n_buckets == 0


def test_gauge_pairs_cover_every_scored_resort():
    pairs = opg.gauge_pairs()
    assert {p.resort for p in pairs} == {"baqueira", "boi-taull", "la-molina"}
    assert all(p.dz_m > 0 for p in pairs)


def test_the_public_verification_page_hides_the_ungated_candidate():
    """The candidate column is scored, but ADR-0003 keeps it off the page until
    it wins — and it has no frozen-baseline counterpart to sit beside anyway."""
    live = {"bucket_6h": {"by_column": {
        "arome_25": {"n_cm": 10, "mae": 1.0},
        "arome_25_opg": {"n_cm": 10, "mae": 0.8},
    }}}
    table = render_verification.metric_table(
        "6 h", render_verification.BUCKET_METRICS, {}, live["bucket_6h"]["by_column"])
    assert "AROME 2.5 km" in table
    assert "opg" not in table.lower()
    assert render_verification.is_candidate("arome_25_opg")
    assert not render_verification.is_candidate("arome_25")


def test_factors_table_flags_inherited_references():
    text = opg._factors_table()
    assert "baqueira" in text and "measured" in text
    assert "inherited" in text  # the XEMA points, until they are probed


def test_archive_env_is_untouched_by_import():
    # opg must not need the datastore just to compute a factor (the site path)
    assert Archive is not None
