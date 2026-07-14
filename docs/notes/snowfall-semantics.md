# Semántica de variables Open-Meteo para los modelos AROME (validación milestone 1)

Fecha de validación: 2026-07-14, contra la API real (forecast + historical-forecast).
Estas conclusiones invalidan dos supuestos del brief y motivaron dos decisiones
de producto (confirmadas por el usuario ese mismo día).

## 1. Qué sirve cada modelo

Sondeado con llamadas reales por modelo (`models=` individual) y confirmado por la
documentación de Open-Meteo ("AROME France HD… smaller selection of weather variables"):

| Variable | AROME HD 1.3 km | AROME 2.5 km |
|---|---|---|
| `snowfall` | ❌ null siempre (también en `minutely_15` y como `snowfall_water_equivalent`) | ✅ cm/hora |
| `precipitation` | ✅ mm/hora | ✅ mm/hora |
| `temperature_2m` | ✅ °C | ✅ °C |
| `freezing_level_height` | ❌ | ❌ (tampoco ARPEGE ni `meteofrance_seamless`) |
| Niveles de presión (850 hPa…) | ❌ | ✅ |

**Decisiones (usuario, 2026-07-14):**
- Columna AROME HD: nieve **derivada** de precipitación + temperatura en cota
  (derivación intra-modelo, no blend). Etiquetada `snowfall_source: "derived"`.
- Cota de congelación: sustituida por **temperatura por cota** (`temperature_2m`
  con downscaling por elevación), que ambos modelos sirven sin llamadas extra.

## 2. Ratio nieve/agua del snowfall nativo

Episodios reales (historical-forecast API, AROME 2.5, Baqueira/La Molina/Boí Taüll,
dic 2025 – feb 2026, 283 horas con nieve y precipitación):

- El ratio horario `snowfall_cm / precipitation_mm` **nunca supera 0.70** → confirma
  que Open-Meteo convierte el equivalente en agua con ratio 7:1 (1 mm agua = 0.7 cm).
- Pero el ratio efectivo es menor incluso en frío (~0.45 cm/mm a T ≤ −2 °C) y cae
  progresivamente hacia 0 en la franja de mezcla, hasta anularse a ~+1 °C. El modelo
  particiona lluvia/nieve; el 0.70 solo aparece en horas de nieve pura.

| T (°C) | ratio observado (cm/mm) |
|---|---|
| ≤ −2 | 0.36 – 0.51 (≈0.45) |
| −1 | 0.30 |
| −0.5 | 0.25 |
| 0 | 0.16 |
| +0.5 | 0.07 |
| ≥ +1 | ≈0 |

## 3. Regla de derivación adoptada (AROME HD)

`cm = mm_precip × ratio(T_cota)` con ratio lineal: **0.45 a T ≤ −2 °C → 0 a T ≥ +1 °C**
(constantes en `config.py`, implementación en `aggregate.snow_ratio`).

Validación contra el snowfall nativo de AROME 2.5 (la derivación aplicada al propio
2.5, que sí tiene verdad terreno):

| Periodo | Combos | Error del total derivado |
|---|---|---|
| ene–feb 2026 (ajuste) | 4 estación/cota | −9 % … +9 % |
| dic 2025 (holdout) | 4 estación/cota | −28 % … −6 % (mes de poca nieve, errores absolutos ~5 cm) |

La regla ingenua (0.7 cm/mm si T ≤ +1 °C) sobreestimaba el total un ~55 %.
Detección de episodios: prácticamente perfecta (0.1 cm perdidos, 4.4 cm falsos
en 2 meses).

## 4. Qué hace realmente el parámetro `elevation`

Comprobado en Baqueira (ene–feb 2026, AROME 2.5):

| elevation | nieve nativa | precip | T media |
|---|---|---|---|
| 1500 | 90.9 cm | 297.6 mm | +0.7 °C |
| 2000 | 142.3 cm | 348.9 mm | −3.2 °C |
| 2600 | **142.3 cm** | **348.9 mm** | −7.1 °C |

- `elevation` **selecciona la celda de malla** que mejor casa con la altitud pedida
  (por eso 1500 ≠ 2000) y **hace downscaling completo de la temperatura** (por eso
  −3.2 ≠ −7.1 con la misma celda).
- `snowfall` y `precipitation` **no se recalculan** a la cota pedida: por encima de
  la celda más alta disponible, la nieve nativa se satura (2000 = 2600) y **no**
  re-particiona lluvia/nieve a la cota. En cota alta la nieve nativa puede
  infraestimar (y en cota baja sobreestimar) cuando la cota de nieve cae a media
  montaña.
- Consecuencia de producto: la diferenciación fina por cota la dan la temperatura
  por cota y la columna derivada; la columna nativa de AROME 2.5 puede repetir el
  mismo valor en cotas contiguas. La web debe mostrarse con la temperatura por cota
  al lado para que esto se lea bien. Posible revisión futura: derivar también la
  columna 2.5 por cota (se decidió mantener el nativo en v1).

## 5. Otras notas

- Con varios modelos en una llamada, las claves horarias van sufijadas:
  `snowfall_meteofrance_arome_france_hd`, etc. Las horas fuera del horizonte del
  modelo llegan como `null` (el pipeline las conserva como `None`, nunca como 0).
- La respuesta ecoa `elevation` (float) → sirve de verificación del downscaling.
- Horizonte efectivo observado: ~48–66 h desde la medianoche local del día de la
  llamada, según la hora del run. Con `forecast_days=3` siempre cubre now+48h,
  con `null` al final que el agregador recorta (`effective_horizon_h`).
- Ambos AROME se actualizan cada 3 h con horizonte de ~2 días (docs Open-Meteo).
