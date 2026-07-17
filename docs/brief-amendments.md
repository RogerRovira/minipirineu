# Propuesta de enmiendas al brief (`MiniPrevi_PiriNeu.md`)

**Estado: propuesta** (2026-07-17, pendiente de revisión). Tres enmiendas decididas en
`handoff.md`; el texto exacto antes/después está aquí para aplicarlo mecánicamente al
brief tras la aprobación. El brief tiene `status: confirmed`, así que nada se toca
hasta entonces.

---

## Enmienda 1 — Acotar el non-goal "NO histórico"

**Motivo**: el non-goal actual prohíbe de facto el archivo de fuentes sin archivo
upstream. El pronòstic del Meteocat sirve solo una ventana móvil de 3 días (validado
2026-07-12 en PiriNeu): sin archivo propio, la columna de referencia no podrá
puntuarse jamás. El principio operativo pasa a ser *archive wide, publish narrow*.

**Antes** (sección Non-goals):

> NO histórico ni archivo de forecasts en v1 — reason: solo importa el último run; la verificación futura usará el archivo de Open-Meteo.

**Después**:

> NO re-servir forecasts históricos a los usuarios — reason: en la web solo importa el último run. Matiz (2026-07-17): el archivo interno sí es obligatorio (archive-before-parse) para toda fuente SIN archivo upstream — pronòstic Meteocat (ventana móvil de 3 días) y observaciones XEMA vía API. Los modelos vía Open-Meteo no lo necesitan (Previous Runs / Historical Forecast API). Ver docs/adr/0002.

---

## Enmienda 2 — Promover la verificación de "Later" a milestone

**Motivo**: la justificación del aplazamiento ("requiere acumular una temporada") es
obsoleta — la Previous Runs API (forecasts con lead real desde ~ene 2024) y el open
data de la XEMA (histórico semihorario completo, sin cuota) permiten un backtest
pre-invierno. Ningún roadmap item que cambie el output publicado se implementa sin
baseline medido (ver `docs/ROADMAP.md`).

**Antes** (sección Later (not v1)):

> Verificación contra observaciones (XEMA, partes de estación) usando Historical Forecast / Previous Runs API de Open-Meteo — why deferred: requiere acumular una temporada; Open-Meteo elimina la necesidad de archivar nosotros.

**Después**: eliminar la entrada de "Later" y añadir un milestone 4 tras "v1 completa":

> Medible: harness de verificación contra observaciones XEMA (truth por incrementos de espesor + pluviómetro corregido), backtest pre-invierno con la Previous Runs API (~2 inviernos) y baseline congelado por estación/cota/lead — done when: una página de verificación muestra MAE por cota y lead y hit-rate nieve/lluvia de TODAS las columnas (ambos AROME y Meteocat), calculados por el mismo código sobre backtest y datos live. La Historical Forecast API se usa solo para ajustar (SLR, umbrales, OPG), nunca para afirmar skill por lead.

---

## Enmienda 3 — Corregir la feature v1 que aún nombra `freezing_level_height`

**Motivo**: validado en milestone 1 (`docs/notes/snowfall-semantics.md`): ningún
modelo Météo-France sirve `freezing_level_height` vía Open-Meteo, y AROME HD no sirve
`snowfall`. El texto de la feature debe reflejar lo que la web ya hace (temperatura
por cota; nieve derivada en HD) y lo planificado (partición por bulbo húmedo, Stage 1).

**Antes** (sección v1 features):

> Ingesta Open-Meteo (AROME HD + AROME 2.5, snowfall y freezing_level_height, parámetro elevation por cota) — done when: para las 3 estaciones salen cm de nieve para intervalos de 6h hasta las 48h por cota baja/media/alta de ambos modelos, con models= explícito (no best_match).

**Después**:

> Ingesta Open-Meteo (AROME HD + AROME 2.5, parámetro elevation por cota; snowfall nativo en 2.5 y derivado de precipitación + temperatura en HD; el papel de la cota de congelación lo juega la temperatura por cota — freezing_level_height no existe para modelos MF vía Open-Meteo, validado en M1; partición por bulbo húmedo planificada tras verificación) — done when: para las 3 estaciones salen cm de nieve para intervalos de 6h hasta las 48h por cota baja/media/alta de ambos modelos, con models= explícito (no best_match).

---

## Aplicación

Tras la aprobación: aplicar los tres reemplazos de texto en `MiniPrevi_PiriNeu.md`,
añadir una línea al inicio del brief tipo `Amended: 2026-07-17 (ver
docs/brief-amendments.md)`, y actualizar la sección "Milestones" de `CLAUDE.md`
(añadir el milestone 4) junto con su "Layout" (que ya lista módulos meteocat aún no
existentes — se vuelven reales en Stage 0 T3).
