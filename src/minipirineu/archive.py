"""Raw-payload archive: the source of truth for verification data (ADR-0002).

Every verification ingest MUST persist its raw payloads here, gzip-compressed
and dated, BEFORE any parsing happens: Meteocat pronostic and XEMA payloads
have no upstream archive, so a parser bug must never be able to lose data.
The verification store is a rebuildable view of this archive.

Ported from PiriNeu `archive.py` (validated in production there); the layout
`raw/<source>/YYYY/MM/DD/<STAMP>_<name>.gz` is kept byte-compatible so
PiriNeu's payloads could be replayed here if ever needed.
"""

import gzip
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

_STAMP = "%Y%m%dT%H%M%SZ"
_ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"
_ENV_VAR = "MINIPIRINEU_DATA_DIR"
_DEFAULT_ROOT = "datastore"


@dataclass(frozen=True)
class Archive:
    """A datastore root; raw payloads live under <root>/raw/."""

    root: Path

    @classmethod
    def from_env(cls) -> "Archive":
        """Root from $MINIPIRINEU_DATA_DIR (CI: the datastore branch checkout),
        defaulting to a local ./datastore directory (gitignored)."""
        return cls(root=Path(os.environ.get(_ENV_VAR, _DEFAULT_ROOT)))

    def store(
        self, source: str, name: str, payload: bytes, fetched_at: datetime | None = None
    ) -> Path:
        """Persist raw bytes gzip-compressed and dated. Never parses or
        inspects the payload. Returns the path written."""
        fetched_at = fetched_at or datetime.now(timezone.utc)
        day_dir = self.root / "raw" / source / fetched_at.strftime("%Y/%m/%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{fetched_at.strftime(_STAMP)}_{name}.gz"
        with gzip.open(path, "wb") as f:
            f.write(payload)
        return path

    def iter_source(self, source: str) -> Iterator[tuple[Path, bytes]]:
        """Yield (path, raw bytes) for every payload of a source, oldest first."""
        src_dir = self.root / "raw" / source
        if not src_dir.exists():
            return
        for path in sorted(src_dir.rglob("*.gz")):
            with gzip.open(path, "rb") as f:
                yield path, f.read()


def run_time_from_path(path: Path) -> str:
    """Recover the fetch timestamp (ISO UTC) encoded in an archive filename."""
    stamp = path.name.split("_", 1)[0]
    return datetime.strptime(stamp, _STAMP).strftime(_ISO_UTC)
