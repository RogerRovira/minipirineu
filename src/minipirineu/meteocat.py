"""Meteocat pronostic Pirineu: pure parsers over archived payloads.

Everything here is a function over decoded JSON — no network, no quota
(fetching lives in ingest_meteocat.py). Parsers follow the REAL payload
shapes, first mapped in PiriNeu (2026-07-12) and re-confirmed on the fixtures
recorded 2026-07-17 (tests/fixtures/meteocat/):

- zones (`/pronostic/v1/pirineu/{date}`): {dataPrediccio, dataPublicacio,
  franjes:[{idTipusFranja, nom, zones:[{idZona, nom, variablesValors:
  [{nom, valor?, periode}]}]}]}. Franjes carry NO date — the window (24h or a
  6h block) comes from the franja `nom`; `valor` is a STRING (categorical
  codes or numbers); variables without `valor` are absent that day (e.g.
  acumulacioNeu in summer) and stay missing, never 0.
- pics (`/pronostic/v1/pirineu/{pics|refugis}/{slug}/{date}`): [{data,
  cotes:[{cota, variables:[{nom, valor}]}]}], 8 three-hourly timesteps,
  cota in {"totes", "1500", "2000", "2500", "3000"}.
"""

import json
import re
from typing import Iterable

from minipirineu.config import METEOCAT_ANCHOR_COORDS, METEOCAT_ANCHORS
from minipirineu.store import Row

SOURCE = "meteocat"

# Zone names as served by the API (truncated at ~25 chars BY the API). The
# endpoint's own 7-zone scheme — a rename/renumber would silently corrupt the
# resort->zone assignment, so ingest alerts on any drift (check_zone_names).
EXPECTED_ZONE_NAMES = {
    1: "Vessant nord Pirineu occi",
    3: "Vessant nord Pirineu orie",
    4: "Pirineu oriental",
    5: "Vessant sud Pirineu occid",
    6: "Vessant sud Prepirineu or",
    7: "Prepirineu occidental",
    8: "Vessant sud Pirineu orien",
}

# Fallback when a franja `nom` is unparseable: idTipusFranja -> (start, span).
_FRANJA_BY_ID = {1: (0, 6), 2: (6, 6), 3: (12, 6), 4: (18, 6), 5: (0, 24)}
_FRANJA_RE = re.compile(r"(\d{1,2}):(\d{2})")


def franja_window(franja: dict) -> tuple[int, int] | None:
    """(start_hour, span_hours) of a franja.

    Real `nom` values are inconsistently formatted ("24h", "00:00h - 06:00h",
    "06:00 - 12:00h") so parse the clock times, falling back to the
    idTipusFranja mapping observed alongside them.
    """
    nom = str(franja.get("nom") or "")
    hours = [int(h) for h, _ in _FRANJA_RE.findall(nom)]
    if len(hours) == 2 and hours[1] > hours[0]:
        return hours[0], hours[1] - hours[0]
    if nom.strip() == "24h":
        return 0, 24
    return _FRANJA_BY_ID.get(franja.get("idTipusFranja"))


def _as_float(valor) -> float | None:
    """Meteocat sends `valor` as str, int or float; text (comentari) -> None."""
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def zone_station(id_zona: int) -> str:
    """Pseudo-station name under which a zone's verification rows are stored."""
    return f"zona_{id_zona}"


def parse_zones(body, run_time_utc: str, target_date: str) -> list[Row]:
    """One row per (zone, franja, variable-with-a-valor), for ALL zones.

    All zones are stored (`zona_<id>` pseudo-stations): which zone represents
    which resort is a revisable config decision (METEOCAT_ZONE_BY_STATION) —
    nothing is lost if the assignment turns out wrong. The window length goes
    into the variable name (zonal.cota.6h vs zonal.acumulacioNeu.24h) so the
    24h summary never mixes with the 6h blocks.
    """
    rows: list[Row] = []
    if not isinstance(body, dict):
        return rows
    for franja in body.get("franjes") or []:
        window = franja_window(franja)
        if window is None:
            continue
        start, span = window
        valid = f"{target_date}T{start:02d}:00Z"
        for zone in franja.get("zones") or []:
            id_zona = zone.get("idZona")
            if not isinstance(id_zona, int):
                continue
            for var in zone.get("variablesValors") or []:
                value = _as_float(var.get("valor"))
                if var.get("nom") and value is not None:
                    rows.append(Row(SOURCE, zone_station(id_zona), run_time_utc,
                                    valid, f"zonal.{var['nom']}.{span}h", value))
    return rows


def check_zone_names(body) -> list[str]:
    """Human-readable drift messages vs EXPECTED_ZONE_NAMES (empty = OK)."""
    problems = []
    seen: dict[int, str] = {}
    for franja in (body.get("franjes") or []) if isinstance(body, dict) else []:
        for zone in franja.get("zones") or []:
            if isinstance(zone.get("idZona"), int):
                seen[zone["idZona"]] = zone.get("nom")
    for id_zona, nom in sorted(seen.items()):
        expected = EXPECTED_ZONE_NAMES.get(id_zona)
        if expected is None:
            problems.append(f"unknown zone id {id_zona} ({nom!r})")
        elif nom != expected:
            problems.append(f"zone {id_zona} renamed: {nom!r} (expected {expected!r})")
    return problems


def parse_pic(body, run_time_utc: str, station: str) -> list[Row]:
    """One row per (timestep, cota, variable): pic.<nom>.<cota>.

    Spaces in noms become underscores ("direccio vent" -> direccio_vent) so
    variable names stay shell/SQL-friendly. A timestep without `data` is
    skipped — missing stays missing, timestamps are never fabricated.
    """
    rows: list[Row] = []
    if not isinstance(body, list):
        return rows
    for point in body:
        valid = point.get("data")
        if not valid:
            continue
        for cota in point.get("cotes") or []:
            level = cota.get("cota") or "peak"
            for var in cota.get("variables") or []:
                value = _as_float(var.get("valor"))
                if var.get("nom") and value is not None:
                    nom = str(var["nom"]).replace(" ", "_")
                    rows.append(Row(SOURCE, station, run_time_utc, str(valid),
                                    f"pic.{nom}.{level}", value))
    return rows


def parse_metadades(body, kind: str) -> dict[str, dict]:
    """codi -> {slug, kind, name, lat, lon} for the configured anchors only.

    `kind` is "pics" or "refugis" (each has its own metadades endpoint); it is
    needed later to build the anchor's forecast URL.
    """
    wanted = {a.codi for a in METEOCAT_ANCHORS}
    mapping: dict[str, dict] = {}
    for item in body if isinstance(body, list) else []:
        codi = item.get("codi")
        if codi in wanted:
            coords = item.get("coordenades") or {}
            mapping[codi] = {"slug": item.get("slug"), "kind": kind,
                             "name": item.get("descripcio"),
                             "lat": coords.get("latitud"),
                             "lon": coords.get("longitud")}
    return mapping


def missing_anchors(mapping: dict[str, dict]) -> set[str]:
    """Configured anchor codis absent from the resolved metadades — a codi
    typo in config, or Meteocat dropped the point; ingest alerts on these."""
    return {a.codi for a in METEOCAT_ANCHORS} - set(mapping)


def coord_drift(mapping: dict[str, dict]) -> list[str]:
    """Drift messages for primary anchors whose metadades coords moved away
    from the canonical values in config (tolerance ~1e-6 deg, a few cm)."""
    problems = []
    for codi, (lat, lon) in METEOCAT_ANCHOR_COORDS.items():
        info = mapping.get(codi)
        if info is None:
            continue  # missing_anchors covers absence
        if abs(info["lat"] - lat) > 1e-6 or abs(info["lon"] - lon) > 1e-6:
            problems.append(f"anchor {codi} coords drifted: "
                            f"{info['lat']},{info['lon']} vs canonical {lat},{lon}")
    return problems


def _zone_fields(franja: dict, id_zona: int) -> dict[str, float | None]:
    """One zone's variables within one franja, as raw codes/values.

    Raw numeric codes only — label mapping needs the official simbols catalog
    (open question 6), unknown codes must render as the code, never crash.
    None values are KEPT (absent variable = missing, e.g. acumulacioNeu in
    summer; comentari is text and is deferred until a winter payload shows
    its real shape)."""
    for zone in franja.get("zones") or []:
        if zone.get("idZona") == id_zona:
            return {var["nom"]: _as_float(var.get("valor"))
                    for var in zone.get("variablesValors") or []
                    if var.get("nom")}
    return {}


def zone_day_fields(body, id_zona: int) -> dict[str, float | None]:
    """The 24h-franja variables of one zone (accumulations + comentari) —
    the payload's own daily summary. Sky state lives in the 6h blocks
    (zone_block_fields), validated on real payloads 2026-07-17."""
    if not isinstance(body, dict):
        return {}
    for franja in body.get("franjes") or []:
        if franja_window(franja) == (0, 24):
            return _zone_fields(franja, id_zona)
    return {}


def zone_block_fields(body, id_zona: int) -> dict[int, dict[str, float | None]]:
    """start_hour -> one zone's variables, for the 6h franjes (cel,
    probabilitat, tempesta, visibilitat, intensitat, cota…)."""
    blocks: dict[int, dict[str, float | None]] = {}
    if not isinstance(body, dict):
        return blocks
    for franja in body.get("franjes") or []:
        window = franja_window(franja)
        if window is None or window[1] != 6:
            continue
        fields = _zone_fields(franja, id_zona)
        if fields:
            blocks[window[0]] = fields
    return blocks
