"""Near-real-time XEMA API ingest (S0.8/T11).

Fetch-loop and staleness/backfill logic without network: a fake fetch returns a
baked all-stations payload and we assert archive-before-parse, idempotent
upserts, the morning-backfill day set, and the staleness gate boundary.
"""

import json
from datetime import datetime, timezone

from minipirineu import ingest_xema_api as ix
from minipirineu import store
from minipirineu.archive import Archive

NOON = datetime(2026, 2, 2, 14, 0, tzinfo=timezone.utc)   # afternoon → today only

_PAYLOAD = json.dumps([
    {"codi": "Z1", "variables": [
        {"codi": 38, "lectures": [{"data": "2026-02-02T00:00Z", "valor": 150, "estat": "V"}]},
        {"codi": 32, "lectures": [{"data": "2026-02-02T00:00Z", "valor": -4.0, "estat": "V"}]},
    ]},
    {"codi": "ZZ", "variables": [  # not one of ours → filtered out
        {"codi": 38, "lectures": [{"data": "2026-02-02T00:00Z", "valor": 9, "estat": "V"}]}]},
]).encode()


class FakeFetch:
    def __init__(self, payload): self.payload = payload; self.calls = []

    def __call__(self, session, var, day):
        self.calls.append((var, day))
        return self.payload


# --- day window / staleness gate --------------------------------------------

def test_days_to_fetch_morning_includes_yesterday():
    morning = datetime(2026, 2, 2, 6, 0, tzinfo=timezone.utc)
    assert [d.isoformat() for d in ix.days_to_fetch(morning)] == ["2026-02-01", "2026-02-02"]


def test_days_to_fetch_afternoon_is_today_only():
    assert [d.isoformat() for d in ix.days_to_fetch(NOON)] == ["2026-02-02"]


def test_latest_pull_age_none_on_empty_archive(tmp_path):
    archive = Archive(root=tmp_path / "ds")
    assert ix.latest_pull_age_h(archive, NOON) is None      # first run never skips


def test_latest_pull_age_after_a_pull(tmp_path):
    archive = Archive(root=tmp_path / "ds")
    archive.store(ix.ARCHIVE_SOURCE, "20260202_v38.json", b"{}",
                  fetched_at=datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc))
    age = ix.latest_pull_age_h(archive, NOON)               # 14:00 − 10:00 = 4 h
    assert age is not None and abs(age - 4.0) < 1e-6


# --- fetch loop -------------------------------------------------------------

def test_run_ingest_archives_before_parse_and_is_idempotent(tmp_path):
    archive = Archive(root=tmp_path / "ds")
    conn = store.connect(archive.root / "v.sqlite")
    fetch = FakeFetch(_PAYLOAD)

    ix.run_ingest(archive, conn, session=None, now_utc=NOON, fetch=fetch)
    # one archived raw per LIVE_VAR (today only), each written before parsing
    archived = list((archive.root / "raw" / ix.ARCHIVE_SOURCE).rglob("*.gz"))
    assert len(archived) == len(ix.LIVE_VARS)
    assert fetch.calls == [(v, NOON.date()) for v in ix.LIVE_VARS]
    # only our station's two mapped readings land, ZZ filtered out
    count = conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]
    assert count == 2
    stations = {s for (s,) in conn.execute("SELECT DISTINCT station FROM verification_values")}
    assert stations == {"Z1"}

    # re-run the same cycle → same rows upserted, store count unchanged
    ix.run_ingest(archive, conn, session=None, now_utc=NOON, fetch=fetch)
    assert conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0] == 2
