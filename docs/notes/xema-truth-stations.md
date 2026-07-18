# Estaciones de verdad XEMA y semántica del open data (S0.3/T5)

Fecha del sondeo en vivo: **2026-07-18**, contra el portal Socrata
`analisi.transparenciacatalunya.cat` (dataset `nzvn-apee`, sin quota) y las
metadades `yqwd-vj5e` / `4fb2-n3yi`. Resuelve las open questions 2 y 4 del
ROADMAP (semántica de columnas y códigos de estación, incluido Cadí Nord).

## Conjunto de estaciones (config `XEMA_STATIONS`)

Verdad **puntuada** (resort ≠ None), rol high/valley. La verdad de nieve
fresca sale de `snow_truth` (var 38):

| codi | estación | alt (m) | rol | resort | snow_truth |
|---|---|---|---|---|---|
| Z1 | Bonaigua | 2262 | high | baqueira | ✅ |
| YN | Vielha - Elipòrt | 1029 | valley | baqueira | — |
| Z2 | Boí | 2537 | high | boi-taull | ✅ |
| CT | el Pont de Suert | 824 | valley | boi-taull | — |
| Z9 | **Cadí Nord - Prat d'Aguiló** | 2145 | high | la-molina | ✅ |
| ZD | la Tosa d'Alp | 2478 | high | la-molina | — |
| DP | Das - Aeròdrom | 1096 | valley | la-molina | — |

**Cadí Nord = Z9** (resuelto aquí; era la open question del usuario del
2026-07-17). Es la verdad de gruix de neu de La Molina porque **ZD la Tosa
d'Alp no sirve la var 38** (confirmado: var 38 en Z1/Z2/Z9 el 2026-02-01T12:00
UTC devuelve 172/108/153 cm; en ZD y CT → NO DATA). ZD se sigue puntuando para
temperatura/viento.

Verdad **archive-wide** (resort = None): EMAs de alta cota del Pirineo que
sirven var 38, cerca de los resorts. Se backfillean **solo var 38** (coste
marginal cero) y quedan disponibles si el set puntuado alguna vez las necesita:
Z3 Malniu (2229), Z5 Certascan (2398), Z7 Espot (2519), ZE el Port del Comte
(2288), DG Núria (1971).

## Variables (config `XEMA_VARIABLES`, dataset `4fb2-n3yi`)

| var | acrónimo | nombre | unidad | slug store |
|---|---|---|---|---|
| 30 | VV10 | Velocitat del vent a 10 m | m/s | vent_velocitat |
| 31 | DV10 | Direcció de vent 10 m | ° | vent_direccio |
| 50 | VVx10 | Ratxa màxima del vent a 10 m | m/s | vent_ratxa |
| 34 | P | Pressió atmosfèrica | hPa | pressio |
| 36 | RS | Irradiància solar global | W/m² | irradiancia |
| 32 | T | Temperatura | °C | temperatura |
| 33 | HR | Humitat relativa | % | humitat |
| 35 | PPT | Precipitació | mm | precipitacio |
| 38 | GNEU | Gruix de neu a terra | cm | gruix_neu |

Se almacenan como `obs.<slug>`. **La var 9 del borrador del ROADMAP NO existe**
en las metadades de variables de la XEMA — se descarta.

### ⚠️ var 38 cambia de unidad: mm antes de ~2022, cm después (T6, 2026-07-18)

Descubierto al construir el golden de T6. El gruix de neu (var 38) **no está en
las mismas unidades en toda la historia**:

| ventana | Z1 (var 38) | Z2 (var 38) | unidad |
|---|---|---|---|
| 2020-01 (Gloria) | 740–1220 | 780–1550 | **mm** (÷10 = 74–122 / 78–155 cm) |
| 2021-02 | 1210–1650 | 370–880 | **mm** |
| 2022-02 | 178–179 | — | **cm** |
| 2023-02 | 57 | — | cm |
| 2024-02 | 39–44 | 20–37 | cm |
| 2025-03 (storm golden) | ~90–137 | ~57–132 | cm |
| 2026-02-01 (sonda T5) | 172 | 108 | cm |

El salto de escala ×10 ocurre **entre el invierno 2020-21 y el 2021-22**. La
sonda de T5 solo miró 2026 (cm), así que este cambio pasó desapercibido.

**Consecuencia para el backfill multi-invierno (cola de T5) y para T6/T7**: un
backfill que cruce ~2021 mezcla mm y cm, y truth-A calcularía incrementos
disparatados. Antes de congelar el baseline (T10) hay que: (a) fijar la fecha
exacta de corte por estación (bisecar el open data), y (b) normalizar var 38 a
cm en la ingesta (dividir /10 el tramo mm) **o** restringir el periodo de
backtest a la era cm (2022→, que ya da >2 inviernos: 2022-23, 2023-24, 2024-25,
2025-26). La segunda opción es la más limpia y evita depender de una fecha de
corte frágil. El golden de T6 usa a propósito una tormenta de la era cm
(2025-03-09, Z2).

Ojo (para T6/T7): no todas las estaciones tienen todos los sensores. Z1
Bonaigua **no** reporta viento (30/31/50) ni presión (34); Z9 sí reporta
viento. "Missing is missing": el parser no inventa filas para sensores
ausentes. Esto condiciona la función de undercatch basada en viento (T7): en
Z1 no habrá viento y esos buckets caerán en el gate por datos ausentes.

## Semántica del open data `nzvn-apee`

- Columnas: `codi_estacio, codi_variable, data_lectura, valor_lectura,
  codi_base, id`. **No hay columna `codi_estat`** en el open data (a
  diferencia de la API XEMA); la única marca de base es `codi_base`.
- `codi_base = "SH"` = semi-horària. Lecturas cada `:00` y `:30`. El dataset
  **también** trae base horària `HO` (56 M filas) y unos pocos valores de base
  corruptos (`DQ`, `-1,5`, …), pero **nunca en mis 12 estaciones**: en toda su
  historia 2009→ solo aparece `SH` (43,3 M filas, 0 HO). Como la PK del store
  no incluye `codi_base`, la query filtra a `codi_base='SH'` para que "una fila
  por instante" sea estructural y no casual (evita el colapso silencioso de dos
  lecturas del mismo instante con bases distintas).
- `id` = `codi_estacio + codi_variable + DDMMYYhhmm` (clave natural; no se usa,
  la PK del store ya distingue por estación/tiempo/variable).
- Rango temporal: **2009-01-01 → presente** (>2 inviernos de sobra).

### `data_lectura` está en **UTC** (comprobado, no supuesto)

La irradiància solar (var 36) en Z1 el 2026-07-10 tiene su pico a las **11:00**
`data_lectura`. El mediodía solar en Cataluña (~1°E) es ≈ 11:56 UTC; en hora
local CEST el pico caería ≈ 14:00. Por tanto `data_lectura` es UTC. Se
normaliza a `...Z` explícito sin desplazar la etiqueta.

### Etiquetado hacia delante (forward-labeled)

Convención XEMA: la lectura de las 11:00 cubre 11:00–11:30. Se guarda la
etiqueta **tal cual**; el bucketing (T6/T7) la interpreta. Una observación
guarda su hora de lectura como `run_time_utc` **y** `valid_time_utc` (es
observación, no pronóstico). Pendiente de confirmación dura contra la API XEMA
(`scripts/record_xema_parity.py`, ~6 llamadas con key; fixtures para T11).

## Backfill

`python -m minipirineu.ingest_xema 2023-11 2024-05` (un invierno). Troceado por
grupo×mes, idempotente (upserts del store), reanudable, archive-before-parse
(cada página cruda gzip al datastore antes de parsear). Verificado en vivo el
2026-07-18: ventana 2026-02-01..04, estaciones puntuadas = 6762 filas;
re-ejecución → 0 filas nuevas (idempotente). Fixture del parser:
`tests/fixtures/xema_opendata_z1_z9_20260201.json` (Z1+Z9, 91 filas reales).
