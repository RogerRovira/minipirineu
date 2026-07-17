"""Archive-before-parse contract (ADR-0002): raw bytes in, identical bytes out.

The archive must never inspect payloads — verification sources (Meteocat
pronostic, XEMA) have no upstream archive, so a parse error must never be able
to lose the raw data.
"""

import gzip
from datetime import datetime, timezone
from pathlib import Path

from minipirineu.archive import Archive, run_time_from_path

FETCHED = datetime(2026, 7, 17, 12, 34, 56, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 18, 6, 0, 0, tzinfo=timezone.utc)


def test_roundtrip_bytes_identical_even_for_garbage(tmp_path):
    # non-JSON garbage proves store() never parses or validates payloads
    payload = b"\x00\xff not json at all \xfe"
    archive = Archive(root=tmp_path)
    archive.store("meteocat", "zones_2026-07-17.json", payload, fetched_at=FETCHED)
    items = list(archive.iter_source("meteocat"))
    assert len(items) == 1
    _, body = items[0]
    assert body == payload


def test_path_layout_is_source_date_stamp_name(tmp_path):
    archive = Archive(root=tmp_path)
    path = archive.store("xema", "day_Z1.json", b"{}", fetched_at=FETCHED)
    expected = tmp_path / "raw" / "xema" / "2026" / "07" / "17"
    assert path.parent == expected
    assert path.name == "20260717T123456Z_day_Z1.json.gz"


def test_payload_is_gzipped_on_disk(tmp_path):
    archive = Archive(root=tmp_path)
    path = archive.store("meteocat", "pic.json", b'{"a": 1}', fetched_at=FETCHED)
    assert path.read_bytes()[:2] == b"\x1f\x8b"  # gzip magic
    with gzip.open(path, "rb") as f:
        assert f.read() == b'{"a": 1}'


def test_iter_source_yields_oldest_first(tmp_path):
    archive = Archive(root=tmp_path)
    # stored newest-first to prove ordering comes from the layout, not call order
    archive.store("xema", "b.json", b"newer", fetched_at=LATER)
    archive.store("xema", "a.json", b"older", fetched_at=FETCHED)
    bodies = [body for _, body in archive.iter_source("xema")]
    assert bodies == [b"older", b"newer"]


def test_iter_source_unknown_source_is_empty(tmp_path):
    assert list(Archive(root=tmp_path).iter_source("nope")) == []


def test_run_time_from_path_recovers_fetch_stamp():
    path = Path("raw/xema/2026/07/17/20260717T123456Z_day_Z1.json.gz")
    assert run_time_from_path(path) == "2026-07-17T12:34:56Z"


def test_from_env_honors_data_dir_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIPIRINEU_DATA_DIR", str(tmp_path / "ds"))
    assert Archive.from_env().root == tmp_path / "ds"


def test_from_env_defaults_to_local_datastore(monkeypatch):
    monkeypatch.delenv("MINIPIRINEU_DATA_DIR", raising=False)
    assert Archive.from_env().root == Path("datastore")
