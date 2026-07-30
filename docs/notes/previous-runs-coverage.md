# Cobertura de la Previous Runs API para AROME (probe T9/S0.6a)

Fecha del sondeo: **2026-07-29**, contra la API real
(`previous-runs-api.open-meteo.com`). Fija el rango de fechas del backtest
(T10) y resuelve la pregunta abierta 1 del roadmap ("Previous Runs API archive
depth for both AROME models"). El script reproducible es
`scripts/probe_previous_runs.py`; la respuesta recortada de referencia queda en
`tests/fixtures/previous_runs_arome_z1_20250201.json`.

## Qué es esta API y por qué (no la Historical Forecast)

La Previous Runs API sirve, para cada instante de validez `T`, el valor que
predijo el run inicializado **N días antes** (`{variable}_previous_day{N}`).
Eso da un **lead fijo por run**, que es justo lo que el gate de verificación
exige para afirmar skill por lead (ADR-0003). La Historical Forecast API, en
cambio, cose para cada hora el run más reciente disponible (lead corto variable)
y por eso el roadmap la reserva **solo para ajuste** (SLR, umbrales, OPG), nunca
para números de skill por lead.

Endpoint: `https://previous-runs-api.open-meteo.com/v1/forecast`. Acepta
`start_date`/`end_date` (rango histórico), `models=` explícito, `elevation`, y
`timezone` (usamos **UTC** para casar con los buckets de verdad, que son UTC —
ADR-0004). Con varios modelos las claves horarias se sufijan por modelo, igual
que en la Forecast API. La estructura exacta de clave, confirmada en el sondeo:

```
{variable}_{model_id}                       # run base (cosido, NO se usa)
{variable}_previous_day{N}_{model_id}        # run de hace N días (lead fijo)
```

## Hallazgo 1 — solo existe el lead de 24 h (`previous_day1`) para AROME

`previous_day2` (lead ~48 h) vuelve **entero a null** para los dos modelos
AROME, incluso en un invierno donde `previous_day1` está 100 % poblado
(2025-02-01..07: día1 168/168 no-nulos, día2 0/168). **No es un fallo de la
query**: con la misma sintaxis, `ecmwf_ifs025` devuelve `previous_day2` y hasta
`previous_day5` completos. La razón es física, no de archivo:

- `previous_day1` = run de hace 24 h. Para cubrir las horas de "hoy" necesita
  leads de 24–48 h. El horizonte de AROME es ~51 h → **cubre → poblado**.
- `previous_day2` = run de hace 48 h. Para las mismas horas necesitaría leads de
  48–72 h → **más allá del horizonte de AROME → vacío**.

**Consecuencia (go/no-go):** el baseline congelado de AROME es un **baseline de
lead 24 h**, no 24/48 h. Es exactamente el caso de contingencia previsto en el
roadmap §3 S0.6 ("if Previous Runs turns out not to archive AROME far enough
back, fall back to..."). El skill a 48 h de AROME **no es medible** desde esta
API; queda para el primer invierno live (T11) o simplemente fuera del baseline.
El `verify.py` de T8 ya sólo implementa totales de 24 h por esta misma razón
(nota de alcance de T8): encaja sin cambios.

Matiz de lead que T10 debe leer: el lead "real" dentro de `previous_day1` no es
un 24 h clavado — barre ~24–48 h según la hora del día (un run de hace 24 h
alcanza sus horas +24…+48). El diseño del roadmap lo etiqueta nominalmente como
**lead = 24 h** (`run_time = valid_time − 24 h`) y así se implementa
(`previous_runs.PREV_LEAD_H`). Es la misma simplificación asumida al llamarlos
"true 24/48h leads", y la razón de que día2 quede vacío la confirma.

## Hallazgo 2 — profundidad de archivo: desde 2024-01-19T12:00Z

`previous_day1` (ambos modelos, temperatura y precipitación) está poblado de
forma continua **desde 2024-01-19T12:00Z hasta el presente**. Antes de ese
instante: null (probado 2023-11, 2023-12, 2024-01-01..18 → 0 no-nulos; el primer
no-nulo cae exacto en 2024-01-19T12:00 tanto para 2.5 como para HD). El roadmap
suponía "~ene 2024"; el sondeo lo confirma y lo afina al día.

**Inviernos cubiertos para el backtest de lead 24 h:**

| Invierno | Ventana con datos | Nota |
|---|---|---|
| 2023–24 | **2024-01-19 →** primavera 2024 | cola del invierno (desde mediados de enero) |
| 2024–25 | completo (nov 2024 → primavera 2025) | invierno entero |
| 2025–26 | — | live (T11), no backtest |

Es decir ~1.5 inviernos de verdad de lead 24 h: suficiente para un baseline
congelado por estación/lead, con la salvedad de que 2023–24 arranca en enero.

## Hallazgo 3 — HD no sirve `snowfall` (igual que en M1)

`snowfall_previous_day1_meteofrance_arome_france_hd` vuelve **entero a null**
(confirmado también en la respuesta recortada de la fixture). Coherente con la
validación de milestone 1 (`docs/notes/snowfall-semantics.md`): AROME HD no sirve
`snowfall` en ninguna forma. Por tanto la columna HD del backtest es **derivada**
de `precipitation_previous_day1` + `temperature_2m_previous_day1` con
`aggregate.snow_ratio` (la misma regla que la web), no una serie nativa. AROME
2.5 sí sirve `snowfall_previous_day1` nativo (verificado con nieve real en la
fixture: total día1 ≈ 4.0 cm el 2025-02-01 en Z1).

## Variables y columnas que fija T9

Por estación (a sus coordenadas y elevación XEMA), un run por chunk, `timezone=UTC`:

- `temperature_2m_previous_day1`, `precipitation_previous_day1`,
  `snowfall_previous_day1` para ambos modelos (HD devuelve snowfall null; se
  ignora y se deriva).
- Columna `arome_25` = `snowfall_previous_day1` nativo de AROME 2.5, sumado a
  buckets de 6 h UTC.
- Columna `arome_hd` = derivada de precip+T de HD (día1), sumada a buckets de 6 h
  UTC. Se guarda como `fx.snowfall_cm.arome_hd` / `fx.snowfall_cm.arome_25`
  (convención de store de T8), `valid_time` = inicio del bucket de 6 h,
  `run_time` = `valid_time − 24 h`.
- `previous_day2` se ignora (siempre null para AROME): sus buckets salen
  incompletos y no generan filas. Missing sigue siendo missing, nunca 0.

## Salvedad — pedir SIEMPRE ≥2 modelos (claves horarias sin sufijo)

Open-Meteo sólo **sufija** cada clave horaria con el `model_id`
(`{var}_previous_day1_{model_id}`) cuando la petición lleva **más de un**
modelo; con un único `models=` las claves vuelven **sin sufijo**
(`{var}_previous_day1`). Es el mismo comportamiento de la Forecast API en vivo
(ver `openmeteo.normalize_hourly`, `openmeteo.py`).

El parser del backtest (`previous_runs._hourly_series`) lee **sólo** la forma
sufijada. Como `build_params` pide siempre los dos modelos AROME, las claves
siempre llegan sufijadas y encaja. Pero si alguien alguna vez reduce la petición
a un solo modelo, `_hourly_series` no encontrará ninguna clave, todas las series
saldrán vacías y el backfill escribirá **0 filas sin dar error** — justo la
pérdida silenciosa de datos que este proyecto persigue evitar (T5). No es un
bug latente hoy: el invariante de ≥2 modelos está fijado por construcción
(`build_params`) y por test
(`test_build_params_requests_previous_day_series_for_both_models`). Es una nota
para quien refactorice: **quitar un modelo obliga a manejar la clave sin
sufijo** en `_hourly_series`.

## Presupuesto

El backfill (`python -m minipirineu.backfill_forecast`) es un one-off acotado:
las estaciones de verdad de nieve (Z1, Z2, Z9) × ~14 meses ≈ 42 llamadas, cada
una ~decenas de "call units". `--dry-run` imprime el plan y el total estimado, y
un tope duro (`config.BACKTEST_DAILY_CALL_UNIT_CAP`) aborta antes de acercarse al
límite no comercial de ~10 000/día. Repartir la ejecución en varios días es
aceptable (el store hace upsert idempotente; re-correr un rango cambia 0 filas).
