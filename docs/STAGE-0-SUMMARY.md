# Stage 0 — resumen de implementación y progreso del roadmap

Estado: **2026-07-30**. Compañero *as-built* de `docs/STAGE-0-IMPLEMENTATION.md`
(el plan) y `docs/ROADMAP.md`. Resume qué se construyó en Stage 0, qué se
desvió del plan y por qué, y qué viene (T11d + Stage 1). Rama `s03-xema-truth`
(pusheada a origin), 257 tests verdes.

## 1. Qué es Stage 0 y por qué

Stage 0 es la **instrumentación de verificación** que bloquea todo lo demás
(ADR-0003): ningún cambio del forecast publicado se despliega sin batir un
número de baseline medido. Construye la tubería completa **verdad → motor de
métricas → baseline congelado → página "¿cuánto nos equivocamos?"**, con el
mismo código puntuando backtest y live.

## 2. Estado de las tareas (T0–T11)

| Tarea | Sub | Qué hace | Estado | Commit |
|---|---|---|---|---|
| T0 | — | Apagar ingesta PiriNeu (liberar cuota XEMA) | ✅ | (previo) |
| T1–T3 | — | Meteocat pronòstic: ingest + columna muntanya + archivo | ✅ en `main` | a79c630 |
| T4 | S0.9 | Fetch 6h ancho + archivo raw + freezing-level diagnóstico | ✅ en `main` | abc695b |
| T5 | S0.3 | Ingesta de verdad XEMA (Socrata open data) + robustez | ✅ | cf4994b, b89e1e6 |
| T6 | S0.4a | truth-A: nieve fresca por bucket desde el gruix de neu | ✅ | a9c1ee5 |
| T7 | S0.4b | truth-B (undercatch+densidad) + gates A/B + merge | ✅ | b979e13 |
| T8 | S0.5 | `verify.py`: motor de métricas único (backtest = live) | ✅ | 2417760 |
| T9 | S0.6a | Fetch de backtest AROME (Previous Runs API) + guard | ✅ | 3029374 |
| T10 | S0.6b | Baseline congelado pre-invierno + informe | ✅ | fd4c035 / a1d8eef |
| T11a | S0.7 | Página de verificación (baseline vs live) | ✅ | 0ee8148 |
| T11b | S0.7 | Live-archive pairing (runs archivados → verdad trailing) | ✅ | a3a0b92 |
| T11c | S0.8 | Ingesta near-real-time XEMA (meteo.cat API) + parity | ✅ | aef920e |
| **T11d** | S0.7/8 | **Workflows (cron XEMA API + job semanal) + Pages** | ⏳ **pendiente** | — |

Módulos nuevos de este bloque: `previous_runs.py`, `backfill_forecast.py`,
`render_verification.py`, `xema_api.py`, `ingest_xema_api.py`, `live_forecast.py`
(+ `verify.py`/`truth.py`/`truth_b.py` de T6–T8). Datos y docs:
`docs/verification/baseline-2026-07-29.{md,json}`,
`docs/notes/previous-runs-coverage.md`, `tests/fixtures/{xema_api,previous_runs_*,openmeteo_live_*}`.

## 3. El baseline congelado (T10) — el número que gatea todo

Congelado en el commit **`fd4c035`** (referenciado en ADR-0003 §Decisión pt 2).
2 inviernos reales (2024-01-19 → 2025-04, estaciones Z1/Z2/Z9), verdad usable
89–95 %. Informe completo en `docs/verification/baseline-2026-07-29.md`.

- **Ambas columnas AROME infra-predicen**: sesgo −0.7 cm/6 h, −2.9 cm/día;
  POD ~0.3; FAR ~0.05 → el fallo dominante es **omisión**, no falsa alarma.
- **La 2.5 nativa bate a la HD derivada** en MAE/POD/CSI en todas las ventanas
  ⇒ ADR-0003 pt 5: la columna HD derivada **no justifica** ser el número
  primario a 24 h de lead. La 2.5 nativa es el ancla del baseline.

## 4. Divergencias respecto al plan (y por qué)

1. **Semántica de variables AROME (M1, enmienda del brief)**: AROME HD no sirve
   `snowfall` en ninguna forma → su columna es **derivada** de precip+T; ningún
   modelo Météo-France sirve `freezing_level_height` → la `temperature_2m` por
   banda hace de línea de nieve. Base de todo lo demás (`docs/notes/snowfall-semantics.md`).
2. **S0.9 absorbido dentro de Stage 0** (decisión "ingest wide" 2026-07-17, T4):
   niveles de presión T/RH + RH de superficie ya se **archivan** en cada run;
   Stage 1 (wet-bulb) es lo que esos datos habilitan, no una fetch nueva.
3. **T9 / Previous Runs API**: el archivo solo da **lead de 24 h** — `previous_day2`
   (48 h) vuelve null para AROME (más allá de su horizonte ~51 h). Y arranca el
   **2024-01-19T12:00Z**, no "~ene 2024" genérico → **~1.5 inviernos**, no 2
   (2023-24 empieza a mediados de enero). Es el caso de contingencia previsto en
   el roadmap §3. *Salvedad single-model*: Open-Meteo solo sufija las claves
   horarias con el modelo si se piden ≥2 → hay que pedir siempre los dos AROME.
4. **T10 / agregación 24 h**: `verify.daily_totals` agrupaba el día por **run**
   (correcto para el live: un run cubre el día a leads crecientes), pero en el
   backtest de lead fijo cada bucket de 6 h tiene su propio run (valid−24 h) →
   **0 días completos**. Se añadió el modo `daily_by="date"` (default `"run"`
   sin cambios) — hallazgo de T10, no previsto en T8.
5. **T11a / página por columna**: el roadmap pedía "per-band, per-lead"; la
   página rinde **por columna** (baseline vs live, 6 h y día). El desglose
   per-lead vive en el JSON pero no se rinde (ver punto 7).
6. **T11b / alineación de buckets (la desviación de diseño importante)**: el JSON
   publicado (`data/openmeteo.json`) usa buckets en **hora local** (Europe/Madrid);
   la verdad es UTC → **no casan**. La vía correcta (y la simetría ADR-0003) es
   re-bucketizar a UTC los **raws horarios** de `raw/openmeteo/` — la cláusula
   "plus datastore raws" del roadmap. Además se **descartan los buckets anteriores
   al run** (análisis/nowcast, no forecast). Limitación abierta: `by_column_lead`
   tiene leads fraccionales (por el stamp de fetch); cuantizar a 6 h queda como mejora.
7. **T11c / S0.8**: el roadmap listaba `ingest_xema.py`, pero ese nombre ya era
   la ingesta Socrata (T5) → el feed near-real-time es un módulo nuevo,
   `ingest_xema_api.py`. La API sirve **dos formas** de respuesta (all-stations =
   lista; one-station con `codiEstacio` = plano); el script de parity heredado
   comparaba el `codi` de *variable* con el de *estación* → se corrigió, y la
   **parity real da 6/6, 48 instantes, 0 discrepancias**.
8. **Revisión de código (max)**: encontró y se corrigieron 2 defectos latentes —
   `backfill_forecast` ignoraba flags mal escritos (→ argparse) y
   `daily_totals(date)` podía doblar un día en un store multi-run (→ dedup por
   bucket).

Lo demás siguió el plan: truth-A/B y gates (ADR-0004) tal cual; `verify.py` como
motor único; archive-before-parse (ADR-0002) en todas las ingestas nuevas.

## 5. Qué viene

### T11d — cierre operativo de Stage 0 (único pendiente)
Necesita el **entorno GitHub del usuario** (no ejecutable desde aquí):
- **Cron XEMA API** (`ingest_xema_api`, gated ~3×/día) → escribe a la rama `datastore`.
- **Job semanal de verificación**: refresca la verdad Socrata, corre
  `live_forecast` + `render_verification`, publica `site/verificacion.html` en Pages.
- Secreto `METEOCAT_API_KEY` (ya en Actions), rama `datastore`, Pages activados.

Operativo pendiente además: **preservar los raws del backtest** (128 MB locales en
`./datastore`) en la rama `datastore` (ADR-0002); cruzar las 3 tormentas del
baseline con **InfoGruixNEU** (validación externa del usuario).

### Stage 1 — fuentes de error conocidas (cada una gateada contra el baseline)
Habilitado por los datos anchos que Stage 0 ya archiva (S0.9). Cada item **solo
se despliega si bate su número del baseline congelado**:
- **S1.1 Partición nieve/lluvia por bulbo húmedo** (Stull desde T/RH; reemplaza
  el taper de T puro). Go/no-go: +≥5 pp de acierto de fase en buckets marginales
  sobre ≥30 eventos, sin degradar el MAE de cm más allá de la banda muerta.
- **S1.2 Tipo de precipitación por bucket / flag de evento marginal** (clasificación
  de perfil wet-bulb + `precipitation_type` de ECMWF IFS 0.25° como columna de
  contraste gratis). Go/no-go: POD ≥ 0.7 y FAR ≤ 0.3 vs fase observada.
- **S1.3 Gradiente orográfico de precipitación (OPG)**: Open-Meteo baja la T con
  `elevation` pero **no** la precip → por encima de la celda más alta la precip
  satura. Factor multiplicativo por banda, prior de literatura primero, luego
  ajustado con pares pico/valle XEMA. Go/no-go: MAE de cm −≥10 % en bandas
  afectadas sin voltear el signo del sesgo en la banda inferior.

El baseline de este documento (sesgo negativo sistemático, POD ~0.3) marca
exactamente dónde Stage 1 tiene margen: **infra-predicción** — el primer
candidato natural es el OPG (S1.3) sobre buckets húmedos.
