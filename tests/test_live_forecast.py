"""Live forecast pairing (S0.7/T11b).

Runs over a trimmed real archived run (tests/fixtures/openmeteo_live_baqueira_2600.json,
Europe/Madrid local time) plus synthetic truth. Pins the band→station map, the
archive-filename parse, the local→UTC re-bucketing onto the truth grid, and the
ingest→verify round-trip. No network.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from minipirineu import live_forecast as lf
from minipirineu import store, verify
from minipirineu.archive import Archive
from minipirineu.truth import parse_stamp

FIXTURE = Path(__file__).parent / "fixtures" / "openmeteo_live_baqueira_2600.json"
RUN = datetime(2026, 7, 20, 9, 0, 40, tzinfo=timezone.utc)


def test_high_band_targets_map_to_snow_truth_stations():
    t = lf.high_band_targets()
    assert t[("baqueira", 2600)] == "Z1"
    assert t[("boi-taull", 2750)] == "Z2"
    assert t[("la-molina", 2500)] == "Z9"
    # only the three snow-truth resorts' high bands are targets
    assert set(t.values()) == {"Z1", "Z2", "Z9"}


def test_parse_archive_name_splits_stamp_resort_elevation():
    p = Path("raw/openmeteo/2026/07/20/20260720T090040Z_boi-taull_2750.json.gz")
    run, resort, elev = lf.parse_archive_name(p)
    assert run == "2026-07-20T09:00:40Z"
    assert resort == "boi-taull" and elev == 2750


def test_raw_to_rows_rebuckets_local_run_to_utc():
    rows = lf.raw_to_rows(FIXTURE.read_bytes(), "2026-07-20T09:00:40Z", "Z1")
    assert {r.variable for r in rows} == {
        "fx.snowfall_cm.arome_hd", "fx.snowfall_cm.arome_25", "fx.snowfall_cm.arome_hd_dry"}
    assert all(r.run_time_utc == "2026-07-20T09:00:40Z" and r.station == "Z1" for r in rows)
    # run is 09:00Z; buckets before it are analysis, not forecasts → dropped, so
    # the earliest scored bucket is the 12:00Z one (00:00Z and 06:00Z are past).
    valids = sorted(r.valid_time_utc for r in rows)
    assert valids[0] == "2026-07-20T12:00:00Z"
    assert all(v.endswith(":00:00Z") and v[11:13] in ("00", "06", "12", "18") for v in valids)
    assert all(parse_stamp(v) >= parse_stamp("2026-07-20T09:00:40Z") for v in valids)  # forecasts only
    # summer fixture → snowfall is 0 cm, present (never dropped as missing)
    assert all(isinstance(r.value, float) for r in rows)
    # this trimmed fixture carries no surface RH, so the promoted wet-bulb HD
    # column degrades per hour to the dry-bulb taper and matches the retained
    # arome_hd_dry reference here (they diverge only once RH is present).
    hd = {r.valid_time_utc: r.value for r in rows if r.variable == "fx.snowfall_cm.arome_hd"}
    dry = {r.valid_time_utc: r.value for r in rows if r.variable == "fx.snowfall_cm.arome_hd_dry"}
    assert hd and hd == dry


def _calm_truth(archive_conn, station, day_utc):
    """30-min calm/flat obs over one UTC day → truth-A = truth-B = 0, usable."""
    t = parse_stamp(f"{day_utc}T00:00:00Z")
    stop = t + timedelta(days=1)
    rows = []
    while t < stop:
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows += [store.Row("xema", station, ts, ts, s, v) for s, v in (
            ("obs.gruix_neu", 40.0), ("obs.temperatura", 8.0), ("obs.precipitacio", 0.0),
            ("obs.vent_velocitat", 1.0), ("obs.vent_ratxa", 2.0))]
        t += timedelta(minutes=30)
    store.upsert_rows(archive_conn, rows)


def test_ingest_filters_to_high_band_and_scores(tmp_path):
    archive = Archive(root=tmp_path / "ds")
    conn = store.connect(archive.root / "verification.sqlite")
    raw = FIXTURE.read_bytes()
    # a scored high band (Z1) and a mid band that must be ignored
    archive.store("openmeteo", "baqueira_2600.json", raw, fetched_at=RUN)
    archive.store("openmeteo", "baqueira_2000.json", raw, fetched_at=RUN)

    n = lf.ingest_live_forecasts(archive, conn)
    assert n > 0
    stations = {s for (s,) in conn.execute(
        "SELECT DISTINCT station FROM verification_values WHERE variable LIKE 'fx.%'")}
    assert stations == {"Z1"}                       # mid band ignored

    # truth for the days the run covers → the live report scores real pairs
    for day in ("2026-07-20", "2026-07-21", "2026-07-22"):
        _calm_truth(conn, "Z1", day)
    report = lf.build_live_report(conn, "2026-07-19T00:00:00Z", "2026-07-23T00:00:00Z")
    assert report["n_pairs"] > 0
    assert "arome_25" in report["bucket_6h"]["by_column"]


def test_ingest_is_idempotent(tmp_path):
    archive = Archive(root=tmp_path / "ds")
    conn = store.connect(archive.root / "verification.sqlite")
    archive.store("openmeteo", "la-molina_2500.json", FIXTURE.read_bytes(), fetched_at=RUN)
    first = lf.ingest_live_forecasts(archive, conn)
    count = conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]
    lf.ingest_live_forecasts(archive, conn)          # re-run
    assert conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0] == count
    assert first == count
