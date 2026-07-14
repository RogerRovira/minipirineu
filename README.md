# MiniPiriNeu

Nieve prevista a 48h por cota (baja/media/alta) en Baqueira, Boí Taüll y La Molina,
usando los modelos mesoescala AROME HD 1.3 km y AROME 2.5 km (Météo-France, vía
Open-Meteo) más la predicción de muntanya del Meteocat como referencia cualitativa.

**Web: <https://rogerrovira.github.io/minipirineu/>**

Web estática regenerada cada ~6h por GitHub Actions y publicada en GitHub Pages.
El brief del proyecto (fuente de verdad de las decisiones) está en
[`MiniPrevi_PiriNeu.md`](MiniPrevi_PiriNeu.md); los hallazgos sobre la semántica
de las variables (qué sirve cada modelo AROME y qué hace el parámetro
`elevation`) están en [`docs/notes/snowfall-semantics.md`](docs/notes/snowfall-semantics.md).

## Uso local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# ingesta Open-Meteo (escribe data/openmeteo.json)
python -m minipirineu.ingest_openmeteo

# tests (los tests live contra APIs reales son opt-in)
pytest
pytest -m live
```
