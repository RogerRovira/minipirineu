"""truth-A: fresh snow from snow-depth increments (S0.4a/T6, ADR-0004).

Pure functions over synthetic series plus one store round-trip. The golden
storm test (real Socrata data) lives in test_truth_golden.py.
"""

import math
from datetime import timedelta

from minipirineu import store, truth
from minipirineu.config import SETTLING_C3_PER_S


def _series(depths, start="2024-02-01T00:00:00Z", step_min=30):
    """Regular 30-min ISO-UTC timeline for a depth list → (times, depths)."""
    t0 = truth.parse_stamp(start)
    stamps = [
        (t0 + timedelta(minutes=i * step_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(len(depths))
    ]
    return stamps, list(depths)


def _simulate_storm(hn_per_step, base=0.0, t_c=0.0, step_s=1800):
    """Build an OBSERVED HS series consistent with production settling: known
    new-snow inputs, each 30-min step settling the young layers. Inversion by
    compute_truth_a must recover the input total (a round-trip on the math)."""
    r = truth.settling_rate_per_step(t_c, step_s)
    layers = []  # thickness of each young layer
    depth = base
    depths = [base]
    for hn in hn_per_step:
        settle = sum(layers) * r
        layers = [th * (1 - r) for th in layers]
        depth = depth - settle + hn
        if hn > 0:
            layers.append(hn)
        depths.append(round(depth, 4))
    return depths


# --- despike (Hampel) -------------------------------------------------------

def test_despike_removes_isolated_spike():
    # a one-sample ultrasonic spike (bird, glitch) is replaced by the local median
    depths = [50.0, 50.0, 50.0, 200.0, 50.0, 50.0, 50.0]
    out = truth.despike(depths)
    assert out == [50.0] * 7


def test_despike_keeps_a_real_snowfall_step():
    # a sustained rise is signal, not a spike — it must survive untouched
    depths = [50.0, 50.0, 50.0, 65.0, 65.0, 65.0, 65.0]
    assert truth.despike(depths) == depths


def test_despike_flat_series_flags_nothing():
    # MAD=0 on a flat window must not blow up and flag every point
    assert truth.despike([40.0] * 7) == [40.0] * 7


def test_despike_preserves_none_gaps():
    # missing stays missing; a None is never filled in by the filter
    out = truth.despike([50.0, 50.0, None, 50.0, 50.0])
    assert out[2] is None
    assert out[0] == out[1] == out[3] == out[4] == 50.0


# --- smoothing --------------------------------------------------------------

def test_smooth_window_one_is_identity():
    assert truth.smooth([1.0, 2.0, 3.0], window=1) == [1.0, 2.0, 3.0]


def test_smooth_preserves_none_gaps():
    assert truth.smooth([10.0, 10.0, None, 10.0, 10.0], window=3)[2] is None


def test_smoothing_damps_jitter_overcount():
    # ±2 cm sensor jitter around a flat pack: unsmoothed, every up-tick is
    # summed as phantom snow; smoothing must cut most of it away
    import random

    rng = random.Random(0)
    jitter = [100.0 + rng.uniform(-2.0, 2.0) for _ in range(48)]  # 24 h
    times, _ = _series(jitter)
    raw = truth.compute_truth_a(times, jitter, temp_c=[-5.0] * 48, smooth_window=1)
    smoothed = truth.compute_truth_a(times, jitter, temp_c=[-5.0] * 48, smooth_window=5)
    raw_total = sum(b.fresh_snow_cm for b in raw if b.complete and b.fresh_snow_cm)
    sm_total = sum(b.fresh_snow_cm for b in smoothed if b.complete and b.fresh_snow_cm)
    assert raw_total > 3.0  # the phantom is real and large unsmoothed
    assert sm_total < 0.4 * raw_total


# --- settling rate ----------------------------------------------------------

def test_settling_rate_matches_anderson_at_zero():
    # at 0 °C the destructive-metamorphism rate is c3 per second (~1%/h)
    step_s = 1800
    expected = 1 - math.exp(-SETTLING_C3_PER_S * step_s)
    assert truth.settling_rate_per_step(0.0, step_s) == expected


def test_settling_rate_slower_when_colder():
    # colder snow settles more slowly (exp(c4·T), T<0)
    assert truth.settling_rate_per_step(-10.0, 1800) < truth.settling_rate_per_step(0.0, 1800)


def test_settling_temperature_proxy_capped_at_zero():
    # snow surface can't exceed 0 °C; +5 air temp is treated like 0
    assert truth.settling_rate_per_step(5.0, 1800) == truth.settling_rate_per_step(0.0, 1800)


# --- no-snow day: never negative snowfall -----------------------------------

def test_settling_only_day_yields_zero_never_negative():
    # a monotonically settling old pack, no snowfall: every complete bucket is
    # exactly 0 cm — settling is not mistaken for melt-driven negative snow
    times, depths = _series([100.0 - 0.1 * i for i in range(60)])  # 30 h
    buckets = truth.compute_truth_a(times, depths, temp_c=[-5.0] * len(times), smooth_window=1)
    complete = [b for b in buckets if b.complete]
    assert complete  # interior buckets are fully covered
    assert all(b.fresh_snow_cm == 0.0 for b in complete)


# --- storm recovery within tolerance ----------------------------------------

def test_storm_recovers_input_total_within_tolerance():
    # 8 × 2.5 cm = 20 cm falls inside one 6h UTC bucket, padded so the bucket is
    # complete; the settling correction must recover ~20, more than raw ΔHS
    hn = [0.0, 0.0] + [2.5] * 8 + [0.0] * 3  # 13 increments → 14 readings 00:00–06:30
    depths = _simulate_storm(hn, base=0.0, t_c=0.0)
    times, _ = _series(depths)
    buckets = truth.compute_truth_a(times, depths, temp_c=[0.0] * len(times), smooth_window=1)
    complete = [b for b in buckets if b.complete]
    assert len(complete) == 1  # only the 00–06 bucket is fully covered
    assert complete[0].fresh_snow_cm == 20.0
    # and the correction added back the settled snow: raw ΔHS is < 20
    assert depths[-1] - depths[0] < 20.0


# --- forward-labeled bucket attribution -------------------------------------

def _buckets_by_start(buckets):
    return {b.bucket_start_utc: b for b in buckets}


def test_jump_before_boundary_lands_in_earlier_bucket():
    # a rise measured AT 06:00 fell during 05:30–06:00 → belongs to 00–06
    depths = [100.0] * 12 + [110.0] * 13  # 00:00..12:00, step at 05:30→06:00
    times, _ = _series(depths)
    b = _buckets_by_start(truth.compute_truth_a(times, depths, temp_c=[-3.0] * len(depths), smooth_window=1))
    assert b["2024-02-01T00:00:00Z"].fresh_snow_cm == 10.0
    assert b["2024-02-01T06:00:00Z"].fresh_snow_cm == 0.0


def test_jump_after_boundary_lands_in_later_bucket():
    # a rise measured AT 06:30 fell during 06:00–06:30 → belongs to 06–12
    depths = [100.0] * 13 + [110.0] * 12  # step at 06:00→06:30
    times, _ = _series(depths)
    b = _buckets_by_start(truth.compute_truth_a(times, depths, temp_c=[-3.0] * len(depths), smooth_window=1))
    assert b["2024-02-01T00:00:00Z"].fresh_snow_cm == 0.0
    assert b["2024-02-01T06:00:00Z"].fresh_snow_cm == 10.0


# --- data gaps: missing is missing ------------------------------------------

def test_oversized_gap_marks_bucket_incomplete_not_zero():
    # drop the 02:00–04:00 readings inside the 00–06 bucket: the accumulation
    # over that gap is unknown, so the bucket is None, never a fabricated 0
    times, depths = _series([100.0] * 25)  # 00:00..12:00
    keep = [i for i, t in enumerate(times) if t[11:13] not in ("02", "03")]
    times = [times[i] for i in keep]  # 01:30 → 04:00 is now a 2.5 h gap
    depths = [depths[i] for i in keep]
    b = _buckets_by_start(truth.compute_truth_a(times, depths, temp_c=[-3.0] * len(depths), smooth_window=1))
    assert b["2024-02-01T00:00:00Z"].complete is False
    assert b["2024-02-01T00:00:00Z"].fresh_snow_cm is None


# --- store round-trip -------------------------------------------------------

def test_station_truth_a_reads_from_store(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    depths = [100.0] * 12 + [108.0] * 13  # +8 cm at 05:30→06:00
    times, _ = _series(depths)
    rows = [
        store.Row("xema", "Z1", t, t, "obs.gruix_neu", d)
        for t, d in zip(times, depths)
    ]
    rows += [store.Row("xema", "Z1", t, t, "obs.temperatura", -4.0) for t in times]
    store.upsert_rows(conn, rows)

    buckets = truth.station_truth_a(
        conn, "Z1", "2024-02-01T00:00:00Z", "2024-02-01T12:30:00Z", smooth_window=1
    )
    by_start = _buckets_by_start(buckets)
    assert by_start["2024-02-01T00:00:00Z"].fresh_snow_cm == 8.0
