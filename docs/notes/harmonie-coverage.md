# Cobertura de KNMI/DMI HARMONIE Europe sobre el Pirineo (open question, milestone 1)

Fecha de comprobación: 2026-07-14, llamada real a Open-Meteo en Baqueira
(42.698, 0.931, elevation 2000).

**Resultado: ambos modelos cubren el Pirineo con valores reales**, incluido
`snowfall` nativo:

| Modelo (id Open-Meteo) | snowfall | precipitation | temperature_2m |
|---|---|---|---|
| `knmi_harmonie_arome_europe` | ✅ 48/48 | ✅ 48/48 | ✅ 48/48 |
| `dmi_harmonie_arome_europe` | ✅ 48/48 | ✅ 48/48 | ✅ 48/48 |

Implicación (feature "Later" del brief): existe una columna de contraste
inter-familia HARMONIE gratis y con snowfall nativo, activable si la
verificación futura lo justifica. No se implementa en v1 (decisión del brief:
el usuario la considera poco significativa a 48h).

**Addendum 2026-07-18**: implementadas como columnas gated tras
`?modelos=todos` (S2.3), junto al IFS de 9 km (`ecmwf_ifs`). Ver
`docs/notes/gated-model-columns.md`.
