import pytest

from minipirineu.config import WETBULB_RH_MAX
from minipirineu.wetbulb import stull_wet_bulb


class TestStullWetBulb:
    def test_paper_worked_example(self):
        # Stull (2011) worked value: T=20 °C, RH=50 % → Tw ≈ 13.7 °C.
        assert stull_wet_bulb(20.0, 50.0) == pytest.approx(13.699, abs=0.02)

    def test_near_saturation_tw_approaches_t(self):
        # At (clamped) high RH the air is near saturated: Tw ≈ T.
        for t in (-5.0, 0.0, 10.0):
            assert stull_wet_bulb(t, 100.0) == pytest.approx(t, abs=0.5)

    def test_dry_air_depresses_wet_bulb_below_t(self):
        # Evaporative cooling: drier air → larger wet-bulb depression.
        t = 5.0
        humid = stull_wet_bulb(t, 90.0)
        dry = stull_wet_bulb(t, 20.0)
        assert dry < humid < t

    def test_rh_is_clamped_to_fit_range(self):
        # RH above the fit ceiling is clamped, so 100 % and 99 % agree exactly.
        assert stull_wet_bulb(3.0, 100.0) == stull_wet_bulb(3.0, WETBULB_RH_MAX)
        # ...and absurd RH does not blow up.
        assert stull_wet_bulb(3.0, 1000.0) == stull_wet_bulb(3.0, WETBULB_RH_MAX)

    def test_missing_inputs_return_none(self):
        assert stull_wet_bulb(None, 50.0) is None
        assert stull_wet_bulb(1.0, None) is None
        assert stull_wet_bulb(None, None) is None
