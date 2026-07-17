"""Meteocat ingest CLI (T3): quota rotation, metadades age gate, skip gate,
archive-before-parse and the last-good failure contract — all offline, over
the fixtures recorded 2026-07-17.

The quota is the design driver (Predicció plan: 100 calls/month): 2 zone
calls + 1 anchor call per day ≈ 92/month, metadades ~2/month on the 30-day
age gate, and the second daily cron slot must self-skip when the first
succeeded (is_fresh) or the budget doubles.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from minipirineu import ingest_meteocat, store
from minipirineu.archive import Archive
from minipirineu.config import METEOCAT_ANCHORS

FIXTURES = Path(__file__).parent / "fixtures" / "meteocat"
# 15:45 local (Europe/Madrid, UTC+2): after the ~14:00 product refresh
NOW = datetime(2026, 7, 17, 13, 45, tzinfo=timezone.utc)

PRIMARIES = tuple(a for a in METEOCAT_ANCHORS if a.primary)
SECONDARIES = tuple(a for a in METEOCAT_ANCHORS if not a.primary)


def load_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeResponse:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")


class FakeSession:
    """Serves canned bodies by URL substring (first match wins); 404 otherwise."""

    def __init__(self, routes: list[tuple[str, bytes]]):
        self.routes = routes
        self.calls: list[str] = []
        self.headers: dict = {}

    def get(self, url: str, timeout: float | None = None) -> FakeResponse:
        self.calls.append(url)
        for substring, body in self.routes:
            if substring in url:
                return FakeResponse(200, body)
        return FakeResponse(404, b"not found")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def full_routes() -> list[tuple[str, bytes]]:
    # metadades before the per-slug catch-alls: substring matching is ordered
    return [
        ("/pirineu/pics/metadades", load_bytes("pics_metadades.json")),
        ("/pirineu/refugis/metadades", load_bytes("refugis_metadades.json")),
        ("/pirineu/2026/07/17", load_bytes("zones_today.json")),
        ("/pirineu/2026/07/18", load_bytes("zones_tomorrow.json")),
        ("/pirineu/pics/", load_bytes("pic_baqueira.json")),
        ("/pirineu/refugis/", load_bytes("pic_baqueira.json")),
    ]


# --- anchor rotation ----------------------------------------------------------

def test_rotation_alternates_primaries_and_secondaries():
    for ordinal in range(760000, 760030):
        anchor = ingest_meteocat.anchor_for_date(date.fromordinal(ordinal))
        assert anchor.primary == (ordinal % 2 == 0)


def test_rotation_periods_are_6_and_14_days():
    # over any 42 days: each primary 42/6 = 7 times, each secondary 42/14 = 3
    start = date(2026, 7, 17)
    picked = [ingest_meteocat.anchor_for_date(start + timedelta(days=i))
              for i in range(42)]
    for anchor in PRIMARIES:
        assert picked.count(anchor) == 7
    for anchor in SECONDARIES:
        assert picked.count(anchor) == 3


def test_rotation_is_keyed_on_the_date_not_call_order():
    day = date(2026, 12, 3)
    assert ingest_meteocat.anchor_for_date(day) == ingest_meteocat.anchor_for_date(day)
    # a dropped day only loses that day's slot; other days are unaffected
    day_after_gap = day + timedelta(days=2)
    with_gap = ingest_meteocat.anchor_for_date(day_after_gap)
    assert with_gap == ingest_meteocat.anchor_for_date(day_after_gap)
    assert with_gap != ingest_meteocat.anchor_for_date(day)


# --- metadades age gate -------------------------------------------------------

def archive_metadades(archive: Archive, fetched_at: datetime) -> None:
    for name in ("pics_metadades.json", "refugis_metadades.json"):
        archive.store("meteocat", name, load_bytes(name), fetched_at=fetched_at)


def test_metadades_reused_from_archive_when_recent(tmp_path):
    archive = Archive(root=tmp_path)
    archive_metadades(archive, NOW - timedelta(days=5))
    session = FakeSession([])
    mapping = ingest_meteocat.load_or_refresh_metadades(session, archive, NOW)
    assert session.calls == []  # 0 quota calls
    assert set(mapping) == {a.codi for a in METEOCAT_ANCHORS}


def test_metadades_refetched_when_older_than_30_days(tmp_path):
    archive = Archive(root=tmp_path)
    archive_metadades(archive, NOW - timedelta(days=31))
    session = FakeSession(full_routes())
    mapping = ingest_meteocat.load_or_refresh_metadades(session, archive, NOW)
    assert len(session.calls) == 2  # pics + refugis
    assert set(mapping) == {a.codi for a in METEOCAT_ANCHORS}
    # the fresh copies were archived (2 old + 2 new)
    assert len(list(archive.iter_source("meteocat"))) == 4


def test_metadades_fetched_when_archive_is_empty(tmp_path):
    archive = Archive(root=tmp_path)
    session = FakeSession(full_routes())
    mapping = ingest_meteocat.load_or_refresh_metadades(session, archive, NOW)
    assert len(session.calls) == 2
    assert mapping["77954ad7"]["slug"] == "cap-de-vaqueira"


# --- skip gate (second cron slot must not double the quota) -------------------

def write_out(path: Path, fetched_at: datetime) -> None:
    path.write_text(json.dumps(
        {"schema": "minipirineu/meteocat/v1",
         "fetched_at": fetched_at.isoformat(timespec="seconds"), "zones": []}))


def test_is_fresh_boundary_at_8h(tmp_path):
    out = tmp_path / "meteocat.json"
    write_out(out, NOW - timedelta(hours=7, minutes=54))
    assert ingest_meteocat.is_fresh(out, NOW)
    write_out(out, NOW - timedelta(hours=8, minutes=6))
    assert not ingest_meteocat.is_fresh(out, NOW)


def test_is_fresh_missing_or_malformed_is_not_fresh(tmp_path):
    assert not ingest_meteocat.is_fresh(tmp_path / "absent.json", NOW)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not ingest_meteocat.is_fresh(bad, NOW)


def test_main_skips_without_any_call_when_fresh(tmp_path, monkeypatch):
    out = tmp_path / "meteocat.json"
    write_out(out, NOW - timedelta(hours=2))
    before = out.read_text()
    session = FakeSession([])
    monkeypatch.setenv("MINIPIRINEU_DATA_DIR", str(tmp_path / "ds"))
    monkeypatch.setattr(ingest_meteocat, "make_session", lambda: session)
    assert ingest_meteocat.main(out, now_utc=NOW) == 0
    assert session.calls == []
    assert out.read_text() == before


# --- snapshot building over real fixtures -------------------------------------

def zone_bodies() -> list[tuple[str, dict]]:
    return [("2026-07-17", json.loads(load_bytes("zones_today.json"))),
            ("2026-07-18", json.loads(load_bytes("zones_tomorrow.json")))]


def test_build_snapshot_shape_from_real_payloads():
    snapshot = ingest_meteocat.build_snapshot(zone_bodies(), "2026-07-17T13:45:00+00:00")
    assert snapshot["schema"] == "minipirineu/meteocat/v1"
    assert [z["zone_id"] for z in snapshot["zones"]] == [1, 5, 6]
    by_id = {z["zone_id"]: z for z in snapshot["zones"]}
    assert by_id[1]["stations"] == ["Baqueira"]
    assert by_id[5]["stations"] == ["Boí Taüll"]
    assert by_id[6]["stations"] == ["La Molina"]
    assert by_id[6]["zone_name"] == "Prepirineu oriental"
    for zone in snapshot["zones"]:
        assert [d["date"] for d in zone["days"]] == ["2026-07-17", "2026-07-18"]
        for day in zone["days"]:
            assert [b["start"] for b in day["blocks"]] == [0, 6, 12, 18]


def test_build_snapshot_codes_are_ints_and_missing_stays_none():
    snapshot = ingest_meteocat.build_snapshot(zone_bodies(), "2026-07-17T13:45:00+00:00")
    day = snapshot["zones"][0]["days"][0]
    block = day["blocks"][2]  # 12–18, has cel + probabilitat on the fixture
    assert isinstance(block["cel"], int)
    assert isinstance(block["probabilitat"], int)
    assert block["cota_m"] is None       # summer: no snow line
    assert day["acumulacio_neu"] is None  # absent, never 0
    assert isinstance(day["acumulacio"], int)


def test_validate_rejects_missing_zone_or_empty_blocks():
    good = ingest_meteocat.build_snapshot(zone_bodies(), "2026-07-17T13:45:00+00:00")
    ingest_meteocat.validate(good)  # must not raise
    with pytest.raises(ValueError):
        ingest_meteocat.validate({**good, "zones": good["zones"][1:]})
    crippled = json.loads(json.dumps(good))
    crippled["zones"][0]["days"][0]["blocks"] = []
    with pytest.raises(ValueError):
        ingest_meteocat.validate(crippled)


# --- full run: archive-first, store rows, failure contract --------------------

def run_main(tmp_path, monkeypatch, session, out=None):
    out = out or tmp_path / "meteocat.json"
    monkeypatch.setenv("MINIPIRINEU_DATA_DIR", str(tmp_path / "ds"))
    monkeypatch.setattr(ingest_meteocat, "make_session", lambda: session)
    return ingest_meteocat.main(out, now_utc=NOW), out


def test_full_run_writes_json_archives_raws_and_store_rows(tmp_path, monkeypatch):
    session = FakeSession(full_routes())
    rc, out = run_main(tmp_path, monkeypatch, session)
    assert rc == 0
    snapshot = json.loads(out.read_text())
    assert len(snapshot["zones"]) == 3
    assert snapshot["fetched_at"].startswith("2026-07-17T13:45")
    # 2 zones + 2 metadades (first run) + 1 anchor = 5 quota calls
    assert len(session.calls) == 5
    archived = [path.name for path, _ in Archive(tmp_path / "ds").iter_source("meteocat")]
    assert any("zones_2026-07-17" in n for n in archived)
    assert any("zones_2026-07-18" in n for n in archived)
    assert any("pics_metadades" in n for n in archived)
    assert any(n.split("_", 1)[1].startswith("pic_") for n in archived)
    conn = store.connect(tmp_path / "ds" / "verification.sqlite")
    n_zonal = conn.execute("SELECT COUNT(*) FROM verification_values "
                           "WHERE variable LIKE 'zonal.%'").fetchone()[0]
    n_pic = conn.execute("SELECT COUNT(*) FROM verification_values "
                         "WHERE variable LIKE 'pic.%'").fetchone()[0]
    assert n_zonal > 0 and n_pic > 0


def test_network_failure_keeps_last_good_and_exits_nonzero(tmp_path, monkeypatch):
    out = tmp_path / "meteocat.json"
    out.write_text('{"old": true}')  # stale (no fetched_at) so no skip gate
    rc, out = run_main(tmp_path, monkeypatch, FakeSession([]), out=out)
    assert rc == 1
    assert json.loads(out.read_text()) == {"old": True}


def test_garbage_payload_is_archived_before_the_parse_fails(tmp_path, monkeypatch):
    routes = [("/pirineu/2026/07/17", b"\xff not json")] + full_routes()
    out = tmp_path / "meteocat.json"
    out.write_text('{"old": true}')
    rc, out = run_main(tmp_path, monkeypatch, FakeSession(routes), out=out)
    assert rc == 1
    assert json.loads(out.read_text()) == {"old": True}  # last-good untouched
    archived = list(Archive(tmp_path / "ds").iter_source("meteocat"))
    assert any(body == b"\xff not json" for _, body in archived)  # raw survived
