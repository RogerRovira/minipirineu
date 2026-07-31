# Gradiente orográfico de precipitación (S1.3): medición, prior y gate

Fecha: **2026-07-31**. Cubre el item S1.3 del `docs/ROADMAP.md` §4. Código:
`src/minipirineu/opg.py`; evidencia reproducible:
`scripts/detect_opg_saturation.py` (sin cuota, sobre el historial ya commiteado).

## 1. El problema, medido y no supuesto

Open-Meteo **baja la temperatura** a la cota que pedimos con `elevation`, pero
**no la precipitación**: `elevation` solo dirige la *selección de celda* (la API
elige la celda vecina de altitud parecida). Cuando la cota pedida está por
encima de la celda más alta del entorno, **la misma celda responde a todas las
cotas superiores** y la precipitación deja de reaccionar a la altura. La API no
publica la altitud de la celda usada (`elevation` en la respuesta es la que le
pedimos), así que la saturación se detecta por su firma: **dos cotas cuya serie
de precipitación es idéntica comparten celda**.

Medido sobre las **39 revisiones commiteadas de `data/openmeteo.json`**
(runs reales de 6 h, 2026-07-22 → 2026-07-31; cada run trae las 3 cotas de los
3 resorts y los 2 modelos). Un par solo cuenta si su cota **inferior** está
mojada (≥ 2 mm/48 h): dos cotas secas son idénticas trivialmente y no prueban
nada.

| resort | modelo | par (m) | runs mojados | idénticos | distintos | Σalta/Σbaja |
|---|---|---|---|---|---|---|
| baqueira | AROME 2.5 | 1500→2000 | 10 | 0 | 10 | 1.329 |
| baqueira | AROME 2.5 | **2000→2600** | 10 | **10** | 0 | **1.000** |
| baqueira | AROME HD | 1500→2000 | 10 | 0 | 10 | 1.186 |
| baqueira | AROME HD | **2000→2600** | 11 | **11** | 0 | **1.000** |
| boi-taull | AROME 2.5 | 2000→2400 | 10 | 0 | 10 | 1.175 |
| boi-taull | AROME 2.5 | 2400→2750 | 12 | 1 | 11 | 1.092 |
| boi-taull | AROME HD | 2000→2400 | 12 | 1 | 11 | 1.048 |
| boi-taull | AROME HD | 2400→2750 | 11 | 0 | 11 | 1.076 |
| la-molina | AROME 2.5 | 1700→2100 | 10 | 0 | 10 | 1.409 |
| la-molina | AROME 2.5 | 2100→2500 | 14 | 0 | 14 | 1.022 |
| la-molina | AROME HD | 1700→2100 | 9 | 0 | 9 | 1.053 |
| la-molina | AROME HD | **2100→2500** | 9 | **9** | 0 | **1.000** |

**Cotas de saturación resultantes** (`config.OPG_REFERENCE_ELEVATION_M`),
con el acuerdo entre runs decidibles:

| punto | modelo | referencia | acuerdo |
|---|---|---|---|
| baqueira | AROME 2.5 | **2000 m** | 10/10 |
| baqueira | AROME HD | **2000 m** | 11/11 |
| boi-taull | AROME 2.5 | sin saturación | 9/10 |
| boi-taull | AROME HD | sin saturación | 10/11 |
| la-molina | AROME 2.5 | sin saturación | 9/9 |
| la-molina | AROME HD | **2100 m** | 9/9 |

Esto **confirma el supuesto del roadmap** ("validado: Baqueira 2000 m ≡ 2600 m")
y lo acota: la saturación **no** es universal — Boí Taüll resuelve celda propia
en las tres cotas, y La Molina solo satura en HD. La corrección, por tanto,
afecta hoy a **dos bandas**: baqueira/alta (ambos modelos) y la-molina/alta (HD).

Los dos runs disidentes de Boí Taüll (1 par idéntico de 12) no cambian la
decisión —la mayoría y ambas cotas contiguas dicen que no satura—, pero quedan
registrados: si esa proporción crece, la selección de celda de Open-Meteo se
está moviendo. Por eso cada run vivo guarda su veredicto (`derived.opg_reference_m.<modelo>`
en el store) y la ingesta **alerta por stderr** si contradice esta tabla —la
misma disciplina de drift que los anchors de Meteocat.

⚠️ Estos 39 runs son de **julio** (convección de verano). La *saturación* es una
propiedad geométrica de la rejilla (qué celda contesta), no del régimen
meteorológico, así que las cotas de referencia valen todo el año. La *magnitud*
del gradiente medida aquí, en cambio, es de verano: se usa solo como
orden de magnitud, no como ajuste (§2).

Confirmación independiente **en invierno**: el sondeo de M1
(`docs/notes/snowfall-semantics.md` §4, Baqueira ene–feb 2026, AROME 2.5) ya dio
1500 → 297.6 mm y **2000 = 2600 → 348.9 mm** de precipitación (y 142.3 cm de
nieve nativa en ambas), con la T sí bajando (−3.2 → −7.1 °C). Misma cota de
saturación, otra estación del año, dos meses de datos: la referencia de 2000 m
en Baqueira no depende del régimen.

## 2. La corrección y su prior

    factor(z) = 1 + OPG_PER_100M · (z − z_ref)/100 m,  tope OPG_MAX_FACTOR
    factor(z) = 1  si z ≤ z_ref  o  si no hay z_ref

Se aplica a la precipitación y, equivalentemente, a la columna de nieve: la
ratio derivada (AROME HD) y la partición nativa (AROME 2.5) son **lineales en
precipitación a temperatura fija**, y la T no la toca el OPG. Por eso el sitio
escala las series horarias y las rutas de verificación escalan los cm ya
bucketizados: mismo número.

**`OPG_PER_100M = +3 %/100 m` es un prior, no un ajuste** (ADR-0003 pt 4). Sus
dos anclas:

1. **El gradiente que AROME sí resuelve entre sus propias celdas** en estos
   mismos puntos: mediana **+2.64 %/100 m** (media +3.64) sobre los 9 pares no
   saturados de la tabla anterior. La corrección no inventa un gradiente
   ajeno al modelo: **continúa el suyo** allí donde la selección de celda lo
   trunca.
2. **La literatura**: los gradientes orográficos invernales de latitudes medias
   caen típicamente en ~3–5 %/100 m, y las evaluaciones de AROME en Pirineos y
   Alpes (Quéno et al. 2016, *The Cryosphere*; Vionnet et al. 2016,
   *J. Hydrometeorol.*) documentan **infraestimación de la precipitación
   invernal en cota alta** — el mismo signo que mide nuestro baseline congelado
   (bias −0.73 cm/6 h, POD ~0.3: el fallo dominante es **omisión**).

El tope `OPG_MAX_FACTOR = 1.35` existe para que una extrapolación larga no
fabrique nieve; con las cotas actuales el factor máximo real es ×1.18
(baqueira/alta, 600 m sobre la referencia).

**Cómo se sustituye el prior por un ajuste**: `python -m minipirineu.opg fit
<inicio> <fin>` estima el gradiente con pares pico/valle de la XEMA
(precipitación var 35, corregida de undercatch con la misma función de
Kochendorfer que usa truth-B, §ADR-0004). Salvedad grande: **Z1 Bonaigua y Z2
Boí no tienen pluviómetro** (docs/notes/xema-truth-stations.md), así que el
único par real hoy es **Z9 Cadí Nord (2145) / DP Das (1096)**, y Das está en la
cubeta de la Cerdanya (sombra pluviométrica): el ajuste saldrá sesgado **alto**.
Tratarlo como cota superior del gradiente, no como verdad.

## 3. Qué NO está medido (y cómo se mide)

- **Las cotas de saturación de los puntos de verificación** (Z1, Z2, Z9). El
  backtest y el pairing live piden **una sola cota** por punto, así que su
  saturación no se puede leer del archivo. Hoy **heredan la de su resort** y
  `opg.resolve_reference` lo marca como `inherited` — un prior explícito, nunca
  una medición disfrazada. Se mide con
  `python scripts/probe_opg_saturation.py --start … --end … --paste`
  (escalera de cotas sobre el mismo punto, ~4 llamadas gratis por punto) y se
  pega en `config.OPG_PROBED_STATION_ELEVATION_M`.
- **El gradiente invernal**: prior hasta que `opg fit` corra sobre un invierno
  real de la XEMA.

Mientras Z1/Z2/Z9 hereden, los factores efectivos del backtest son modestos —
Z1 ×1.079 (2262 m sobre 2000), Z9 ×1.014 (HD), Z2 ×1.000 — así que **el gate
puede quedarse sin señal suficiente**. Ese es el orden correcto: medir primero
los puntos (probe), después juzgar la corrección.

## 4. Procedimiento del gate (go/no-go de S1.3)

Umbral del roadmap: **MAE de cm −≥10 % en las bandas afectadas sobre buckets
húmedos del backtest, sin voltear el signo del sesgo**. Nada de esto renderiza
hasta que pase (ADR-0003): `config.OPG_ENABLED = False`.

```bash
export MINIPIRINEU_DATA_DIR=datastore
# 0) (recomendado) medir las cotas de saturación de los puntos XEMA
python scripts/probe_opg_saturation.py --start 2025-02-01 --end 2025-02-07 --paste
# 1) reconstruir las columnas del backtest: cada una escribe ahora su variante _opg
python -m minipirineu.backfill_forecast 2024-01 2024-05
python -m minipirineu.backfill_forecast 2024-11 2025-04
# 2) veredicto
python -m minipirineu.opg gate 2024-01-19T00:00:00Z 2025-05-01T00:00:00Z \
    --out-md docs/verification/opg-gate-<fecha>.md
```

Lecturas del veredicto:

- **PASS** → poner `OPG_ENABLED = True`, commitear el informe del gate y anotar
  el cambio de output publicado; confirmarlo después en vivo (prior→live,
  ADR-0003 pt 4).
- **no-go por ganancia insuficiente** → revertir es cambiar una constante
  (`OPG_PER_100M`) o dejar el flag en False: nada más depende de ello.
- **no-go por overshoot** (el sesgo negativo pasa a positivo) → el gradiente es
  demasiado agresivo para esas cotas; bajar el prior y volver a puntuar.
- **"no OPG-affected wet buckets"** → no es un aprobado: significa que ningún
  punto puntuado está por encima de su cota de saturación (o que faltan las
  medidas del probe). Sin señal no hay gate.

Detalle de interpretación: el roadmap pide "sin voltear el signo del sesgo en la
banda inferior". Las bandas inferiores tienen factor 1.0 **por construcción** (no
se les escribe fila `_opg` siquiera), así que su sesgo no puede cambiar; el
riesgo real es el **overshoot en la banda corregida**, y ahí es donde
`evaluate_gate` aplica la comprobación de signo. La invariancia de las bandas no
afectadas está fijada por tests, no por medición.

## 5. Columnas y contrato de datos

- `fx.snowfall_cm.<columna>_opg` en el store de verificación, **junto a** la
  columna sin corregir, nunca en su lugar (T9 backtest y T11b live escriben
  ambas). Un punto con factor 1.0 no escribe variante: sería idéntica.
- `derived.opg_reference_m.<modelo>` — veredicto de saturación por run
  (valor NULL = "decidido: no satura"; sin fila = "el run no permitía decidir").
- `data/openmeteo.json`: **sin cambios** mientras `OPG_ENABLED` sea False. Con
  el flag activo, cada banda corregida añade `opg_factor` a su modelo.
