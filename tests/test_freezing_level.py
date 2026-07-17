"""Freezing-level derivation, ported from PiriNeu with semantics UNCHANGED
(its module docstring: hard-won edge cases, do not "fix"):

- whole column above 0 °C → None (no freezing level in range);
- whole column below 0 °C → lowest level's height, capped=True;
- inversions → the LOWEST crossing (conservative for snow line), with
  n_crossings recording that an inversion was present.

Mini extends the scan to 6 levels (600/500 hPa added: warm SW flows push
the 0 °C level above 700 hPa — handoff 2026-07-16).
"""

from minipirineu.freezing_level import derive_freezing_level

# 1000, 925, 850, 700, 600, 500 hPa — geopotential heights, ground -> up
HEIGHTS = [110.0, 780.0, 1450.0, 3010.0, 4200.0, 5600.0]


def test_normal_crossing_is_interpolated():
    fl = derive_freezing_level([5.0, 2.0, -1.0, -8.0, -15.0, -25.0], HEIGHTS)
    # crossing between 925 hPa (2 °C, 780 m) and 850 hPa (-1 °C, 1450 m)
    assert fl.height_m == 780.0 + (1450.0 - 780.0) * (2.0 / 3.0)
    assert fl.capped is False
    assert fl.n_crossings == 1


def test_inversion_takes_lowest_crossing():
    fl = derive_freezing_level([2.0, -1.0, 1.0, -3.0, -10.0, -20.0], HEIGHTS)
    # three sign changes; the lowest crossing (1000->925 hPa) wins
    assert fl.height_m == 110.0 + (780.0 - 110.0) * (2.0 / 3.0)
    assert fl.n_crossings == 3
    assert fl.capped is False


def test_whole_column_frozen_caps_at_lowest_level():
    fl = derive_freezing_level([-1.0, -2.0, -5.0, -12.0, -20.0, -30.0], HEIGHTS)
    assert fl.height_m == 110.0
    assert fl.capped is True


def test_cold_bottom_with_warm_layer_takes_lowest_crossing():
    # NOT capped: the ported semantics interpolate the lowest sign change
    fl = derive_freezing_level([-0.5, 3.0, -1.0, -6.0, -12.0, -22.0], HEIGHTS)
    expected = 110.0 + (780.0 - 110.0) * (-0.5 / (-0.5 - 3.0))
    assert fl.height_m == expected
    assert fl.capped is False
    assert fl.n_crossings == 2


def test_whole_column_warm_returns_none():
    fl = derive_freezing_level([12.0, 9.0, 5.0, 1.0, 0.5, 0.2], HEIGHTS)
    assert fl.height_m is None
    assert fl.capped is False


def test_missing_levels_are_skipped():
    fl = derive_freezing_level(
        [3.0, None, -1.0, -5.0, None, -20.0],
        [110.0, None, 1450.0, 3010.0, None, 5600.0],
    )
    # interpolates between 1000 hPa (3 °C) and 850 hPa (-1 °C)
    assert fl.height_m == 110.0 + (1450.0 - 110.0) * (3.0 / 4.0)


def test_fewer_than_two_valid_levels_returns_none():
    fl = derive_freezing_level(
        [-3.0, None, None, None, None, None],
        [110.0, None, None, None, None, None],
    )
    assert fl.height_m is None
    assert fl.capped is False
