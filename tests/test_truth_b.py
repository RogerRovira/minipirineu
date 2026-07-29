"""truth-B + quality gates + merged truth (S0.4b/T7, ADR-0004).

Pure functions over synthetic series and hand-built buckets, plus one store
round-trip. The real-storm golden (Z9, precip+wind) lives in
test_truth_b_golden.py.
"""

from datetime import timedelta

from minipirineu import store, truth_b
from minipirineu.config import (
    FRESH_SNOW_DENSITY_MAX,
    GATE_WIND_MEAN_MS,
    UNDERCATCH_CE_FLOOR,
)
from minipirineu.truth import TruthBucket
from minipirineu.truth_b import BucketB, parse_stamp


def _times(n, start="2024-02-01T00:00:00Z", step_min=30):
    t0 = parse_stamp(start)
    return [(t0 + timedelta(minutes=i * step_min)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(n)]


def _bb(start="2024-02-01T00:00:00Z", *, fresh=None, precip=None, wind=None,
        gust=None, temp=None, dhs=None, complete=True):
    return BucketB(start, fresh, precip, wind, gust, temp, dhs, complete)


def _tb(start="2024-02-01T00:00:00Z", *, fresh=None, complete=True):
    return TruthBucket(start, fresh, 12, complete)


# --- undercatch (Kochendorfer 2017 / WMO-SPICE) -----------------------------

def test_catch_efficiency_is_unity_without_wind():
    # no wind, no undercatch — the gauge catches everything
    assert truth_b.catch_efficiency(0.0, -5.0) == 1.0


def test_catch_efficiency_drops_with_wind():
    # more wind blows more snow past an unshielded gauge
    assert truth_b.catch_efficiency(2.0, -5.0) > truth_b.catch_efficiency(6.0, -5.0)


def test_catch_efficiency_lower_when_colder():
    # colder = lighter snow = more easily blown out of the gauge
    assert truth_b.catch_efficiency(4.0, -10.0) < truth_b.catch_efficiency(4.0, 0.0)


def test_catch_efficiency_reference_band():
    # an unshielded gauge at ~5 m/s (gauge height) catches roughly half of snow
    # (Kochendorfer et al. 2017) — assert the transfer function lands in-band
    ce = truth_b.catch_efficiency(5.0, -5.0, wind_factor=1.0)
    assert 0.25 < ce < 0.60


def test_catch_efficiency_floored_in_extreme_wind():
    # past the SPICE fit range CE would run to ~0; the floor keeps 1/CE finite
    assert truth_b.catch_efficiency(50.0, -10.0) == UNDERCATCH_CE_FLOOR


# --- fresh-snow density (Hedstrom & Pomeroy 1998) ---------------------------

def test_density_cold_asymptote_matches_helfricht_mean():
    # very cold snow tends to the D0 asymptote ≈ 68 kg/m³ (Helfricht 2018 mean)
    assert abs(truth_b.new_snow_density(-30.0) - 67.9) < 0.1


def test_density_at_zero_celsius():
    # ρ(0) = 67.9 + 51.3 = 119.2 kg/m³
    assert abs(truth_b.new_snow_density(0.0) - 119.2) < 0.1


def test_density_increases_toward_freezing():
    assert truth_b.new_snow_density(-8.0) < truth_b.new_snow_density(-1.0)


def test_density_clamped_above():
    assert truth_b.new_snow_density(25.0) == FRESH_SNOW_DENSITY_MAX


# --- phase split ------------------------------------------------------------

def test_snow_fraction_endpoints_and_midpoint():
    assert truth_b.snow_fraction(-2.0) == 1.0
    assert truth_b.snow_fraction(3.0) == 0.0
    assert truth_b.snow_fraction(1.25) == 0.5  # halfway between 0.5 and 2.0


# --- gauge → cm -------------------------------------------------------------

def test_gauge_fresh_snow_cold_calm():
    # 1 mm SWE, no wind, cold: cm = 100/ρ(-6) with CE=1, frac=1
    cm = truth_b.gauge_fresh_snow_cm(1.0, 0.0, -6.0)
    assert abs(cm - 100.0 / truth_b.new_snow_density(-6.0)) < 1e-9


def test_gauge_undercatch_raises_estimate():
    # same precip/temp, wind present → CE<1 → more snow inferred than calm
    calm = truth_b.gauge_fresh_snow_cm(2.0, 0.0, -6.0)
    windy = truth_b.gauge_fresh_snow_cm(2.0, 5.0, -6.0)
    assert windy > calm


def test_gauge_rain_yields_zero_snow():
    # warm precip is rain: solid fraction 0 → 0 cm of snow
    assert truth_b.gauge_fresh_snow_cm(5.0, 1.0, 3.0) == 0.0


# --- per-bucket gauge features ----------------------------------------------

def test_compute_truth_b_full_snowy_bucket():
    times = _times(12)  # 00:00..05:30, one 6h bucket, fully covered
    precip = [0.5] * 12
    wind, gust, temp, depth = [1.0] * 12, [1.5] * 12, [-5.0] * 12, [80.0] * 12
    buckets = truth_b.compute_truth_b(times, precip, wind, gust, temp, depth)
    assert len(buckets) == 1
    b = buckets[0]
    assert b.complete is True
    assert b.precip_mm == 6.0  # 12 steps × 0.5 mm
    assert b.fresh_snow_cm == round(truth_b.gauge_fresh_snow_cm(6.0, 1.0, -5.0), 1)


def test_compute_truth_b_missing_gauge_reading_makes_precip_none():
    # a single missing precip step = unknown total → None, never a partial sum
    times = _times(12)
    precip = [0.5] * 12
    precip[4] = None
    b = truth_b.compute_truth_b(times, precip, [1.0] * 12, [1.0] * 12, [-5.0] * 12, [80.0] * 12)[0]
    assert b.precip_mm is None
    assert b.fresh_snow_cm is None


def test_compute_truth_b_gap_marks_incomplete():
    # drop the 02:00–03:30 readings → oversized gap → bucket not covered
    times = _times(12)
    keep = [i for i, t in enumerate(times) if t[11:13] not in ("02", "03")]
    times = [times[i] for i in keep]
    n = len(times)
    b = truth_b.compute_truth_b(times, [0.5] * n, [1.0] * n, [1.0] * n, [-5.0] * n, [80.0] * n)[0]
    assert b.complete is False
    assert b.precip_mm is None


# --- A/B gates + merge ------------------------------------------------------

def test_gate_wind_excludes_bucket():
    # sustained mean wind above the drifting threshold → redistribution → exclude
    f = _bb(wind=GATE_WIND_MEAN_MS + 1.0, gust=20.0, temp=-5.0, precip=2.0, fresh=4.0)
    m = truth_b.merge_truth([_tb(fresh=5.0)], [f])[0]
    assert m.excluded == "wind"
    assert m.truth_cm is None


def test_gust_alone_does_not_exclude():
    # a high peak gust but calm mean wind must NOT nuke the bucket
    f = _bb(fresh=9.0, precip=5.0, wind=1.0, gust=18.0, temp=-4.0, dhs=8.0)
    m = truth_b.merge_truth([_tb(fresh=10.0)], [f])[0]
    assert m.excluded is None
    assert m.method == "A+B"


def test_merge_confirms_when_a_and_b_agree():
    f = _bb(fresh=11.0, precip=6.0, wind=1.0, gust=2.0, temp=-4.0, dhs=10.0)
    m = truth_b.merge_truth([_tb(fresh=10.0)], [f])[0]
    assert m.excluded is None
    assert m.method == "A+B"
    assert m.truth_cm == 10.0  # truth-A is the reported cm


def test_gate_ab_divergence_excludes_bucket():
    f = _bb(fresh=2.0, precip=1.0, wind=1.0, gust=2.0, temp=-4.0, dhs=8.0)
    m = truth_b.merge_truth([_tb(fresh=10.0)], [f])[0]  # 10 vs 2, gap 8 > max(3, 6)
    assert m.excluded == "ab_divergence"
    assert m.truth_cm is None


def test_merge_unconfirmed_when_no_gauge():
    # Z1-style station: snow depth but no wind/gauge → truth-A stands, flagged
    f = _bb(fresh=None, wind=None, gust=None, temp=None, dhs=None)
    m = truth_b.merge_truth([_tb(fresh=8.0)], [f])[0]
    assert m.method == "A"
    assert m.flags == ("unconfirmed",)
    assert m.truth_cm == 8.0


def test_merge_gauge_only_when_no_snow_depth():
    # snow-depth truth missing but the gauge is intact → fall back to B, flagged
    f = _bb(fresh=5.0, precip=3.0, wind=1.0, gust=2.0, temp=-4.0, dhs=None)
    m = truth_b.merge_truth([_tb(fresh=None, complete=False)], [f])[0]
    assert m.method == "B"
    assert m.flags == ("gauge_only",)
    assert m.truth_cm == 5.0


def test_melt_signature_flags_phase_only_over_divergence():
    # T>0, depth falling, gauge accumulating: A≈0 and B>0 disagree, but that is
    # a melt bucket, not a bad reading — flag phase_only, do NOT exclude
    f = _bb(fresh=4.0, precip=2.0, wind=1.0, gust=2.0, temp=1.0, dhs=-3.0)
    m = truth_b.merge_truth([_tb(fresh=0.0)], [f])[0]
    assert m.excluded is None
    assert m.flags == ("phase_only",)


def test_merge_incomplete_both_sides():
    f = _bb(fresh=None, complete=False)
    m = truth_b.merge_truth([_tb(fresh=None, complete=False)], [f])[0]
    assert m.excluded == "incomplete"
    assert m.truth_cm is None


def test_exclusion_stats_counts():
    merged = truth_b.merge_truth(
        [_tb("2024-02-01T00:00:00Z", fresh=10.0), _tb("2024-02-01T06:00:00Z", fresh=5.0)],
        [_bb("2024-02-01T00:00:00Z", wind=9.0, gust=20.0, temp=-5.0),  # wind-excluded
         _bb("2024-02-01T06:00:00Z", fresh=5.0, wind=1.0, gust=2.0, temp=-4.0, dhs=4.0)],
    )
    stats = truth_b.exclusion_stats(merged)
    assert stats["total"] == 2
    assert stats["excluded"] == 1
    assert stats["wind"] == 1
    assert stats["usable"] == 1


# --- store round-trip -------------------------------------------------------

def test_station_merged_truth_reads_from_store(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    times = _times(13)  # 00:00..06:00 so the 00–06 bucket is fully covered
    depths = [80.0] * 13
    rows = []
    for t, d in zip(times, depths):
        rows.append(store.Row("xema", "Z9", t, t, "obs.gruix_neu", d))
        rows.append(store.Row("xema", "Z9", t, t, "obs.temperatura", -5.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.precipitacio", 0.5))
        rows.append(store.Row("xema", "Z9", t, t, "obs.vent_velocitat", 1.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.vent_ratxa", 2.0))
    store.upsert_rows(conn, rows)

    merged = truth_b.station_merged_truth(
        conn, "Z9", "2024-02-01T00:00:00Z", "2024-02-01T12:30:00Z", smooth_window=1
    )
    first = {m.bucket_start_utc: m for m in merged}["2024-02-01T00:00:00Z"]
    # flat depth → truth-A 0; gauge caught snow → they disagree by construction,
    # but a calm cold bucket is a legitimate A/B divergence (gauge says snow, the
    # sonde says none): exercises the merge end to end from the store.
    assert first.method in {"A+B", "A", "none"}
    assert first.excluded in {None, "ab_divergence"}
