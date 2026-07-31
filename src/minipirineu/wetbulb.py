"""Stull (2011) wet-bulb temperature from dry-bulb T and relative humidity.

Roland Stull, "Wet-Bulb Temperature from Relative Humidity and Air Temperature"
(J. Appl. Meteor. Climatol. 50, 2267–2269, 2011): a single empirical fit for the
wet-bulb temperature Tw at sea-level pressure, accurate to a few tenths of a °C
over −20…+50 °C and RH 5–99 %. Used by S1.1 as the snow/rain phase driver: near
0 °C, dry air evaporatively cools falling precipitation so snow survives well
above freezing, which a dry-bulb-only taper misses. Tw captures that.

Pure function of two scalars, so it is trivially testable against Stull's own
worked value (T=20 °C, RH=50 % → Tw≈13.7 °C). Missing input → None (unknown, not
a fabricated 0), matching the rest of the pipeline.
"""

import math

from minipirineu.config import WETBULB_RH_MAX, WETBULB_RH_MIN


def stull_wet_bulb(t_c, rh_pct):
    """Wet-bulb temperature (°C) from air temperature (°C) and RH (%).

    RH is clamped to Stull's fitted range [WETBULB_RH_MIN, WETBULB_RH_MAX] so
    saturated/very-dry inputs stay inside the empirical fit instead of running
    off it. Either input None → None.
    """
    if t_c is None or rh_pct is None:
        return None
    rh = min(max(rh_pct, WETBULB_RH_MIN), WETBULB_RH_MAX)
    return (
        t_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )
