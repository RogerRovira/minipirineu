# Columnas gated HARMONIE/IFS tras `?modelos=todos` (S2.3)

Fecha: 2026-07-18. Implementa la columna de contraste inter-familia que el
brief dejó aparcada ("un parámetro de URL la reactiva") y que el ROADMAP
recoge como S2.3. Las columnas existen en los datos y en el HTML pero están
ocultas por defecto; se ven añadiendo `?modelos=todos` a la URL.

## Sondeo en vivo (2026-07-18, Baqueira 42.698/0.931, elevation 2000)

| Modelo (id Open-Meteo) | snowfall | precipitation | temperature_2m | horizonte útil |
|---|---|---|---|---|
| `knmi_harmonie_arome_europe` | ✅ 48/48 nativo | ✅ | ✅ | ~58 h desde medianoche local |
| `dmi_harmonie_arome_europe` | ✅ 48/48 nativo | ✅ | ✅ | ~56 h |
| `ecmwf_ifs` | ✅ 48/48 nativo | ✅ | ✅ | 72/72 h (los 3 días pedidos) |

Sin huecos internos en ninguna serie; el IFS llega interpolado a 1h (su
salida nativa es 3-horaria), así que las sumas por bloque de 6h no pierden
horas.

## `ecmwf_ifs` ES el IFS HRES de 9 km del brief

El brief pedía "ECMWF IFS 9km ... vía Open-Meteo". Además del documentado
`ecmwf_ifs025` (0.25° ≈ 25 km), la API acepta `ecmwf_ifs`, y el espaciado de
su rejilla lo delata: barriendo latitudes, los centros de celda devueltos van
de 0.0703° en 0.0703° (42.6362, 42.7065, 42.7768…) — exactamente el paso de
anillos de la rejilla octaédrica O1280 del HRES de 9 km. Se usa `ecmwf_ifs`,
no `ecmwf_ifs025`.

Para los HARMONIE el mismo barrido da ~0.0496° (~5.5 km, KNMI) y ~0.0349°
(~3.9 km, DMI), con saltos irregulares porque Open-Meteo elige celda también
por elevación del terreno. Como el nominal de DMI no cuadra (2 km
publicitados), las etiquetas de la web no llevan km salvo el IFS.

## Mecanismo (decisiones)

- **Petición separada** por estación/cota (18 llamadas/run en vez de 9,
  gratis): si Open-Meteo retirase un id gated, ese HTTP 400 rompería toda la
  petición — separado, solo puede romper las columnas experimentales, nunca
  los datos AROME (principio de ingestas independientes).
- **Degradación a `unavailable`**: fallo de red/schema o snowfall nativo
  todo-null ⇒ la entrada del modelo se publica con `"unavailable": true`,
  `intervals: []` y totales `None` (en la web, "—" en todas las celdas).
  Nunca un 0 falso: la suma del total ignora `None`s y un snowfall muerto con
  precipitación viva habría mostrado "0 cm". `validate()` solo acepta
  `unavailable` en modelos gated y con la columna vacía.
- **Gate en el cliente**: las filas llevan `class="gated"` (CSS
  `display:none`); un JS de 3 líneas añade `show-gated` al `body` si
  `?modelos=todos`. Sin backend, coherente con la web estática.
- **Schema**: sigue `minipirineu/openmeteo/v1` — los cambios son aditivos
  (claves `gated`/`unavailable` por modelo, 3 modelos más al final de la
  lista). Snapshots antiguos renderizan igual que antes.

## Criterio de promoción (no cambiado aquí)

ROADMAP S2.3: una columna gated solo pasa a visible por defecto tras ≥1 mes
de invierno puntuado con MAE ≤ el del AROME 2.5. Hasta que exista la
baseline congelada (S0.5–S0.6), las columnas siguen ocultas y sin puntuar;
mientras tanto van acumulando presencia en `data/openmeteo.json` (y en el
datastore cuando T4 aterrice) para poder puntuarlas retroactivamente.

Fixture grabada: `tests/fixtures/openmeteo_gated_baqueira_2000.json`
(petición idéntica a la del ingest gated).
