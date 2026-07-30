"""Previous Runs backtest client (S0.6a/T9).

The parser runs over a byte-faithful recorded response
(tests/fixtures/previous_runs_arome_z1_20250201.json — Z1 coords, 2025-02-01/02,
both AROME models, base + previous_day1 + previous_day2), plus synthetic edge
cases for lead labeling and bucket completeness. No network. The recorded
findings behind these expectations are in docs/notes/previous-runs-coverage.md.
"""

import json
from pathlib import Path

from minipirineu import aggregate, previous_runs
from minipirineu.config import MODELS

FIXTURE = Path(__file__).parent / "fixtures" / "previous_runs_arome_z1_20250201.json"
HD = next(m for m in MODELS if m.column == "arome_hd")
A25 = next(m for m in MODELS if m.column == "arome_25")


def _raw() -> dict:
    return json.loads(FIXTURE.read_bytes())


# --- request building -------------------------------------------------------

def test_previous_var_key_matches_the_probed_structure():
    assert previous_runs.previous_var("temperature_2m", 1) == "temperature_2m_previous_day1"
    assert previous_runs.previous_var("snowfall", 2) == "snowfall_previous_day2"


def test_build_params_requests_previous_day_series_for_both_models():
    p = previous_runs.build_params(42.6, 0.9, 2262, "2025-02-01", "2025-02-07", lead_days=(1,))
    hourly = p["hourly"].split(",")
    assert "snowfall_previous_day1" in hourly
    assert "precipitation_previous_day1" in hourly
    assert "temperature_2m_previous_day1" in hourly
    assert p["models"] == "meteofrance_arome_france_hd,meteofrance_arome_france"
    assert p["timezone"] == "UTC"
    assert p["start_date"] == "2025-02-01" and p["end_date"] == "2025-02-07"
    assert p["elevation"] == 2262


# --- parsing the real fixture -----------------------------------------------

def test_fixture_yields_both_columns_at_24h_lead():
    rows = previous_runs.to_forecast_rows(_raw(), "Z1")
    cols = {r.variable for r in rows}
    assert cols == {"fx.snowfall_cm.arome_25", "fx.snowfall_cm.arome_hd"}
    assert all(r.source == "openmeteo" and r.station == "Z1" for r in rows)
    # 48 h of dense day1 → 8 six-hour buckets per column
    per_col = {c: [r for r in rows if r.variable == c] for c in cols}
    assert len(per_col["fx.snowfall_cm.arome_25"]) == 8
    assert len(per_col["fx.snowfall_cm.arome_hd"]) == 8
    # every row is a fixed 24 h lead: run_time = valid_time − 24 h
    for r in rows:
        v = previous_runs.parse_stamp(r.valid_time_utc)
        run = previous_runs.parse_stamp(r.run_time_utc)
        assert (v - run).total_seconds() == 24 * 3600


def test_native_25_total_matches_the_recorded_snowfall():
    rows = [r for r in previous_runs.to_forecast_rows(_raw(), "Z1")
            if r.variable == "fx.snowfall_cm.arome_25"]
    # the fixture's 2.5 day1 snowfall sums to ~4 cm over the two days
    assert 3.5 <= sum(r.value for r in rows) <= 4.5


def test_hd_snowfall_is_null_so_the_column_is_derived_not_native():
    hourly = _raw()["hourly"]
    # HD serves no native snowfall (M1); its day1 series is all null …
    assert all(v is None for v in hourly["snowfall_previous_day1_meteofrance_arome_france_hd"])
    # … yet the derived HD column still produces buckets (from precip + T)
    hd_rows = [r for r in previous_runs.to_forecast_rows(_raw(), "Z1")
               if r.variable == "fx.snowfall_cm.arome_hd"]
    assert hd_rows and all(r.value is not None for r in hd_rows)


def test_derived_column_equals_aggregate_derive_snowfall():
    hourly = _raw()["hourly"]
    times = hourly["time"]
    precip = hourly["precipitation_previous_day1_meteofrance_arome_france_hd"]
    temp = hourly["temperature_2m_previous_day1_meteofrance_arome_france_hd"]
    expected = previous_runs.bucketize(times, aggregate.derive_snowfall(precip, temp))
    assert previous_runs.column_buckets(hourly, times, HD, 1) == expected


def test_previous_day2_produces_no_rows_for_arome():
    # day2 (~48 h lead) is beyond AROME's horizon and comes back all-null:
    # incomplete buckets → no rows. Missing is missing, never a fabricated 0.
    assert previous_runs.to_forecast_rows(_raw(), "Z1", lead_days=(2,)) == []


# --- lead labeling + bucketing edge cases (synthetic) -----------------------

def _synthetic_25(times, snow_day1):
    """A minimal raw with only AROME 2.5 snowfall_previous_day1 populated."""
    return {"hourly": {"time": times,
                       "snowfall_previous_day1_meteofrance_arome_france": snow_day1}}


def test_run_time_is_valid_minus_24h_in_utc_across_a_dst_boundary():
    # 2025-03-30 is Europe/Madrid spring-forward; in UTC the subtraction is
    # clean — this pins that we never touch local time.
    times = [f"2025-03-30T{h:02d}:00" for h in range(6, 12)]  # one 06:00Z bucket
    rows = previous_runs.to_forecast_rows(_synthetic_25(times, [1.0] * 6), "Z1", lead_days=(1,))
    assert len(rows) == 1
    (r,) = rows
    assert r.variable == "fx.snowfall_cm.arome_25"
    assert r.valid_time_utc == "2025-03-30T06:00:00Z"
    assert r.run_time_utc == "2025-03-29T06:00:00Z"
    assert r.value == 6.0


def test_incomplete_bucket_is_dropped():
    # five hours (missing the sixth) → the 06:00 bucket is not fully covered
    times = [f"2025-02-01T{h:02d}:00" for h in range(6, 11)]
    assert previous_runs.bucketize(times, [1.0] * 5) == []
    # a None inside an otherwise full bucket also drops it
    full = [f"2025-02-01T{h:02d}:00" for h in range(6, 12)]
    assert previous_runs.bucketize(full, [1.0, 1.0, None, 1.0, 1.0, 1.0]) == []


def test_bucketize_sums_a_complete_bucket():
    times = [f"2025-02-01T{h:02d}:00" for h in range(0, 6)]
    assert previous_runs.bucketize(times, [0.1, 0.2, 0.0, 0.0, 0.5, 0.2]) == [
        ("2025-02-01T00:00:00Z", 1.0)
    ]


def test_estimate_call_units_is_bounded_and_positive():
    # one month (~30 days) of 6 series is a handful of units, never zero
    u = previous_runs.estimate_call_units(30, 6)
    assert 1 <= u <= 20
