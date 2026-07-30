"""verify.py metric engine (S0.5/T8).

Synthetic pairs with exact expected scores pin every metric; one store
round-trip exercises the pair builder against real truth code (T7). The metric
engine has no knowledge of backtest vs live — that symmetry is the point.
"""

import json

from minipirineu import store, verify
from minipirineu.truth import parse_stamp
from minipirineu.verify import Pair


def _pair(fc, tc, *, column="arome_hd", station="Z9", resort="la-molina",
          run="2025-03-09T00:00:00Z", valid="2025-03-09T06:00:00Z", phase_only=False):
    lead = (parse_stamp(valid) - parse_stamp(run)).total_seconds() / 3600.0
    return Pair(column, station, resort, run, valid, lead, fc, tc, phase_only)


# --- accumulation -----------------------------------------------------------

def test_perfect_forecast_scores_perfectly():
    m = verify.bucket_metrics([_pair(5, 5), _pair(10, 10), _pair(2, 2)])
    assert m["n_cm"] == 3
    assert m["mae"] == 0.0
    assert m["bias"] == 0.0
    assert m["dead_band_rate"] == 1.0
    assert (m["pod"], m["far"], m["csi"]) == (1.0, 0.0, 1.0)


def test_constant_bias_recovered():
    m = verify.bucket_metrics([_pair(7, 5), _pair(12, 10), _pair(4, 2)])
    assert m["mae"] == 2.0
    assert m["bias"] == 2.0
    # every error is exactly 2 cm = the absolute dead band → all hits
    assert m["dead_band_rate"] == 1.0


def test_all_miss_events():
    m = verify.bucket_metrics([_pair(0, 5), _pair(0, 10)])
    assert m["pod"] == 0.0
    assert m["csi"] == 0.0
    assert m["far"] is None          # no forecast events → FAR undefined
    assert m["misses"] == 2
    assert m["mae"] == 7.5
    assert m["bias"] == -7.5
    assert m["dead_band_rate"] == 0.0


def test_all_false_alarms():
    m = verify.bucket_metrics([_pair(5, 0), _pair(8, 0)])
    assert m["pod"] is None          # no observed events → POD undefined
    assert m["far"] == 1.0
    assert m["csi"] == 0.0
    assert m["false_alarms"] == 2


# --- dead band edges --------------------------------------------------------

def test_dead_band_absolute_edge_at_2cm():
    assert verify.bucket_metrics([_pair(3, 1)])["dead_band_rate"] == 1.0     # err 2 = max(2, .2)
    assert verify.bucket_metrics([_pair(3.1, 1)])["dead_band_rate"] == 0.0   # err 2.1 > 2


def test_dead_band_fractional_edge_at_20pct():
    assert verify.bucket_metrics([_pair(24, 20)])["dead_band_rate"] == 1.0   # err 4 = 20%
    assert verify.bucket_metrics([_pair(24.1, 20)])["dead_band_rate"] == 0.0  # err 4.1 > 4


# --- phase_only -------------------------------------------------------------

def test_phase_only_excluded_from_cm_but_scores_events():
    pairs = [_pair(5, 5), _pair(0, 10, phase_only=True)]
    m = verify.bucket_metrics(pairs)
    assert m["n_cm"] == 1            # the phase_only pair is not a cm sample
    assert m["mae"] == 0.0          # …so cm MAE sees only the perfect pair
    assert m["n_event"] == 2        # but it IS an event sample
    assert m["misses"] == 1         # obs 10 ≥ 1, forecast 0 → a miss
    assert m["pod"] == 0.5


# --- grouping ---------------------------------------------------------------

def test_group_metrics_splits_by_column():
    pairs = [_pair(5, 5, column="arome_hd"), _pair(0, 5, column="arome_25")]
    g = verify.group_metrics(pairs, ("column",))
    assert g[("arome_hd",)]["mae"] == 0.0
    assert g[("arome_25",)]["mae"] == 5.0


# --- 24 h totals + snow days ------------------------------------------------

def _day(fc4, tc4, *, run="2025-03-09T00:00:00Z", date="2025-03-09", **kw):
    hours = ("00", "06", "12", "18")
    return [_pair(fc, tc, run=run, valid=f"{date}T{h}:00:00Z", **kw)
            for fc, tc, h in zip(fc4, tc4, hours)]


def test_daily_totals_sum_complete_days_only():
    pairs = _day([1, 1, 1, 1], [2, 0, 0, 0])                       # complete day → total
    pairs += _day([1, 1, 1], [2, 0, 0], date="2025-03-10")[:3]     # 3 buckets → dropped
    totals = verify.daily_totals(pairs)
    assert len(totals) == 1
    assert totals[0].forecast_cm == 4.0
    assert totals[0].truth_cm == 2.0


def test_daily_totals_by_date_groups_fixed_lead_buckets_across_runs():
    # the backtest shape: each 6 h bucket of a UTC day comes from its own run
    # (run = valid − 24 h), so no run covers the whole day.
    hours = ("00", "06", "12", "18")
    pairs = [_pair(1, 1, run=f"2025-03-08T{h}:00:00Z", valid=f"2025-03-09T{h}:00:00Z")
             for h in hours]
    # "run" grouping (live default) finds no complete day — 4 distinct runs
    assert verify.daily_totals(pairs, daily_by="run") == []
    # "date" grouping sums the calendar day regardless of run
    totals = verify.daily_totals(pairs, daily_by="date")
    assert len(totals) == 1
    assert totals[0].forecast_cm == 4.0 and totals[0].truth_cm == 4.0
    assert totals[0].date_utc == "2025-03-09"


def test_daily_totals_by_date_dedups_buckets_across_runs():
    # a live/multi-run store scored with daily_by="date": a valid bucket covered
    # by more than one run must count once, never double the day.
    hours = ("00", "06", "12", "18")
    day = [_pair(1, 1, run=f"2025-03-08T{h}:00:00Z", valid=f"2025-03-09T{h}:00:00Z")
           for h in hours]
    # a genuine 4-bucket day + a duplicate 00Z from an older run → still one day,
    # 00Z counted once (the old count-the-list code saw 5 pairs and dropped it)
    totals = verify.daily_totals(
        day + [_pair(1, 1, run="2025-03-07T00:00:00Z", valid="2025-03-09T00:00:00Z")],
        daily_by="date")
    assert len(totals) == 1 and totals[0].forecast_cm == 4.0
    # the corruption the fix prevents: 2 valid buckets each covered by 2 runs =
    # 4 pairs. The old code counted 4 pairs as a complete day and double-summed;
    # now the day has only 2 distinct buckets → not complete → dropped.
    two = [_pair(3, 3, run=r, valid=v)
           for v in ("2025-03-09T00:00:00Z", "2025-03-09T06:00:00Z")
           for r in ("2025-03-08T00:00:00Z", "2025-03-07T00:00:00Z")]
    assert verify.daily_totals(two, daily_by="date") == []


def test_snow_day_metrics_flag_a_day():
    totals = verify.daily_totals(_day([2, 1, 1, 0], [3, 0, 0, 0]))  # 4 cm fx, 3 cm obs
    m = verify.snow_day_metrics(totals)[("arome_hd",)]
    assert m["hits"] == 1           # both ≥ 1 cm/24h
    assert m["pod"] == 1.0
    assert m["mae"] == 1.0          # |4 − 3|


# --- store convention + report ----------------------------------------------

def test_forecast_variable_roundtrip():
    assert verify.forecast_variable("arome_hd") == "fx.snowfall_cm.arome_hd"
    assert verify.forecast_column("fx.snowfall_cm.arome_hd") == "arome_hd"
    assert verify.forecast_column("obs.gruix_neu") is None


def test_report_is_json_and_markdown_serializable():
    report = verify.verify_report([_pair(5, 5), _pair(0, 5, column="arome_25")])
    assert json.loads(verify.to_json(report))["n_pairs"] == 2
    md = verify.to_markdown(report)
    assert "Verification report" in md
    assert "arome_hd" in md


def _times(n, start="2024-02-01T00:00:00Z"):
    t0 = parse_stamp(start)
    from datetime import timedelta
    return [(t0 + timedelta(minutes=30 * i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(n)]


def test_build_pairs_joins_forecast_to_truth(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    times = _times(13)  # 00:00..06:00 → the 00–06 truth bucket is fully covered
    rows = []
    for t in times:  # calm, no precip, flat pack → truth-A = truth-B = 0, not excluded
        rows.append(store.Row("xema", "Z9", t, t, "obs.gruix_neu", 80.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.temperatura", -5.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.precipitacio", 0.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.vent_velocitat", 1.0))
        rows.append(store.Row("xema", "Z9", t, t, "obs.vent_ratxa", 2.0))
    # a forecast for the 00–06 bucket (has truth) and one for 18:00 (no truth)
    rows.append(store.Row("openmeteo", "Z9", "2024-02-01T00:00:00Z",
                          "2024-02-01T00:00:00Z", "fx.snowfall_cm.arome_hd", 3.0))
    rows.append(store.Row("openmeteo", "Z9", "2024-02-01T00:00:00Z",
                          "2024-02-01T18:00:00Z", "fx.snowfall_cm.arome_hd", 9.0))
    store.upsert_rows(conn, rows)

    pairs = verify.build_pairs(conn, ["Z9"], "2024-02-01T00:00:00Z", "2024-02-01T12:30:00Z")
    assert len(pairs) == 1          # the 18:00 forecast has no truth bucket → dropped
    (p,) = pairs
    assert p.column == "arome_hd"
    assert p.forecast_cm == 3.0
    assert p.truth_cm == 0.0
    assert p.lead_h == 0.0
