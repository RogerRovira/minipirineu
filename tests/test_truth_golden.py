"""Golden-file truth-A over a real documented storm (T6, ADR-0004).

Storm of 2025-03-09 at Z2 (Boí, 2537 m, La Molina's/Boí's high snow truth):
snow depth climbs ~60 → ~106 cm through 09 Mar. The recorded Socrata slice runs
the whole pipeline offline — parse → verification store → truth-A — and pins the
per-6h-bucket fresh snow so any change in the despike/smoothing/settling chain
shows up here. Numbers are XEMA data; the ICGC InfoGruixNEU chart is the user's
eyeball cross-check, never a source of numbers.

The values encode the current algorithm and its config coefficients; a
deliberate re-tune updates this golden (that is what a golden file is for).
"""

from pathlib import Path

from minipirineu import store, truth, xema_opendata

FIXTURE = Path(__file__).parent / "fixtures" / "xema_storm_z2_20250309.json"
START, END = "2025-03-08T00:00:00Z", "2025-03-11T00:00:00Z"

# (bucket_start_utc, fresh_snow_cm, complete) — recorded 2026-07-18.
GOLDEN = [
    ("2025-03-08T00:00:00Z", 4.7, True),
    ("2025-03-08T06:00:00Z", 2.7, True),
    ("2025-03-08T12:00:00Z", 3.0, True),
    ("2025-03-08T18:00:00Z", 0.6, True),
    ("2025-03-09T00:00:00Z", 0.0, True),
    ("2025-03-09T06:00:00Z", 26.7, True),   # storm onset (sensor releases a rimed catch-up)
    ("2025-03-09T12:00:00Z", 18.7, True),
    ("2025-03-09T18:00:00Z", 10.2, True),
    ("2025-03-10T00:00:00Z", 0.0, True),
    ("2025-03-10T06:00:00Z", 2.5, True),
    ("2025-03-10T12:00:00Z", 1.8, True),
    ("2025-03-10T18:00:00Z", None, False),  # data ends mid-bucket → incomplete, never 0
]


def _buckets(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    store.upsert_rows(conn, xema_opendata.parse_payload(FIXTURE.read_bytes()))
    return truth.station_truth_a(conn, "Z2", START, END)


def test_golden_storm_buckets(tmp_path):
    got = [(b.bucket_start_utc, b.fresh_snow_cm, b.complete) for b in _buckets(tmp_path)]
    assert got == GOLDEN


def test_golden_storm_is_physically_sane(tmp_path):
    buckets = _buckets(tmp_path)
    complete = [b for b in buckets if b.complete]
    # never negative snowfall
    assert all(b.fresh_snow_cm >= 0.0 for b in complete)
    # the 09 Mar onset bucket is the single biggest
    peak = max(complete, key=lambda b: b.fresh_snow_cm)
    assert peak.bucket_start_utc == "2025-03-09T06:00:00Z"
    # storm total exceeds the raw net depth gain (settling was added back) yet
    # stays in a plausible band for a ~2-day Pyrenean storm
    total = sum(b.fresh_snow_cm for b in complete)
    assert 47.0 < total < 90.0
