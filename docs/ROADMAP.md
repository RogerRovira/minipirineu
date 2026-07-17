# ROADMAP — making MiniPiriNeu measurable, then better

**Status: proposed** (2026-07-17, pending user review). Implements the plan settled in
`handoff.md` (2026-07-16), reconciled against this repo and the PiriNeu donor repo
(`github.com/RogerRovira/PiriNeu`, `datastore` branch inspected).

Governing principle (settled, not up for review): **archive wide, publish narrow**.
Any source may be fetched and archived cheaply (archive-before-parse), but nothing
enters the rendered forecast without passing the verification gate. No item that
changes published output ships before the harness exists (Stage 0) and the
pre-winter baseline is frozen.

Effort scale: **S** ≤ 1 session · **M** 1–2 sessions · **L** 2+ sessions.

---

## 0. Reconciliation: handoff vs repo reality (2026-07-17)

| # | Handoff said | What the repos actually show | Consequence |
|---|---|---|---|
| R1 | Meteocat muntanya column is serving | **Not implemented.** No `meteocat.py` / `ingest_meteocat.py`, no `meteocat.yml` workflow (`forecast.yml` references it), no `data/meteocat.json`. Only the fixture-probe workflow and a renderer placeholder exist. Brief milestone 3 has not started. | The Meteocat column folds into Stage 0 (S0.2): it must exist to be archived and scored. |
| R2 | PiriNeu's XEMA cron runs, "raw payloads archived since 2026-07-14" | **The XEMA and Meteocat legs have failed on every Actions run** since wiring (2026-07-13/14): `METEOCAT_API_KEY` was never set as a PiriNeu repo secret (45 identical alerts in `datastore:logs/alerts.log`). The datastore holds 45 Open-Meteo payloads + 16 AEMET tars — **zero XEMA, zero Meteocat payloads**. | XEMA: nothing permanently lost — full semi-hourly history is on Socrata open data. Meteocat pronostic: **every unarchived day is lost forever** (rolling 3-day window, validated by PiriNeu 2026-07-12). See "Immediate actions" below. |
| R3 | Re-parse PiriNeu's archived XEMA payloads for variables 30/31/50/34/36/9 and backfill | No archived XEMA payloads exist to re-parse. | The extended variable list moves to the Socrata puller (S0.3) — same variables, zero quota cost, deeper history. |
| R4 | Place `data-report-MiniPiriNeu.md` / `data-report-PiriNeu.md` in `docs/` | Not in either repo, not on this machine. | Working from repo code directly (which the handoff ranks above the reports anyway). If copies exist elsewhere, drop them into `docs/`. |
| R5 | Mini's `config.py` expects Meteocat zones like "Aran - Franja Nord" (brief open question) | PiriNeu validated (2026-07-12): the pronostic API uses its **own 7-zone scheme**, serves no geometry, and truncates zone names at ~25 chars. Assignment: baqueira→1, boi_taull→5 (high confidence), la_molina→6 (**LOW** — zones 4/8 plausible). | Adopt PiriNeu's mapping + zone-drift alerts; confirm la_molina on winter data (port of `verify_meteocat_zones.py` logic). Resolves the brief's zone-code open question. |
| R6 | Truth stations: snow depth at Z1, Z2, YN, DP; ZD has none | Confirmed in PiriNeu `config.py`/`xema_ingest.py`. Note: La Molina's only snow-depth truth is **Das (DP), a 1096 m valley station in the Cerdanya cold pool** — weak truth for 1700–2500 m bands. | S0.3 queries the open-data station metadata for additional snow-depth EMAs near each resort (zero API cost) before freezing the truth set. **Decision 2026-07-17: Cadí Nord (ICGC InfoGruixNEU EMA) joins as La Molina's high snow-depth station — code to confirm against `yqwd-vj5e`.** |

### Immediate actions (user, ~5 minutes, before any code)

Decision 2026-07-17 (user): PiriNeu stops collecting **now** — Mini gets the complete,
solid ingestion. It's summer: the pronostic gap until S0.2 lands carries no snow
signal, and nothing else PiriNeu collects is irreplaceable (R2).

1. **Disable PiriNeu's `ingest` workflow** (PiriNeu → Actions → "ingest" → Disable
   workflow). The repo stays as parts donor and reference only.
2. **Confirm `METEOCAT_API_KEY` is set in `minipirineu`** (the probe workflow needs
   it; S0.2 needs probe fixtures).
3. With PiriNeu off, the Predicció plan's 100 calls/month belong to Mini alone —
   funding zones (2/day) **and** the pics-anchor isozero rotation (~1/day) ≈ 92/month.

---

## 1. The verification gate (metric spec shared by backtest and live)

`verify.py` computes, with the **same code** on backtest and live pairs:

- **Accumulation**: MAE and bias of snowfall cm per (resort, station/band, lead bucket),
  on 6h buckets and 24h/48h totals. **Dead band**: |error| ≤ max(2 cm, 20 % of obs)
  counts as a hit — instrument noise is not chased (±1–2 cm sensor floor).
- **Events**: POD / FAR / CSI for "snow day" (≥1 cm/24h) and per-6h-bucket event calls.
- **Snow line** (from Stage 1): transition-altitude error in metres.
- **Meteocat column**: scored too — phase and event/no-event calls (no public
  verification of it exists anywhere).
- Columns scored side by side, same period, same truth: AROME HD derived,
  **AROME 2.5 native (primary internal baseline — the derived column must beat it to
  justify existing)**, Meteocat muntanya; later HARMONIE / ECMWF IFS. Literature
  numbers are context only, never table rows.

**Frozen baseline** = the S0.5 backtest report (~2 winters, per resort/station/lead).
Every Stage 1–3 item ships only if it beats the relevant frozen-baseline number on its
go/no-go check. Backtest-derived corrections are **priors to confirm live** (model
versions drift across the archive period), not final constants.

**Truth pipeline** (settled in handoff): truth-A = despiked positive 30-min snow-depth
increments (var 38) with two-layer Anderson-style settling correction; truth-B =
heated-gauge precip (var 35) with WMO-SPICE wind undercatch transfer (10 m wind,
var 30) ÷ parametrized fresh-snow density (68 ± 9 kg/m³ baseline, T/wind dependent).
Quality gates: drop buckets where A and B diverge wildly or gusts exceed ~6–8 m/s;
melt-contaminated buckets (T > 0 °C, HS falling, gauge accumulating) are
phase-scorable, cm-lower-bound only. ZD (la Tosa) is out of the truth set.
Verification pairs are built **at XEMA station coordinates/elevations** (forecast
re-fetched at those points), not at resort band points — apples to apples.

---

## 2. Quota budget

| Source | Cap | Today | After Stage 0 |
|---|---|---|---|
| Open-Meteo forecast | ~10 000 calls/day (ensemble ×4) | 36/day (9 × 4 runs) | +6 station points/run → ~60/day; backtest is a bounded one-off (≤ a few k call-units/day, spread over days) |
| Meteocat Predicció | **100/month** | 0 (PiriNeu off) | Mini zones 2/day + pics-anchor rotation ~1/day + metadades ~2/month ≈ 92/month |
| Meteocat XEMA API | **750/month** | 0 (PiriNeu off) | Mini live ingest ~570–720/month (S0.8); Socrata handles all history |
| Socrata open data (`analisi.transparenciacatalunya.cat`) | none (app token recommended) | — | truth backfill + periodic refresh |

---

## 3. Stage 0 — instrument (nothing publishes until this exists)

Recommended order: S0.1 → S0.2 (restarts the pronostic archive — the only clock
ticking), with S0.9 early (cheap, starts the profile history), then S0.3 → S0.6
(the harness), then S0.7–S0.8 (close the live loop).

### S0.1 Datastore branch + archive-before-parse module
- **Mechanism**: adopt PiriNeu's pattern — a `datastore` branch holding
  `raw/<source>/YYYY/MM/DD/<stamp>_<name>.gz` plus the verification SQLite; port
  `archive.py` nearly as-is; workflows bootstrap/commit the branch (serialize via
  concurrency group). Main branch `data/` keeps only the published forecast JSONs.
- **Files**: `src/minipirineu/archive.py`, `src/minipirineu/store.py` (long-format
  SQLite, ported from PiriNeu `db.py`), workflow steps, `docs/adr/0002-*.md`.
- **Quota**: none · **Effort**: S–M · **Deps**: none
- **Acceptance**: a test proves payload → archive → `iter_archived` roundtrip and that
  parsers consume archive bytes only; the branch exists with one real payload in it.

### S0.2 Meteocat pronostic: archive + muntanya column (completes brief milestone 3)
- **Mechanism**: port PiriNeu's parsers (franja windows, string `valor`, zone-drift
  check, per-cota pics) and quota discipline. Zones endpoint
  (`/pronostic/v1/pirineu/{date}`) for today + tomorrow = 2 calls/day, decoupled
  workflow (never in the 6h cron). **Pics-anchor rotation included** (user decision
  2026-07-17 — isozero + 3000 m wind reference data, "just in case"): one anchor/day
  on PiriNeu's primary/secondary rotation, metadades refetched ~monthly. Anchors are
  archive-only, nothing renders from them. Every payload archived before parsing.
  Render the mapped zone per resort (R5 mapping); freshness badge already exists
  (`STALE_AFTER_H["meteocat"] = 26`).
- **Files**: `src/minipirineu/meteocat.py`, `ingest_meteocat.py`,
  `.github/workflows/meteocat.yml`, `config.py` (zone map + anchors), `render.py`
  (column content), tests + probe fixtures.
- **Quota**: ~3/day ≈ 92/month (+~5 one-off for fixtures) · **Effort**: M · **Deps**: S0.1
- **Acceptance**: `data/meteocat.json` regenerates 2×/day; the page shows each
  resort's zone forecast with a freshness badge; every payload (zones, pics,
  metadades) lands in the archive before parsing.

### S0.3 XEMA truth ingestion from Socrata open data
- **Mechanism**: pull dataset `nzvn-apee` (semi-hourly XEMA readings; Socrata
  SoQL/CSV export, station code + variable code filters), variables 30/31/50/34/36/9
  + 32/33/35/38, stations Z1, YN, Z2, CT, ZD, DP **+ Cadí Nord** (ICGC InfoGruixNEU
  EMA — La Molina's high snow-depth truth; user decision 2026-07-17, code from
  `yqwd-vj5e`) — after reconciling codes against station metadata and scanning for
  any further snow-depth EMAs near the resorts (R6). **Archive wide**: backfill every
  Pyrenees EMA reporting snow depth (var 38) — zero marginal cost via Socrata; only
  the resort-local truth set is scored. Confirm column semantics (variable/estat/base horària encoding,
  forward-labeled timestamps) against 2–3 fresh XEMA API samples (~6 quota calls).
  Backfill ≥2 winters into the verification store; idempotent re-pulls.
- **Files**: `src/minipirineu/xema_opendata.py`, backfill CLI, `config.py`
  (XEMA station set), tests with recorded CSV extracts.
- **Quota**: none (Socrata) + ~6 XEMA API calls one-off · **Effort**: M · **Deps**: S0.1
- **Acceptance**: verification store holds ≥2 winters × ≥6 stations of 30-min series;
  a recorded-fixture test pins the column semantics; re-running backfill changes 0 rows.

### S0.4 Truth pipeline (truth-A / truth-B, gates, dead band)
- **Mechanism**: as in §1. Settling-correction coefficients from Helfricht et al.
  2018 / SNTHERM-derived equations — taken from the literature and validated on 2–3
  documented storms, **not guessed**. Manual spot-check against ICGC InfoGruixNEU
  charts (numbers always from XEMA data; the PNG charts are eyeball-only).
- **Files**: `src/minipirineu/truth.py`, unit tests (settling, undercatch transfer,
  bucket alignment incl. forward-labeling), golden-file test on a hand-verified storm.
- **Quota**: none · **Effort**: L · **Deps**: S0.3
- **Acceptance**: golden storm reproduces hand-computed 6h truth within rounding;
  gates demonstrably exclude a synthetic wind-blown and a melt-contaminated bucket.

### S0.5 `verify.py` metric engine
- **Mechanism**: pair builder (forecast column × lead bucket × station) + the §1
  metrics; emits machine-readable JSON + a human report. No knowledge of whether
  pairs came from backtest or live — that's the point.
- **Files**: `src/minipirineu/verify.py`, tests with synthetic pairs of known scores.
- **Quota**: none · **Effort**: M · **Deps**: S0.4
- **Acceptance**: synthetic suites (perfect forecast, constant bias, all-miss) return
  exactly the expected MAE/bias/POD/FAR/CSI; dead band boundary cases pinned.

### S0.6 Pre-winter backtest → frozen baseline
- **Mechanism**: probe the **Previous Runs API** for actual AROME HD / AROME 2.5
  archive depth (open question — one call per model, record in
  `docs/notes/previous-runs-coverage.md`), then fetch `_previous_day1`/`_previous_day2`
  series (true 24/48h leads) at XEMA station points for ~2 winters; recompute the
  derived-HD column from archived precip+temp with the current ratio; score both
  AROME columns with S0.5 against S0.4 truth. **Historical Forecast API is for
  fitting only** (SLR, thresholds, OPG, lapse rates), never lead-time skill claims.
- **Files**: `scripts/backtest.py` (or module), archived raw responses,
  `docs/verification/baseline-<date>.md` + JSON.
- **Quota**: bounded one-off (≤ a few k call-units/day, spread) · **Effort**: L · **Deps**: S0.5
- **Acceptance**: a committed baseline report with per resort/station/lead MAE, bias,
  POD/FAR for both columns over the covered winters; baseline declared **frozen**.
- **Go/no-go downstream**: if Previous Runs turns out not to archive AROME far enough
  back, fall back to: baseline from whatever depth exists + first live winter as the
  confirmation window (do not substitute Historical Forecast for skill numbers).

### S0.7 Live verification loop + "how wrong were we" page
- **Mechanism**: weekly job builds pairs from the live forecast archive (git history
  of `data/openmeteo.json` — already one commit per 6h run — plus datastore raws)
  vs Socrata truth; renders a static verification page (trailing per-band, per-lead
  MAE + snow/rain hit-rate for **all** columns incl. Meteocat, same code as backtest).
  Attribution for Open-Meteo (CC-BY) and Meteocat mandatory.
- **Files**: `render_verification.py` or `render.py` extension, workflow, template.
- **Quota**: none · **Effort**: M · **Deps**: S0.5 (S0.6 for baseline comparison row)
- **Acceptance**: page live on Pages, auto-refreshed weekly, shows the frozen baseline
  next to trailing live scores; missing weeks render as missing, never 0.

### S0.8 XEMA API live ingest port + PiriNeu cutover
- **Mechanism**: port `xema_ingest.py` (station-day endpoint, staleness-gated ~3
  cycles/day ≈ 570/month, morning backfill; readings carry their own timestamps so
  refetches are idempotent) writing to archive + verification store. Purpose: near-
  real-time obs for freshness and Stage 2 obs-anchoring; Socrata remains the
  historical truth. PiriNeu's ingest is already off (T0), so this is a pure
  addition — the 750/month XEMA plan is Mini's alone.
- **Files**: `src/minipirineu/xema_api.py`, `ingest_xema.py`, workflow, tests.
- **Quota**: ~570–720/month of the 750 XEMA plan · **Effort**: M · **Deps**: S0.1 (parallel to S0.4+)
- **Acceptance**: archived payloads accumulate on the datastore branch; parse parity
  with Socrata rows for an overlapping day.

### S0.9 Widen the 6h Open-Meteo fetch + raw-response archiving (ingest wide)
- **Mechanism**: same 9 calls/run, bigger payloads (user decision 2026-07-17 —
  ingest wide, publish narrow): add `relative_humidity_2m`, 10 m wind + gusts, and
  pressure-level T/RH/geopotential at 1000/925/850/700/**600/500 hPa** (warm SW
  flows push the 0 °C level above 700 hPa; **AROME 2.5 only — HD serves no pressure
  levels**, validated M1). Archive every raw response to the datastore before
  parsing: the committed `data/openmeteo.json` keeps only rendered variables, and
  Previous Runs coverage of pressure-level variables is unconfirmed — self-archiving
  is the only guaranteed profile history. Enables Stage 1 wet-bulb calibration and
  the derived-isozero diagnostic (PiriNeu `freezing_level.py` port, **persisting
  `n_crossings`**). **Nothing new renders.**
- **Files**: `openmeteo.py` (vars), `ingest_openmeteo.py` (archive step),
  `forecast.yml` (datastore commit), tests + one wide fixture.
- **Quota**: none extra · **Effort**: S · **Deps**: S0.1
- **Acceptance**: cron archives wide raw responses; profiles non-null for AROME 2.5
  at all stations; rendered page unchanged (golden test); HD's null pressure-level
  series stay `None`.

---

## 4. Stage 1 — known error sources (each gated on the frozen baseline)

*(The former S1.1 — pressure-level T/RH + surface RH fetch — moved into Stage 0 as
S0.9 under the ingest-wide decision of 2026-07-17; Stage 1 is what those data enable.)*

### S1.1 Wet-bulb snow/rain partition (replaces the pure-T taper as phase driver)
- **Mechanism**: Stull wet-bulb from T/RH; partition around ~0.5–1.0 °C wet-bulb
  (Pyrenees air-T threshold ≈1.0 °C), threshold calibrated on backtest; per-band via
  downscaled T + band-interpolated RH.
- **Effort**: M · **Deps**: S0.9 (plus enough archived/backtest profile data to calibrate)
- **Go/no-go**: ships only if phase hit-rate on marginal buckets (band T within
  ±2 °C of 0) improves ≥5 percentage points vs the current T-taper on ≥30 backtest
  events, **without** degrading cm MAE beyond the dead band. The isozero derivation
  is diagnostic/display, not the partition.

### S1.2 Per-bucket precipitation-type / marginal-event flag
- **Mechanism**: wet-bulb profile classification, optionally simplified Bourgouin
  energy-area check (Birk et al. 2021 revision); ECMWF IFS 0.25° native
  `precipitation_type` fetched as a free cross-check column (adds IFS to the same
  calls, not new calls).
- **Effort**: M · **Deps**: S1.1
- **Go/no-go**: flag renders only if backtest POD ≥ 0.7 and FAR ≤ 0.3 vs obs phase
  on marginal buckets.

### S1.3 Orographic precipitation gradient (OPG) above the saturating cell
- **Mechanism**: Open-Meteo's `elevation` downscales T but **not** precip: above the
  highest grid cell precip saturates (validated: Baqueira 2000 m ≡ 2600 m).
  Multiplicative precip-elevation factor for affected bands; literature prior first
  (Vionnet/Quéno: AROME underestimates winter mountain precip), later fitted from
  peak/valley XEMA pairs.
- **Effort**: M · **Deps**: S0 complete
- **Go/no-go**: cm MAE on affected bands improves ≥10 % on backtest wet buckets
  without flipping bias sign on the band below; otherwise revert (single constant,
  trivially reversible).

---

## 5. Stage 2 — QPF improvements (gated on Stage 1 outcomes)

| Item | Mechanism (short) | Effort | Go/no-go |
|---|---|---|---|
| S2.1 Lagged-run blending | AROME updates 3-hourly, we fetch 6-hourly — previous run(s) are free pseudo-members | M | 24h-total cm MAE −≥5 % vs frozen baseline, per-resort average |
| S2.2 Neighborhood sampling | 3–5 grid points around each resort vs convective displacement / double penalty (calls ×3–5, still trivial) | M | event FAR −≥5 points at equal POD, or cm MAE −≥5 % |
| S2.3 HARMONIE / IFS columns | KNMI + DMI HARMONIE (native snowfall, coverage validated 2026-07-14) and ECMWF IFS via the brief's URL-parameter gate | S–M | column promoted from URL-gate to default only after ≥1 winter month scored and MAE ≤ AROME 2.5's |
| S2.4 Flow-regime quantile mapping | classify by 700 hPa wind (NW Atlantic vs Mediterranean easterly); map **event totals / wet 6h buckets with frequency matching** — never raw hours | L | regime-subset event-total MAE −≥10 % without degrading the other regime |
| S2.5 Trailing inverse-MAE weighting | ~30-day window, per resort, live only | M | blended column beats best single column's trailing MAE for 2 consecutive months |
| S2.6 Obs-anchored first buckets | replace elapsed part of current bucket with observed XEMA accumulation (needs S0.8 live obs) | M | first-bucket MAE strictly better on 1 month live; never touches later buckets |
| S2.7 Ensemble spread → probabilities | Open-Meteo Ensemble API (calls ×4): P(≥X cm), snow-line range; AROME stays the central estimate | M | Brier skill > 0 vs climatology on backtest/live before rendering |

---

## 6. Stage 3 — refinements

| Item | Mechanism (short) | Effort | Gate |
|---|---|---|---|
| S3.1 Kuchera SLR A/B | SLR = 12 + 2(271.16 − Tmax) [Tmax > 271.16 K], else 12 + (271.16 − Tmax); A/B only — the fitted piecewise ratio (0.45 → 0 across −2…+1 °C) **stays default** | S | adopt only if cold-event (≤ −5 °C) cm MAE beats the fitted ratio; QPF error dominates, expect a low ceiling |
| S3.2 Snow-depth percentile climatology | daily depth vs 10/30/50/70/90 percentiles from XEMA open data ("molt deficitari → molt excedentari") | M | zero API cost; matches ICGC percentile chart on spot checks |
| S3.3 Per-station Kalman bias filter | temperature first (cleanest signal) | M | band-T MAE −≥15 % live over 1 month |
| S3.4 Analog ensemble | needs ≥1–2 archived seasons | L | deferred until archive depth exists |

---

## 7. Do NOT revisit (from handoff, verbatim intent)

- **Deep learning for phase or cm** — few hundred labelled events/winter, literature
  shows no gain over wet-bulb/logistic near 0 °C, training needs paid GPU. Off the
  table until ≥2–3 verified seasons show simpler methods plateauing.
- **PiriNeu's AEMET GeoTIFF leg** — RGBA legend-bin midpoints, no snow variable, no
  cota, unconfirmed f207/f228 semantics, unrecoverable missed cycles.

## 8. Open questions carried into Stage 0

1. Previous Runs API archive depth for both AROME models (probe first, S0.6).
2. Socrata `nzvn-apee` column semantics vs API payloads (confirm on first pull, S0.3).
3. Settling-correction coefficients at 30-min resolution (literature + storm
   validation, S0.4).
4. Final truth-station set — API codes vs `yqwd-vj5e`; Cadí Nord's station code; any
   further snow-depth EMAs worth archiving (S0.3).
5. ~~Meteocat zone for la_molina~~ **RESOLVED 2026-07-17**: user verified the
   official zone map — baqueira→1, boi-taull→5, la-molina→6 all confirmed
   (docs/notes/meteocat-pronostic-semantics.md). Winter cota-vs-isozero check
   stays as belt-and-braces only.
6. ~~Categorical code semantics~~ **RESOLVED 2026-07-17** via official docs +
   simbols catalog: acumulacio/acumulacioNeu are ordinal BINS (score the
   Meteocat column on bins + cota metres, never continuous cm — spec
   correction). Only the winter `comentari` text shape remains open (S0.2).

## 9. ADRs to draft during Stage 0 (per repo convention, before implementing)

- **ADR-0002**: datastore branch + archive-before-parse as Mini's storage architecture
  (supersedes the narrow reading of the brief's "NO histórico" — see brief amendment 1).
- **ADR-0003**: verification-first gate — frozen baseline, metric spec, go/no-go
  process for forecast-affecting changes.
- **ADR-0004**: truth pipeline design (truth-A/truth-B, gates, dead band) and its
  literature anchors.
