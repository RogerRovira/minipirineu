"""Pure-logic tests for the S1.1 phase go/no-go evaluator (scripts/eval_s11_phase.py).

The DB/archive-facing parts are exercised by running the script on the real
backtest datastore; here we pin the tricky pure helpers: the 6 h mean bucketizer
(must align key-for-key with previous_runs.bucketize) and the per-column phase +
cm scoring math. scripts/ is not a package, so load the module by path.
"""

import importlib.util
from pathlib import Path

import pytest

from minipirineu import previous_runs

_SPEC = importlib.util.spec_from_file_location(
    "eval_s11_phase", Path(__file__).parent.parent / "scripts" / "eval_s11_phase.py")
e = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(e)


def test_station_from_name_extracts_codi():
    assert e._station_from_name(Path("20260729T205608Z_Z9_2024-01.json.gz")) == "Z9"


def test_bucket_mean_averages_full_buckets_and_aligns_with_bucketize():
    times = [f"2025-02-01T{h:02d}:00" for h in range(0, 6)]
    temps = [-1.0, -1.0, -2.0, -2.0, 0.0, 0.0]           # mean = -1.0
    means = e.bucket_mean(times, temps)
    assert means == {"2025-02-01T00:00:00Z": pytest.approx(-1.0)}
    # same bucket key the cm bucketizer emits → the forecast/truth join lines up
    (cm_key, _), = previous_runs.bucketize(times, [1.0] * 6)
    assert set(means) == {cm_key}


def test_bucket_mean_drops_incomplete_or_null_buckets():
    times = [f"2025-02-01T{h:02d}:00" for h in range(0, 5)]   # only 5 of 6 hours
    assert e.bucket_mean(times, [0.0] * 5) == {}
    full = [f"2025-02-01T{h:02d}:00" for h in range(0, 6)]
    assert e.bucket_mean(full, [0.0, 0.0, None, 0.0, 0.0, 0.0]) == {}


def _ev(band_t, truth_cm, dry_cm, *, phase_only=False, key=("Z1", "b")):
    return e.Event(band_t, truth_cm, phase_only, dry_cm, key)


def test_score_column_phase_and_mae_on_marginal_only():
    events = [
        _ev(0.5, truth_cm=3.0, dry_cm=3.0),    # marginal, obs snow, fc snow → hit; err 0
        _ev(-1.0, truth_cm=3.0, dry_cm=0.0),   # marginal, obs snow, fc rain → miss; err 3
        _ev(6.0, truth_cm=0.0, dry_cm=0.0),    # NOT marginal → ignored by both metrics
    ]
    s = e.score_column(events, lambda ev: ev.dry_cm)
    assert s["n_events"] == 2
    assert s["hit_rate"] == pytest.approx(0.5)
    assert s["mae"] == pytest.approx(1.5)      # (0 + 3) / 2


def test_score_column_excludes_phase_only_from_cm_but_not_phase():
    events = [
        _ev(0.0, truth_cm=5.0, dry_cm=5.0),                    # cm sample + phase hit
        _ev(0.0, truth_cm=0.0, dry_cm=9.0, phase_only=True),  # phase only: no cm, but a phase call
    ]
    s = e.score_column(events, lambda ev: ev.dry_cm)
    assert s["n_events"] == 2                  # both are marginal phase events
    assert s["mae"] == pytest.approx(0.0)      # only the first, perfect, cm-eligible bucket
