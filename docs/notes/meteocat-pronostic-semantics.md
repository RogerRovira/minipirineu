# Semántica del pronòstic Pirineu del Meteocat (validación 2026-07-17)

Fuentes: documentación oficial de la API Meteocat (tablas pegadas por el
mantenedor desde la página de docs, no bot-friendly), **mapa oficial de zonas
verificado visualmente por el mantenedor el 2026-07-17**, catálogo
`referencia/v1/simbols` grabado como fixture, y payloads reales grabados el
mismo día (`tests/fixtures/meteocat/`).

## 1. Zonas: mapeo estación → zona CONFIRMADO

La API usa ids 1,3,4,5,6,7,8 que el mapa oficial numera "Zona 1..7" (¡el id de
la API y el número del mapa NO coinciden!). El mantenedor verificó el mapa:

| Estación | id API | Nombre |
|---|---|---|
| Baqueira | **1** | Vessant nord Pirineu occidental |
| Boí Taüll | **5** | Vessant sud Pirineu occidental |
| La Molina | **6** | Prepirineu oriental |

Evidencia adicional: el punto-etiqueta oficial de la zona 1 (42.704, 0.966,
embebido en meteo.cat/prediccio/pirineu) cae encima de Baqueira. El check
empírico de invierno (correlación cota zonal vs isozero de anchors, port de
`verify_meteocat_zones.py`) queda como validación de cinturón y tirantes, ya
no bloquea nada. Nota: la web usa para la zona 6 el nombre "Prepirineu
oriental"; la API sirve el nombre truncado "Vessant sud Prepirineu or".

## 2. Producto zonas: 3 días de horizonte, actualización ~14:00 local

4 franjas de 6h (`periode` 1) + 1 franja diaria de 24h (`periode` 2).
Ventana móvil de 3 días (hoy..D+2); sin archivo upstream (validado en
PiriNeu: fechas pasadas → HTTP 400). Ingerimos hoy+mañana (2 llamadas/día);
D+2 existe si algún día se quiere ampliar (+~30 llamadas/mes — no cabe con la
rotación de anchors dentro de 100/mes).

## 3. Variables zonales — LAS ACUMULACIONES SON BINS, NO CANTIDADES

| Variable | Franja | Tipo | Valores |
|---|---|---|---|
| cel | 6h | código símbolo | catálogo `simbols.json` (26 códigos cel) |
| visibilitat | 6h | ordinal 1–4 | 1 Excel·lent (>50 km) · 2 Bona (10–50) · 3 Regular (1–10) · 4 Dolenta (<1 km, boira) |
| probabilitat | 6h | ordinal 1–5 | 1 No se n'espera · 2 No es descarta (<10%) · 3 Possible (10–30%) · 4 Probable (30–70%) · 5 Molt probable (>70%) |
| tempesta | 6h | ordinal 1–5 | mismas etiquetas que probabilitat |
| intensitat | 6h | ordinal 1–4 | 1 Feble (<3 mm/30 min) · 2 Moderada (3–20) · 3 Forta (20–40) · 4 Torrencial (>40) |
| cota | 6h | **metros** (numérico real) | cota de nieve |
| acumulacio | 24h | **ordinal 1–6 (bins de lluvia)** | 1 No se n'espera · 2 Minsa (0,1–5 mm) · 3 Poc abundant (5–20) · 4 Abundant (20–50) · 5 Molt abundant (50–100) · 6 Extremadament abundant (>100) |
| acumulacioNeu | 24h | **ordinal 1–6 (bins de nieve)** | 1 No se n'espera · 2 Minsa (<2 cm) · 3 Poc abundant (2–5) · 4 Abundant (5–10) · 5 Molt abundant (10–40) · 6 Extremadament abundant (>40) |
| comentari | 24h | texto | forma real pendiente de un payload de invierno |

**Consecuencia para la verificación** (corrige el spec): la columna Meteocat
se puntúa en cm SOLO vía bins ordinales (acierto de bin, distancia ordinal,
evento/no-evento con umbral de bin) más el error de cota en metros — nunca
como cantidad continua. La tabla de docs dice "mm/cm" en Unitats, pero las
tablas de valores (y el payload real, valor '1' un día seco) confirman bins.

## 4. Variables de pics/refugis (8 pasos trihorarios)

isozero (m), iso-10 (m) bajo cota "totes"; humitat (%), temperatura (°C),
direcció vent (grados), velocitat vent (**m/s**) en cotas 1500/2000/2500/3000.

## 5. Etiquetas

`cel` → catálogo oficial `referencia/v1/simbols` (fixture grabado; 1 llamada
del plan Referència, fuera de la cuota Predicció). Resto → tablas de arriba.
Código desconocido → se muestra el código crudo, nunca rompe.
