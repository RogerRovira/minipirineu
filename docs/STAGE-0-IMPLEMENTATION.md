# Stage 0 implementation plan — session-sized tasks

**Status: proposed** (2026-07-17, updated same day with user decisions: PiriNeu
ingestion stops now, pics-anchor rotation in scope, Cadí Nord joins the truth set,
wide 6h fetch moves into Stage 0). Companion to `docs/ROADMAP.md` §3. Each task is
sized for one working session, follows RED→GREEN→VALIDATE (tests written and failing
before implementation), and ends in a committable state. Dependencies are explicit;
tasks without a dependency edge can be reordered.

Conventions carried from the existing code: pure parsers over recorded fixtures
(no network in default `pytest`), live-API tests opt-in via `-m live`, missing data
is `None` never 0, failure leaves last-good outputs untouched, UTC in storage /
Europe/Madrid in presentation. New modules stay small (target ≤200 lines, functions
≤20 lines) — split rather than grow.

---

## T0 — Stop PiriNeu, unblock Mini (user actions, no code)

1. **Disable PiriNeu's `ingest` workflow** (PiriNeu → Actions → "ingest" → ⋯ →
   Disable workflow). Decision 2026-07-17: PiriNeu retires as a collector too — Mini
   gets the complete ingestion. Nothing irreplaceable is lost: its XEMA/Meteocat
   legs never ran (missing secret), and summer pronostic payloads carry no snow
   signal. The 100/month Predicció quota becomes Mini's alone.
2. Confirm `METEOCAT_API_KEY` is set in **minipirineu** (probe workflow + T3).
3. Record fixtures for T2 (~5 quota calls total): dispatch `probe-meteocat.yml`
   (2 zone payloads), after extending it — or via a local `-m live` script — to also
   record one pics-anchor payload and the pics/refugis metadades.

**Done when**: PiriNeu shows no new datastore commits, and zone + pic + metadades
fixtures are available for T2.

---

## T1 — ADRs 0002/0003 + datastore branch + archive module (S0.1)

**Goal**: Mini can archive any raw payload before parsing, to a `datastore` branch.

- Draft `docs/adr/0002-datastore-branch-archive-before-parse.md` and
  `docs/adr/0003-verification-first-gate.md` (decision + context + consequences;
  ADR-0004 lands with T6 when the truth design is concrete).
- Port PiriNeu `archive.py` → `src/minipirineu/archive.py` (pathlib, type hints;
  root dir via env var `MINIPIRINEU_DATA_DIR`, like PiriNeu's `PIRINEU_DATA_DIR`).
- Port PiriNeu `db.py` → `src/minipirineu/store.py`: long-format table
  `(source, station, run_time_utc, valid_time_utc, variable, value)` with idempotent
  upsert. This is the **verification store**, not a product store.
- Workflow snippet (reused by T3/T4/T11 workflows): checkout-or-bootstrap
  `datastore` branch, commit with rebase-retry loop (lift from PiriNeu `ingest.yml`).

**Tests (RED first)**:
- roundtrip: `archive_payload` → `iter_archived` yields identical bytes, oldest-first;
- `run_time_from_path` recovers the fetch timestamp;
- store upsert idempotence: same rows twice → same row count;
- store rejects nothing silently (schema constraint test).

**Done when**: pytest green; one manual run archives a dummy payload to a local dir.
**Effort**: 1 session.

## T2 — Meteocat parsers from fixtures (S0.2a)

**Goal**: pure functions turning archived pronostic payloads into (a) verification
rows and (b) the render-ready zone structure.

- Port from PiriNeu `meteocat_ingest.py`: `_franja_window` (inconsistent `nom`
  formats + `idTipusFranja` fallback), `_parse_zones` (string `valor`, absent
  variables, `zona_<id>` keying), `check_zone_names` (drift detection with the
  7-zone `EXPECTED_ZONE_NAMES`), **`_parse_pic`** (per-cota rows: `totes` /
  1500/2000/2500/3000; isozero, iso-10, T/RH/wind per level) and the metadades
  slug/coord extraction with the missing-anchor alert.
- Add `config.py`: `METEOCAT_ZONE_BY_STATION = {"baqueira": 1, "boi-taull": 5,
  "la-molina": 6}` replacing the `None` placeholders, with the LOW-confidence
  la_molina comment (ROADMAP R5), plus the ported `METEOCAT_ANCHORS` tuple
  (codi/resort/station/primary — canonical coords included).
- Extraction for the qualitative column: per zone/day, the fields the render needs
  (cel code, precip probability, `acumulacioNeu` + `cota` when present). Code →
  label mapping kept in one small dict; unknown codes render as the raw code, never
  crash (winter-blocked question 6).

**Tests (RED first)**: parser tests over the T0 probe fixtures + synthetic edge cases
(franja "24h", "00:00h - 06:00h", missing `valor`, unknown zone id triggers drift
message, summer payload without `acumulacioNeu`, pic payload with all five cotes,
metadades missing a configured codi).

**Done when**: pytest green against real fixtures. **Effort**: 1–1.5 sessions.

## T3 — Meteocat ingest CLI + workflow + rendered column (S0.2b)

**Goal**: brief milestone 3 done, archive-first, anchors collecting.

- `src/minipirineu/ingest_meteocat.py`: per run — zones for today + tomorrow
  (2 calls), **one pics anchor via the ported `anchors_for_date` rotation**
  (primaries every 6 days, secondaries every 14; keyed on the date so same-day
  retries hit the same anchor), metadades only when the archived copy is >30 days
  old (~2 calls/month). Archive every payload (T1) **before** parsing. Write
  `data/meteocat.json` in the shape `render.render_meteocat` already consumes;
  same failure contract as the Open-Meteo ingester (any error → keep last-good,
  exit ≠ 0). Anchors and metadades go to archive + store only — nothing renders.
- `.github/workflows/meteocat.yml`: 2×/day cron (post-publication ~13 UTC + one
  retry slot), concurrency group `publish`, datastore commit step, red-run-on-failure.
  Fix the stale comment in `forecast.yml` (it references this file).
- `render.py`: real content for the column (labels from T2), attribution line.

**Tests (RED first)**: golden render with a fixture-built `meteocat.json`; freshness
badge stale/fresh boundary at 26 h (extend `test_freshness.py`); rotation math
(even/odd ordinals, dropped-day behavior); metadades age gate; ingest failure
contract (mock session raising → previous file untouched, non-zero exit).

**Done when**: page shows the three zones' forecast locally from fixtures; a
`workflow_dispatch` run archives zones + anchor + writes the JSON; quota math
double-checked (~3/day ≈ 92/month). **Effort**: 1.5 sessions.

## T4 — Widen the 6h Open-Meteo ingest + raw archiving (S0.9)

**Goal**: archive today what Stage 1–2 will need (ingest wide, publish narrow).

- Extend `HOURLY_VARS`: `relative_humidity_2m`, `wind_speed_10m`, `wind_gusts_10m`
  (both models) and pressure-level temperature/RH/geopotential height at
  1000/925/850/700/600/500 hPa (**AROME 2.5 only** — HD serves no pressure levels,
  validated M1; its series come back null and stay `None`). Same 9 calls/run.
- Archive each raw response to the datastore (T1) before parsing. Rationale: the
  committed `data/openmeteo.json` keeps only rendered variables, and Previous Runs
  coverage of pressure-level variables is unconfirmed — self-archiving is the only
  guaranteed profile history.
- Port PiriNeu `freezing_level.py` → `src/minipirineu/freezing_level.py` (0 °C
  scan + interpolation, capped/inversion semantics, **persist `n_crossings`**);
  computed into the store as a diagnostic — **not rendered**.
- `data/openmeteo.json` and the page do **not** change (verification gate).

**Tests (RED first)**: `build_params` includes the new vars; parse of a recorded
wide-response fixture; rendered page byte-identical with wide input (golden);
freezing-level port keeps PiriNeu's edge cases (all-above, all-below → capped,
inversion → lowest crossing + `n_crossings=2`).

**Done when**: cron archives wide raw responses; page unchanged. **Effort**: 1 session.

## T5 — Socrata XEMA client + station reconciliation + backfill (S0.3)

**Goal**: ≥2 winters of 30-min observations for the truth stations, quota-free.

- `src/minipirineu/xema_opendata.py`: SoQL query builder (dataset `nzvn-apee`,
  filter `codi_estacio` + `codi_variable`, paging; optional app token via env var),
  CSV/JSON → store rows. Variables: 30, 31, 50, 34, 36, 9, 32, 33, 35, 38.
- Reconcile station codes against `yqwd-vj5e` metadata: the PiriNeu six (Z1, YN,
  Z2, CT, ZD, DP) **plus Cadí Nord** (ICGC InfoGruixNEU EMA — La Molina's high
  snow-depth truth; user decision 2026-07-17, resolve its XEMA code here) and any
  further snow-depth EMAs near the resorts. **Archive wide**: backfill every
  Pyrenees EMA reporting var 38 (zero marginal cost); only the resort-local truth
  set is scored. Record the chosen set in `config.py` (`XEMA_STATIONS`, roles
  high/valley) and `docs/notes/xema-truth-stations.md`.
- Semantics cross-check: for one recent day × 2 stations, pull the same readings
  from the XEMA API (~6 calls, `-m live` script, responses kept as fixtures for
  T11) and assert value/timestamp parity — pins the open-data encoding (estat,
  base horària, forward labeling).
- Backfill CLI: chunked per station-month, idempotent, resumable; writes to the
  verification store; archives raw CSV chunks per archive-before-parse.

**Tests (RED first)**: query-builder unit tests; parser over a recorded CSV extract;
idempotence (re-run = 0 new rows); forward-labeling preserved (11:00 value covers
11:00–11:30 — stored as-is, bucketing interprets later).

**Done when**: store holds ≥2 winters for the truth set (Cadí Nord included);
parity script passes. **Effort**: 1.5–2 sessions.

## T6 — Truth-A: snow-depth increments (S0.4a) + ADR-0004

**Goal**: per-6h-bucket fresh-snow cm from var 38, defensible.

- Despiking/smoothing of the 30-min depth series (median/Hampel-style; parameters
  in config, not hardcoded).
- Two-layer Anderson-style settling correction; coefficients from Helfricht et al.
  2018 / SNTHERM-derived equations, cited in ADR-0004 — **not guessed**.
- Sum of positive increments → UTC 6h buckets (aligned with forecast buckets;
  mind forward-labeled readings).

**Tests (RED first)**: synthetic series — single spike removed; steady settling on a
no-snow day yields 0 (not negative snowfall); a synthetic 20 cm storm with settling
recovers within tolerance; bucket-edge reading lands in the right bucket.
**Golden file**: one documented storm at Z1 or Z2, hand-verified against
InfoGruixNEU (numbers from XEMA data, chart for eyeballing only).

**Done when**: unit + golden tests green. **Effort**: 1.5–2 sessions.

## T7 — Truth-B + quality gates + merged truth (S0.4b)

**Goal**: independent gauge-based truth and the A/B merge rules.

- Undercatch transfer function (Kochendorfer et al. 2020 WMO-SPICE, unshielded
  heated gauge) using 10 m wind (var 30); fresh-snow density baseline 68 ± 9 kg/m³,
  T/wind parametrization; SWE → cm.
- Quality gates: A/B wild divergence → bucket excluded; gusts (var 50) > threshold
  (6–8 m/s, config) → excluded (wind redistribution dominates ΔHS); melt signature
  (T > 0 °C, HS falling, gauge accumulating) → flagged `phase_only`, cm is a lower
  bound.
- Output: per (station, 6h bucket): `truth_cm`, `method`, `flags`, or exclusion
  reason. Missing is missing.

**Tests (RED first)**: transfer function against published reference points; density
bounds; each gate triggered by a targeted synthetic bucket; merged output golden
test on the T6 storm.

**Done when**: truth series for 2 winters materializes into the store with exclusion
stats summarized (a sanity report: % excluded per station/winter). **Effort**: 1.5 sessions.

## T8 — `verify.py` metric engine (S0.5)

**Goal**: one scoring path for backtest and live.

- Pair builder: (column, station, lead bucket) forecast vs truth, honoring
  exclusions/flags (phase_only pairs feed event metrics only).
- Metrics: MAE, bias (6h + 24h/48h totals), dead band hits (|err| ≤ max(2 cm, 20 %)),
  POD/FAR/CSI (snow day ≥1 cm/24h; per-bucket events); per resort/station/band/lead.
- Output: JSON (machine) + markdown table (human). No I/O beyond store + files.

**Tests (RED first)**: synthetic pair suites with exact expected scores (perfect,
+2 cm constant bias, all-miss, dead-band edge at exactly 2 cm / exactly 20 %);
phase_only pairs excluded from cm metrics but present in event metrics.

**Done when**: green; a one-command run scores any date range in the store.
**Effort**: 1–1.5 sessions.

## T9 — Previous Runs probe + backtest fetch (S0.6a)

**Goal**: the forecast side of the baseline, archived.

- Probe `previous-runs-api.open-meteo.com` for `meteofrance_arome_france_hd` and
  `meteofrance_arome_france` archive depth (2 calls); record findings in
  `docs/notes/previous-runs-coverage.md` → fixes the backtest date range.
- Fetcher: `_previous_day1`/`_previous_day2` series (true 24/48h leads) for precip,
  T, and native snowfall (2.5) at **XEMA truth-station coords + elevations**;
  chunked date ranges, responses archived raw, then parsed into the store as
  forecast rows (`run_time` = valid_time − lead).
- Derived-HD column recomputed from archived precip+temp via `aggregate.snow_ratio`
  (import, don't duplicate).
- Budget guard: dry-run prints planned call-units; hard cap per day (config) so the
  10k/day non-commercial limit is never approached.

**Tests (RED first)**: response parser over a recorded previous-runs fixture; lead
labeling (day1 vs day2) correct across midnight/DST; derived recompute equals
`aggregate.derive_snowfall` on the same inputs.

**Done when**: both winters' forecast rows are in the store, raws archived.
**Effort**: 1.5–2 sessions (fetch runtime spread over days is fine).

## T10 — Run the backtest → frozen baseline report (S0.6b)

**Goal**: the numbers everything else is gated on.

- Run T8 over T9 forecasts × T7 truth, both AROME columns, per resort/station/lead,
  per winter and pooled.
- Write `docs/verification/baseline-<date>.md` + JSON; include exclusion stats,
  archive-depth caveats, and the derived-vs-native comparison (does the derived
  column justify existing at HD's resolution?).
- Manual validation pass: 2–3 documented storms cross-eyeballed with InfoGruixNEU.
- Declare the baseline **frozen** (commit hash referenced in ADR-0003).

**Done when**: report committed; user has reviewed it. **Effort**: 1 session + review.

## T11 — Live loop: XEMA API ingest + verification page (S0.7 + S0.8)

**Goal**: verification runs itself weekly; near-real-time obs flow.

- Port `xema_ingest.py` → `src/minipirineu/ingest_xema.py` + workflow (staleness-
  gated ~3 cycles/day, morning backfill, archive-before-parse, idempotent
  readings-as-run_time), truth-set stations incl. Cadí Nord. Parity test: one
  overlapping day, API vs Socrata rows. Quota ~570–720/month — Mini's alone now.
- Weekly verification workflow: refresh Socrata truth, build pairs from the live
  forecast archive (git history of `data/openmeteo.json` + datastore raws), run
  `verify.py`, render `site/verificacion.html` (trailing scores beside the frozen
  baseline, per column incl. Meteocat phase/event scores; CC-BY attributions).

**Tests (RED first)**: XEMA API parser over the T5 parity fixtures; staleness gate
boundaries; verification page golden render with synthetic scores; missing week
renders as missing, never 0.

**Done when**: first weekly run publishes the page; Stage 0 acceptance (brief
amendment 2) met end-to-end. **Effort**: 2 sessions.

---

## Sequencing summary

```
T0 (user) ──► T1 ──► T2 ──► T3 (Meteocat pronostic collecting + column live)
               │
               ├──► T4 (wide 6h fetch + raw archive — early, cheap)
               │
               ├──► T5 ──► T6 ──► T7 ──► T8 ──► T10 ──► T11
               │                                 ▲
               └──────────► T9 ──────────────────┘
```

T2/T3, T4, and T5–T9 are independent chains after T1; interleave freely. Total:
~14–17 sessions. Milestone commits: after T3 ("Meteocat column + archive"), after
T10 ("frozen baseline"), after T11 ("verification live").
