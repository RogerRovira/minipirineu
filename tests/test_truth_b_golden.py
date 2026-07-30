"""Golden-file truth-B + merged truth over a real windy storm (T7, ADR-0004).

Storm of 2025-03-09 at Z9 (Cadí Nord - Prat d'Aguiló, 2145 m, La Molina's snow
truth) — the one scored high station that reports precip (35), 10 m wind (30)
and gust (50) alongside snow depth (38), so it exercises the whole T7 chain:
gauge undercatch, fresh-snow density, and every A/B gate. The recorded Socrata
slice runs the pipeline offline — parse → store → truth-A + truth-B → merge —
and pins the per-bucket disposition. Numbers are XEMA data; the storm is a
documented, genuinely windy event (gusts to 21 m/s), which is why several
buckets are wind-excluded.

The values encode the current algorithm and its config coefficients; a
deliberate re-tune updates this golden (that is what a golden file is for).
"""

from pathlib import Path

from minipirineu import store, truth_b, xema_opendata

FIXTURE = Path(__file__).parent / "fixtures" / "xema_wide_z9_20250309.json"
START, END = "2025-03-08T00:00:00Z", "2025-03-11T00:00:00Z"

# (bucket_start_utc, truth_cm, method, flags, excluded) — recorded 2026-07-29.
GOLDEN = [
    ("2025-03-08T00:00:00Z", None, "none", (), "incomplete"),   # data starts 05:30
    ("2025-03-08T06:00:00Z", 0.4, "A", ("phase_only",), None),  # warm, HS falling, gauge catching → melt
    ("2025-03-08T12:00:00Z", 0.6, "A", ("phase_only",), None),
    ("2025-03-08T18:00:00Z", None, "none", (), "wind"),         # mean wind 7.0 m/s
    ("2025-03-09T00:00:00Z", None, "none", (), "wind"),         # mean wind 6.5 m/s + rimed catch-up
    ("2025-03-09T06:00:00Z", 9.4, "A+B", (), None),             # calm accumulation, A≈B confirm
    ("2025-03-09T12:00:00Z", None, "none", (), "ab_divergence"),  # 0.7 vs 4.3 cm
    ("2025-03-09T18:00:00Z", 0.5, "A+B", (), None),
    ("2025-03-10T00:00:00Z", 0.0, "A+B", (), None),
    ("2025-03-10T06:00:00Z", 0.8, "A+B", (), None),
    ("2025-03-10T12:00:00Z", 1.0, "A+B", (), None),
    ("2025-03-10T18:00:00Z", 0.0, "B", ("gauge_only",), None),  # HS series ends, gauge intact
]


def _merged(tmp_path):
    conn = store.connect(tmp_path / "v.sqlite")
    store.upsert_rows(conn, xema_opendata.parse_payload(FIXTURE.read_bytes()))
    return truth_b.station_merged_truth(conn, "Z9", START, END)


def test_golden_merged_truth(tmp_path):
    got = [(m.bucket_start_utc, m.truth_cm, m.method, m.flags, m.excluded) for m in _merged(tmp_path)]
    assert got == GOLDEN


def test_golden_exclusion_stats(tmp_path):
    stats = truth_b.exclusion_stats(_merged(tmp_path))
    assert stats["total"] == 12
    assert stats["wind"] == 2          # the two sustained-wind buckets
    assert stats["ab_divergence"] == 1
    assert stats["phase_only"] == 2    # the warm melt front
    assert stats["usable"] == 8


def test_golden_is_physically_sane(tmp_path):
    merged = _merged(tmp_path)
    # never negative snow, and confirmed buckets carry the snow-depth value
    for m in merged:
        if m.truth_cm is not None:
            assert m.truth_cm >= 0.0
    # the single confirmed accumulation bucket is the storm's main fall
    confirmed = [m for m in merged if m.method == "A+B" and m.truth_cm]
    assert max(confirmed, key=lambda m: m.truth_cm).bucket_start_utc == "2025-03-09T06:00:00Z"
