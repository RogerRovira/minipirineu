from datetime import datetime, timedelta, timezone

import pytest

from minipirineu.aggregate import derive_snowfall, floor_to_bucket, snow_ratio, to_buckets


def hourly_times(start: str, n: int) -> list[str]:
    t0 = datetime.fromisoformat(start)
    return [(t0 + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(n)]


# 72 hours starting at local midnight, like a forecast_days=3 response
TIMES = hourly_times("2026-01-10T00:00", 72)
NOW = datetime.fromisoformat("2026-01-10T07:30")


def buckets(snow, precip=None, temp=None, now=NOW):
    precip = precip if precip is not None else [0.0] * len(snow)
    temp = temp if temp is not None else [-2.0] * len(snow)
    return to_buckets(TIMES, snow, precip, temp, now)


class TestSnowRatio:
    def test_full_ratio_when_cold(self):
        assert snow_ratio(-10.0) == pytest.approx(0.45)
        assert snow_ratio(-2.0) == pytest.approx(0.45)

    def test_zero_when_warm(self):
        assert snow_ratio(1.0) == 0.0
        assert snow_ratio(15.0) == 0.0

    def test_linear_taper_in_mixed_range(self):
        assert snow_ratio(-0.5) == pytest.approx(0.225)
        assert snow_ratio(0.0) == pytest.approx(0.15)


class TestDeriveSnowfall:
    def test_cold_hours_convert_at_full_ratio(self):
        assert derive_snowfall([1.0, 2.0], [-3.0, -2.0]) == pytest.approx([0.45, 0.9])

    def test_warm_hours_yield_zero_snow(self):
        assert derive_snowfall([5.0, 5.0], [1.1, 10.0]) == [0.0, 0.0]

    def test_mixed_range_hours_convert_partially(self):
        assert derive_snowfall([2.0], [0.0]) == pytest.approx([0.3])

    def test_missing_inputs_stay_none_not_zero(self):
        assert derive_snowfall([None, 1.0, 1.0], [-2.0, None, -3.0]) == [None, None, 0.45]


class TestToBuckets:
    def test_eight_full_buckets_with_complete_data(self):
        result = buckets([0.1] * 72, [0.2] * 72, [-2.0] * 72)

        assert len(result["intervals"]) == 8
        assert result["effective_horizon_h"] == 48
        # now=07:30 floors to the 06:00 block
        assert result["intervals"][0]["start"] == "2026-01-10T06:00"
        assert result["intervals"][0]["end"] == "2026-01-10T12:00"
        assert result["intervals"][-1]["end"] == "2026-01-12T06:00"
        for interval in result["intervals"]:
            assert interval["snowfall_cm"] == pytest.approx(0.6)
            assert interval["precipitation_mm"] == pytest.approx(1.2)
            assert interval["temperature_c"] == pytest.approx(-2.0)

    def test_bucket_sums_equal_total(self):
        snow = [i * 0.1 for i in range(72)]
        result = buckets(snow)
        assert result["total_snowfall_cm"] == pytest.approx(
            round(sum(iv["snowfall_cm"] for iv in result["intervals"]), 1)
        )
        # hours 06..53 inclusive fall inside the 8 buckets
        assert result["total_snowfall_cm"] == pytest.approx(round(sum(snow[6:54]), 1))

    def test_trailing_nulls_shorten_horizon_instead_of_faking_zeros(self):
        # model horizon ends at hour 36: everything after is null
        result = buckets(
            [0.5] * 36 + [None] * 36,
            [1.0] * 36 + [None] * 36,
            [-1.0] * 36 + [None] * 36,
        )
        assert result["effective_horizon_h"] == 30
        assert len(result["intervals"]) == 5
        assert all(iv["snowfall_cm"] is not None for iv in result["intervals"])
        assert result["intervals"][-1]["end"] == "2026-01-11T12:00"

    def test_partial_bucket_at_horizon_uses_available_hours_only(self):
        # horizon at hour 33 → last bucket (30:00-36:00) has only 3 real hours
        result = buckets(
            [1.0] * 33 + [None] * 39,
            [1.0] * 33 + [None] * 39,
            [-1.0] * 33 + [None] * 39,
        )
        assert result["intervals"][-1]["snowfall_cm"] == pytest.approx(3.0)

    def test_all_null_snowfall_with_real_precip_keeps_bucket(self):
        # AROME HD shape before derivation: no snowfall, real precip/temp
        result = buckets([None] * 72, [1.0] * 72, [5.0] * 72)
        assert len(result["intervals"]) == 8
        for interval in result["intervals"]:
            assert interval["snowfall_cm"] is None
            assert interval["precipitation_mm"] == pytest.approx(6.0)
        assert result["total_snowfall_cm"] == 0

    def test_all_null_series_yields_no_intervals(self):
        result = buckets([None] * 72, [None] * 72, [None] * 72)
        assert result["intervals"] == []
        assert result["effective_horizon_h"] == 0
        assert result["total_snowfall_cm"] == 0

    def test_gap_inside_horizon_raises(self):
        snow, precip, temp = [0.0] * 72, [0.0] * 72, [2.0] * 72
        # hollow out one full bucket (12:00-18:00) inside the horizon
        for i in range(12, 18):
            snow[i] = precip[i] = temp[i] = None
        with pytest.raises(ValueError, match="gap"):
            to_buckets(TIMES, snow, precip, temp, NOW)

    def test_aware_now_is_rejected(self):
        with pytest.raises(ValueError, match="naive"):
            buckets([0.0] * 72, now=datetime.now(timezone.utc))


def test_floor_to_bucket():
    assert floor_to_bucket(datetime(2026, 1, 10, 0, 0)) == datetime(2026, 1, 10, 0, 0)
    assert floor_to_bucket(datetime(2026, 1, 10, 5, 59)) == datetime(2026, 1, 10, 0, 0)
    assert floor_to_bucket(datetime(2026, 1, 10, 23, 1)) == datetime(2026, 1, 10, 18, 0)
