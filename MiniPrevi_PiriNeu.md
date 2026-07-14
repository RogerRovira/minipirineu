
schema: project-brief/v1
status: confirmed
project_name: MiniPiriNeu
Project Brief: MiniPiriNeu
Pitch
Una web que muestra la nieve prevista a 48h por cota (baja/media/alta) en Baqueira, Boí Taüll y La Molina, usando los modelos mesoescala de alta resolución (AROME) que los agregadores comerciales no sirven.
Description
Una página estática que se regenera cada ~6h con los últimos runs de modelo. Para cada estación muestra una tabla: cm de nieve prevista a 48h en cota baja, media y alta según AROME HD 1.3 km y AROME 2.5 km (Météo-France, vía Open-Meteo), más la predicción de muntanya del Meteocat de la zona como referencia cualitativa con intervención de predictor humano.
Target user & problem

Who: Grupo de colegas esquiadores del Pirineo catalán.
Problem: Los agregadores (infonieve, snow-forecast) usan modelos globales que fallan sistemáticamente en el Pirineo (orografía, cota de nieve); los modelos mesoescala buenos existen pero nadie los sirve por estación y cota.
Today they: Consultan infonieve/snow-forecast y complementan mirando a mano Meteocat/AEMET/Météo-France, con resultados poco fiables para ventanas de 2-3 días.

Core loop
Usuario abre la web → ve para cada estación los cm de nieve previstos a 48h desglosados por cota baja/media/alta y por modelo → decide qué día y a qué estación va.
Cotas de referencia por estación (confirmadas por el usuario):





























EstaciónBajaMediaAltaBaqueira1.500 m2.000 m2.600 mBoí Taüll2.000 m2.400 m2.750 mLa Molina1.700 m2.100 m2.500 m
Scope
v1 features (3–5)

Ingesta Open-Meteo (AROME HD + AROME 2.5, snowfall y freezing_level_height, parámetro elevation por cota) — done when: para las 3 estaciones salen cm de nieve para intervalos de 6h hasta las 48h por cota baja/media/alta de ambos modelos, con models= explícito (no best_match).
Ingesta predicció de muntanya Meteocat (API, por zonas, 2 llamadas/día) — done when: cada estación catalana muestra la predicción de su zona.
Vista web estática (tabla estaciones × modelos × cotas en intervalos de 6h hasta 48h) — done when: el grupo la abre desde el móvil y decide en <30 segundos.
Refresh automático vía cron con timestamp de frescura por fuente — done when: los datos nunca tienen más de ~6h y un fallo de ingesta se ve como dato viejo, nunca como dato bueno.

Later (not v1)

Boletines de peligro de aludes: BPA del ICGC (referencia para Pirineo catalán) + boletín nivológico AEMET (complemento Aran/frontera) — why deferred: es información de seguridad, no de forecast; merece diseño propio.
Predicción de montaña AEMET (zona Pirineo Catalán, isotermas 0/-10 °C) — why deferred: solapa con Meteocat como fuente cualitativa; útil solo si Meteocat decepciona.
Columna de contraste inter-familia (ECMWF IFS 9km o DWD ICON-EU vía Open-Meteo) — why deferred: el usuario la considera poco significativa a 48h; un parámetro de URL la reactiva si la verificación lo justifica.
Verificación contra observaciones (XEMA, partes de estación) usando Historical Forecast / Previous Runs API de Open-Meteo — why deferred: requiere acumular una temporada; Open-Meteo elimina la necesidad de archivar nosotros.
Alertas push por umbral de cm — why deferred: primero validar que el forecast es fiable consultándolo.

Non-goals

NO hacer blend/consenso de modelos — reason: sin datos de verificación, un blend es una media arbitraria que puede ser peor que el mejor modelo solo.
NO ingerir grids crudos (GRIB2, GeoTIFF, WCS) en v1 — reason: Open-Meteo ya sirve los mismos datos de modelo en JSON; la ingesta raster directa queda documentada como fallback en el ADR, no se implementa.
NO auth ni cuentas de usuario — reason: grupo pequeño, URL compartida basta.
NO app móvil nativa — reason: web responsive cubre el uso desde el móvil.
NO histórico ni archivo de forecasts en v1 — reason: solo importa el último run; la verificación futura usará el archivo de Open-Meteo.
NO backend/servidor dinámico — reason: los datos solo cambian cada ~6h; estático regenerado por cron es suficiente y sin mantenimiento.

Complexity flags

Dependencia de un intermediario (Open-Meteo) para los datos de modelo — decisión: aceptada conscientemente. Mitigación: es open source y self-hosteable; el acceso directo (WCS GeoTIFF de Météo-France) queda documentado en el ADR como fallback; uso no comercial dentro de límites (~10.000 calls/día, usamos decenas).
Cuota API Meteocat (100 consultas/mes en predicción) — decisión: simplificada. Ingesta Meteocat desacoplada del cron principal, 2 llamadas/día (~60/mes). XEMA (750/mes) reservada para la fase de verificación (later).
Dependencia general de APIs de terceros — decisión: aceptada conscientemente. Timestamp visible del último dato por fuente; ingestas independientes (el fallo de una no tumba las otras).

Stack decision

Choice: Python 3.12 (solo requests + JSON) · datos de modelo vía Open-Meteo Forecast API con models=meteofrance_arome_france_hd,meteofrance_arome_france y elevation por cota · Meteocat API REST · GitHub Actions con cron cada 6h (Meteocat desacoplado a 2/día) · output JSON + HTML estático publicado en GitHub Pages.
Context: (1) Open-Meteo sirve la salida cruda de AROME ya parseada en JSON con downscaling por elevación — elimina toda la fontanería raster/GRIB. (2) Con 3 estaciones y datos que cambian cada 6h no existe nada dinámico que justifique un servidor.
Alternatives considered:

WCS GeoTIFF directo de Météo-France (rasterio) — rejected because: reimplementa lo que Open-Meteo ya hace, con auth, más requests y parsing raster; queda como fallback documentado si Open-Meteo falla o cambia.
GeoTIFF georreferenciado de AEMET (HARMONIE) — rejected because: raster RGBA a decodificar contra escala de color (intervalos, no valores), sin variable de nieve; la pieza más frágil con el menor valor.
GRIB2 directo (paquetes de meteo.data.gouv.fr o ECMWF Open Data 0.25°) — rejected because: dominio completo sin subsetting y curva eccodes para datos que ya llegan en JSON; ECMWF directo además es peor resolución que el IFS 9km de Open-Meteo.
OpenWeatherMap One Call — rejected because: blend propietario de modelos globales, la categoría de producto cuyo fallo en el Pirineo motiva este proyecto.
FastAPI en VPS o Synology propio — rejected because: añade una máquina que mantener para servir datos estáticos entre runs.


Consequences: Más fácil: pipeline trivial (requests + JSON), cero infraestructura, cero coste, la verificación futura usa el archivo de Open-Meteo. Más difícil: intermediario como punto único de fallo para los grids (mitigado arriba); sin columna inter-familia en v1, la coincidencia AROME HD / AROME 2.5 no es confirmación independiente (mismo modelo, misma física, mismas condiciones de contorno); debugging de jobs en Actions más incómodo que en local.

Working constraints

Knows: Python (pipelines propios en producción), simulación CFD (campos en malla, interpolación), Git/GitHub, APIs REST, JSON, admin de infraestructura.
New to: APIs meteorológicas concretas (Open-Meteo, Meteocat), semántica de variables de forecast (snowfall acumulado horario, freezing level, downscaling por elevación).
Team: solo (usuarios: grupo pequeño de colegas).
AI workflow: Claude Code.
Testing appetite: serious testing.
Git habits: commits por milestone.
Deploy target: GitHub Pages (build vía GitHub Actions).

Milestones (rough)

Datos en mano: script que llama a Open-Meteo para las 3 estaciones × 3 cotas × 2 modelos AROME y escribe un JSON con cm de nieve 48h y cota de congelación. Valida la semántica de las variables y el downscaling por elevación.
Publicado y automático: página HTML estática generada desde ese JSON, desplegada en GitHub Pages, regenerada por cron cada 6h con timestamps de frescura. Ya es usable y mejor que infonieve.
v1 completa: columna Meteocat muntanya añadida (ingesta 2/día, mapeo estación→zona), cumpliendo todos los acceptance checks.

Open questions & risks

Mapeo estación → zona de predicción de muntanya del Meteocat (Baqueira→Aran-Franja Nord, etc.): confirmar códigos de zona en milestone 3.
Semántica exacta de snowfall en Open-Meteo por modelo (acumulación horaria en cm, ratio nieve/agua asumido ~7:1): validar en milestone 1 contra un episodio real.
Cobertura de KNMI/DMI Harmonie Europe (Open-Meteo) sobre el Pirineo: si cubren, habría un HARMONIE inter-familia con valores reales gratis — comprobar en milestone 1.
