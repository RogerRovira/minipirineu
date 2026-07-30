import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from minipirineu import ingest_xema, store
from minipirineu.archive import Archive
from minipirineu.config import XEMA_SNOW_DEPTH_VAR, XEMA_STATIONS, XEMA_VARIABLES

FIXTURE = Path(__file__).parent / "fixtures" / "xema_opendata_z1_z9_20260201.json"
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _archive(tmp_path) -> Archive:
    return Archive(root=tmp_path / "datastore")


class FakePager:
    """Serves pre-baked pages by $offset and records every query it saw."""

    def __init__(self, pages):
        self.pages = pages  # list[list[dict]]
        self.calls = []

    def __call__(self, session, params, timeout=60):
        self.calls.append(params)
        offset = params["$offset"]
        limit = params["$limit"]
        idx = offset // limit
        page = self.pages[idx] if idx < len(self.pages) else []
        return json.dumps(page).encode()


# --- month chunking ---------------------------------------------------------

def test_month_chunks_are_contiguous_half_open():
    chunks = ingest_xema.month_chunks("2024-01", "2024-03")
    assert [c[2] for c in chunks] == ["2024-01", "2024-02", "2024-03"]
    assert chunks[0] == ("2024-01-01T00:00:00", "2024-02-01T00:00:00", "2024-01")
    # each chunk's end is the next chunk's start — no gap, no overlap
    assert chunks[0][1] == chunks[1][0]
    assert chunks[1][1] == chunks[2][0]


def test_month_chunks_crosses_year_boundary():
    chunks = ingest_xema.month_chunks("2023-12", "2024-01")
    assert [c[2] for c in chunks] == ["2023-12", "2024-01"]
    assert chunks[0][1] == "2024-01-01T00:00:00"


def test_month_chunks_single_month():
    assert len(ingest_xema.month_chunks("2024-02", "2024-02")) == 1


def test_month_chunks_rejects_reversed_range():
    with pytest.raises(ValueError, match="precedes"):
        ingest_xema.month_chunks("2024-03", "2024-01")


# --- chunk backfill ---------------------------------------------------------

def test_backfill_chunk_single_page_and_idempotent(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    rows = json.loads(FIXTURE.read_text())
    pager = FakePager([rows])

    def run():
        return ingest_xema.backfill_chunk(
            archive, conn, ["Z1", "Z9"], list(XEMA_VARIABLES),
            "2026-02-01T00:00:00", "2026-03-01T00:00:00", "2026-02",
            session=None, now_utc=NOW, fetch_page=pager,
        )

    first = run()
    assert first == len(rows)
    count = conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0]
    assert count == len(rows)
    run()  # re-pull the same window
    assert conn.execute("SELECT COUNT(*) FROM verification_values").fetchone()[0] == count


def test_backfill_chunk_pages_until_short(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    # two full pages then a short one → three fetches, offsets 0/2/4
    def obs(station, ts):
        return {"codi_estacio": station, "codi_variable": "38",
                "data_lectura": ts, "valor_lectura": "10"}
    pages = [
        [obs("Z1", "2026-02-01T00:00:00.000"), obs("Z2", "2026-02-01T00:00:00.000")],
        [obs("Z1", "2026-02-01T00:30:00.000"), obs("Z2", "2026-02-01T00:30:00.000")],
        [obs("Z1", "2026-02-01T01:00:00.000")],
    ]
    pager = FakePager(pages)
    total = ingest_xema.backfill_chunk(
        archive, conn, ["Z1", "Z2"], ["38"],
        "2026-02-01T00:00:00", "2026-03-01T00:00:00", "2026-02",
        session=None, now_utc=NOW, fetch_page=pager, page_limit=2,
    )
    assert total == 5
    assert [p["$offset"] for p in pager.calls] == [0, 2, 4]
    # one archived page file per fetch
    archived = list((archive.root / "raw" / "xema").rglob("*.gz"))
    assert len(archived) == 3


def test_backfill_chunk_paging_uses_raw_count_not_parsed_count(tmp_path):
    # regression: a full page whose rows are partly skipped by parse_rows must
    # NOT be read as the last page — that would silently truncate the window.
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")

    def obs(var, ts):
        return {"codi_estacio": "Z1", "codi_variable": var,
                "data_lectura": ts, "valor_lectura": "100"}

    # page 0: 2 raw records (== page_limit) but one is an unmapped variable
    # (999) that parse_rows drops → 1 parsed row. Data continues on page 1.
    pages = [
        [obs("999", "2026-02-01T00:00:00.000"), obs("38", "2026-02-01T00:00:00.000")],
        [obs("38", "2026-02-01T00:30:00.000"), obs("38", "2026-02-01T01:00:00.000")],
        [obs("38", "2026-02-01T01:30:00.000")],
    ]
    pager = FakePager(pages)
    ingest_xema.backfill_chunk(
        archive, conn, ["Z1"], ["38"],
        "2026-02-01T00:00:00", "2026-03-01T00:00:00", "2026-02",
        session=None, now_utc=NOW, fetch_page=pager, page_limit=2,
    )
    stored = conn.execute(
        "SELECT COUNT(*) FROM verification_values WHERE variable='obs.gruix_neu'"
    ).fetchone()[0]
    # all four var-38 readings across the three pages, none lost to truncation
    assert stored == 4
    assert [p["$offset"] for p in pager.calls] == [0, 2, 4]


def test_backfill_groups_do_not_overwrite_each_others_archive(tmp_path):
    # regression: scored and wide run the same month with the same fetch
    # timestamp; their raw pages must land in distinct files
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")

    def pager(session, params, timeout=60):
        codi = "Z1" if "'Z1'" in params["$where"] else "Z3"
        return json.dumps([{"codi_estacio": codi, "codi_variable": "38",
                            "data_lectura": "2024-02-01T00:00:00.000",
                            "valor_lectura": "50"}]).encode()

    chunks = ingest_xema.month_chunks("2024-02", "2024-02")
    ingest_xema.backfill(archive, conn, chunks, session=None, now_utc=NOW, fetch_page=pager)
    files = list((archive.root / "raw" / "xema").rglob("*.gz"))
    assert len(files) == 2, [f.name for f in files]
    names = sorted(f.name for f in files)
    assert "scored" in names[0] and "wide" in names[1]


def test_backfill_archives_before_parsing(tmp_path):
    # a page that isn't valid JSON must still be on disk after the failure:
    # archive-before-parse means the bytes are saved before decoding (ADR-0002)
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")

    def broken(session, params, timeout=60):
        return b"{ this is not json"

    with pytest.raises(json.JSONDecodeError):
        ingest_xema.backfill_chunk(
            archive, conn, ["Z1"], ["38"],
            "2026-02-01T00:00:00", "2026-03-01T00:00:00", "2026-02",
            session=None, now_utc=NOW, fetch_page=broken,
        )
    archived = list((archive.root / "raw" / "xema").rglob("*.gz"))
    assert len(archived) == 1  # the raw bytes survived the parser blowing up


# --- group orchestration ----------------------------------------------------

def test_backfill_pulls_all_vars_for_scored_and_only_snow_for_wide(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    pager = FakePager([])  # every chunk returns an empty page → one call each
    chunks = ingest_xema.month_chunks("2024-02", "2024-02")
    ingest_xema.backfill(archive, conn, chunks, session=None, now_utc=NOW, fetch_page=pager)

    scored_codes = {s.codi for s in XEMA_STATIONS if s.resort}
    wide_codes = {s.codi for s in XEMA_STATIONS if not s.resort}
    scored_call = next(p for p in pager.calls if f"'{next(iter(scored_codes))}'" in p["$where"]
                       and "codi_variable in ('30'" in p["$where"])
    wide_call = next(p for p in pager.calls
                     if all(f"'{c}'" in p["$where"] for c in wide_codes))
    # scored group asks for the full variable set; wide group only snow depth
    for code in XEMA_VARIABLES:
        assert f"'{code}'" in scored_call["$where"]
    assert f"codi_variable in ('{XEMA_SNOW_DEPTH_VAR}')" in wide_call["$where"]


def test_scored_only_skips_the_wide_group(tmp_path):
    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    pager = FakePager([])
    chunks = ingest_xema.month_chunks("2024-02", "2024-02")
    ingest_xema.backfill(archive, conn, chunks, session=None, now_utc=NOW,
                         scored_only=True, fetch_page=pager)
    wide_codes = {s.codi for s in XEMA_STATIONS if not s.resort}
    assert not any(all(f"'{c}'" in p["$where"] for c in wide_codes) for p in pager.calls)
    assert len(pager.calls) == 1  # scored group only


def test_store_is_rebuildable_from_the_archive(tmp_path):
    # ADR-0002 core invariant: the archive is the source of truth and the store
    # is a rebuildable view of it. Rebuilding from the archived gzip bytes alone
    # must reproduce the exact same rows.
    from minipirineu import xema_opendata

    archive = _archive(tmp_path)
    conn = store.connect(archive.root / "v.sqlite")
    pager = FakePager([json.loads(FIXTURE.read_text())])
    ingest_xema.backfill_chunk(
        archive, conn, ["Z1", "Z9"], list(XEMA_VARIABLES),
        "2026-02-01T00:00:00", "2026-03-01T00:00:00", "2026-02",
        session=None, now_utc=NOW, fetch_page=pager,
    )
    original = set(conn.execute(
        "SELECT source,station,run_time_utc,valid_time_utc,variable,value FROM verification_values"
    ).fetchall())

    rebuilt_conn = store.connect(tmp_path / "rebuilt.sqlite")
    for _path, raw in archive.iter_source("xema"):
        store.upsert_rows(rebuilt_conn, xema_opendata.parse_payload(raw))
    rebuilt = set(rebuilt_conn.execute(
        "SELECT source,station,run_time_utc,valid_time_utc,variable,value FROM verification_values"
    ).fetchall())

    assert rebuilt == original and len(rebuilt) > 0


def test_main_bad_args_returns_usage_code():
    assert ingest_xema.main([]) == 2
    assert ingest_xema.main(["2024-01"]) == 2


def test_main_malformed_month_is_reported_not_a_traceback(capsys):
    # a bad month must exit 2 with a clear message, never crash the process
    assert ingest_xema.main(["2024", "2024-05"]) == 2
    assert ingest_xema.main(["2024-13", "2024-14"]) == 2
    assert "bad month range" in capsys.readouterr().err


def test_main_reversed_range_is_reported(capsys):
    assert ingest_xema.main(["2024-05", "2024-01"]) == 2
    assert "bad month range" in capsys.readouterr().err
