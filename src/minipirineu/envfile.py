"""Minimal .env loader (stdlib-only — the stack stays boring).

Local convenience: fills os.environ from a KEY=VALUE file so live scripts
and manual ingests find METEOCAT_API_KEY without per-shell exports (see
.env.example). Shell-exported variables always win, and CI never has a
.env file — workflows inject secrets as real environment variables.
"""

import os
from pathlib import Path


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    """Export KEY=VALUE lines into os.environ; returns what was loaded.

    Missing file → no-op. Comments, blanks, malformed lines and empty
    values (the .env.example placeholders) are skipped silently.
    """
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
