# ADR 0001 — Open-Meteo como fuente de los modelos AROME (y acceso raster directo como fallback)

Estado: aceptada (decisión registrada en el brief `MiniPrevi_PiriNeu.md`, sección
"Stack decision" y "Complexity flags"). Este ADR la documenta y detalla el plan
de fallback exigido por el brief.

## Decisión

Los datos de modelo (AROME HD 1.3 km y AROME 2.5 km de Météo-France) se obtienen
de la **Open-Meteo Forecast API** (`https://api.open-meteo.com/v1/forecast`) con
`models=meteofrance_arome_france_hd,meteofrance_arome_france` explícito (nunca
`best_match`) y el parámetro `elevation` por cota. Sin auth, JSON directo,
~9 llamadas por run (límite no comercial ~10.000/día).

## Contexto y alternativas rechazadas (del brief)

- **WCS GeoTIFF directo de Météo-France** — rechazada: reimplementa lo que
  Open-Meteo ya hace (con auth, más requests, parsing raster). **Es el fallback
  documentado abajo.**
- **GeoTIFF georreferenciado de AEMET (HARMONIE)** — rechazada: raster RGBA a
  decodificar contra una escala de color (intervalos, no valores) y sin variable
  de nieve; la pieza más frágil con el menor valor.
- **GRIB2 directo (meteo.data.gouv.fr / ECMWF Open Data 0.25°)** — rechazada:
  dominio completo sin subsetting y curva de eccodes para datos que ya llegan en
  JSON; el ECMWF directo es además peor resolución que el IFS 9 km vía Open-Meteo.
- **OpenWeatherMap One Call** — rechazada: blend propietario de modelos globales,
  justo la categoría de producto cuyo fallo en el Pirineo motiva este proyecto.

## Riesgo aceptado

Open-Meteo es un intermediario único para los grids. Mitigaciones: es open
source y self-hosteable; volumen de uso trivial; y el acceso directo queda
documentado aquí para poder actuar en frío si Open-Meteo falla, cambia
condiciones o degrada las variables (como ya se detectó en milestone 1 con
`snowfall` de AROME HD — ver `docs/notes/snowfall-semantics.md`).

## Fallback: WCS GeoTIFF directo de Météo-France

Camino de acción si hay que prescindir de Open-Meteo:

1. **Cuenta y clave**: registrarse en el portal de APIs de Météo-France
   (`https://portail-api.meteofrance.fr`), suscribirse a las APIs de "Paquets
   AROME" / "AROME PI" (WCS). La auth es por API key (header `apikey`) u OAuth2
   client-credentials según producto; las claves caducan y hay que rotarlas.
2. **Endpoints WCS** (OGC Web Coverage Service 2.0.1), por modelo:
   - AROME 0.01° (HD) y AROME 0.025°: `GetCapabilities` para listar coverages
     (una por variable y run), `DescribeCoverage` para ejes, y `GetCoverage` con
     `subset=lat(...)`, `subset=long(...)`, `subset=time("...")` y
     `format=image/tiff` para descargar el recorte GeoTIFF de cada variable/hora.
   - Variables equivalentes: precipitación total/nieve acumulada por hora y
     temperatura a 2 m (los nombres exactos de coverage cambian por run; se
     resuelven vía `GetCapabilities`).
3. **Parsing**: `rasterio` (nueva dependencia) para abrir cada GeoTIFF y muestrear
   el valor en las coordenadas de cada estación (`sample()`); sin downscaling por
   elevación de serie — habría que elegir celda por altitud con un DEM o aceptar
   la celda nativa (ver nota de semántica: es también lo que hace Open-Meteo para
   nieve/precipitación).
4. **Coste**: una request por variable × hora × modelo (o paquetes por run según
   producto) — decenas-cientos de requests por run frente a 9; presupuestar
   contra los rate limits del portal.
5. **Encaje en el pipeline**: solo cambia la capa de ingesta; `aggregate.py`, el
   JSON generado, el render y los workflows quedan iguales. Escribir un
   `openmeteo.py`-equivalente (`meteofrance_wcs.py`) que produzca la misma
   estructura normalizada `{time, models: {…: {snowfall_cm, precipitation_mm,
   temperature_c}}}`.

## Consecuencias

Las del brief, más lo aprendido en milestone 1: la dependencia funcional real es
mayor de lo previsto porque Open-Meteo no sirve `snowfall` para AROME HD ni
`freezing_level_height` para ningún modelo Météo-France — la web usa nieve
derivada (HD) y temperatura por cota, decisiones que un acceso directo también
tendría que replicar (los GRIB de AROME HD tampoco publican nieve; es una
limitación del producto de difusión, no de Open-Meteo).
