# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**MiniPiriNeu** — a static website showing 48h snow forecasts by elevation band (low/mid/high) for three Catalan Pyrenees ski resorts (Baqueira, Boí Taüll, La Molina), using high-resolution mesoscale models (AROME) that commercial aggregators don't serve.

`MiniPrevi_PiriNeu.md` is the authoritative project brief (in Spanish, status: confirmed). Read it before making design decisions — it records the stack decision, rejected alternatives, non-goals, and acceptance criteria for each v1 feature. Do not re-litigate decisions recorded there.

## Stack (decided in the brief)

- Python 3.12, `requests` + JSON only — no raster/GRIB tooling. Dev-only: pytest.
- Model data from **Open-Meteo Forecast API** with explicit `models=meteofrance_arome_france_hd,meteofrance_arome_france` (never `best_match`) and the `elevation` parameter per elevation band. Variables: `snowfall`, `precipitation`, `temperature_2m`.
- **Validated API reality (milestone 1, supersedes the brief's variable assumptions — see `docs/notes/snowfall-semantics.md`)**: AROME HD serves no snowfall variable, so its snow is derived from precipitation × temperature-dependent ratio (`aggregate.snow_ratio`, fitted against AROME 2.5's native snowfall); no Météo-France model serves `freezing_level_height`, so per-band `temperature_2m` plays the snow-line role. Both decisions were confirmed by the user on 2026-07-14.
- **Meteocat API** for the qualitative mountain forecast per zone.
- GitHub Actions cron every ~6h regenerates JSON + static HTML; deployed to GitHub Pages. No backend, no auth, no database.

## Layout

`src/minipirineu/` (config, openmeteo, aggregate, ingest_openmeteo, meteocat, ingest_meteocat, render) · `data/` generated JSON committed by CI · `site/` generated HTML (not committed) · `templates/` + `assets/` for the page · `tests/` with recorded API fixtures in `tests/fixtures/` · `docs/adr/` + `docs/notes/` for decisions and M1 findings. Local setup: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`; run `pytest` (live-API tests are opt-in via `-m live`).

## Architecture

Pipeline: ingest APIs → write JSON → render static HTML table (stations × models × elevation bands, 6h intervals up to 48h) → publish.

Key constraints baked into the design:

- **Independent ingestions**: a failure in one source must not take down the others; each source shows a visible freshness timestamp. Stale data must look stale, never pass as fresh.
- **Meteocat quota is tight** (100 forecast calls/month): its ingestion is decoupled from the main cron and limited to 2 calls/day. Don't add Meteocat calls to the 6h cron.
- Reference elevations per station are confirmed in the brief (e.g. Baqueira 1500/2000/2600 m).

## Non-goals (v1)

No model blending/consensus, no raw grid ingestion (GRIB2/GeoTIFF — documented as fallback only), no user accounts, no native app, no forecast archive, no dynamic server. Avalanche bulletins (BPA/ICGC, AEMET) are explicitly deferred.

## Milestones

1. **Datos en mano** — script calling Open-Meteo for all stations × 3 bands × 2 AROME models, writing JSON with 48h snow cm and freezing level. Validate `snowfall` semantics and elevation downscaling here.
2. **Publicado y automático** — static HTML from that JSON, on GitHub Pages, cron every 6h with freshness timestamps.
3. **v1 completa** — Meteocat muntanya column (station→zone mapping to be confirmed).

## Working conventions

- User knows Python, Git, REST APIs; is new to weather APIs and forecast variable semantics — explain those when relevant.
- Serious testing appetite; commits per milestone.
- Open questions to resolve during milestones are listed at the end of the brief (Meteocat zone codes, snowfall units/ratio per model, KNMI/DMI HARMONIE coverage over the Pyrenees).
