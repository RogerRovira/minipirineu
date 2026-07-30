# ADR 0003 — Gate de verificación para todo cambio del forecast publicado

Estado: aceptada (2026-07-17). Formaliza el principio rector del roadmap
(`docs/ROADMAP.md` §1) y la enmienda 2 del brief.

## Decisión

Ningún cambio que altere el output publicado (nuevas columnas, particiones de
fase, correcciones de QPF, blends, SLR…) se despliega sin:

1. **Harness previo**: `verify.py` computa el spec de métricas — MAE/bias de cm
   por (estación, cota/estación XEMA, lead) en buckets de 6h y totales 24/48h
   con banda muerta (|error| ≤ max(2 cm, 20 %)); POD/FAR/CSI de eventos (día de
   nieve ≥1 cm/24h y evento por bucket); error de cota de transición en metros
   (desde Stage 1) — con **el mismo código** sobre backtest y datos live.
2. **Baseline congelado**: el informe del backtest pre-invierno (Previous Runs
   API + verdad XEMA, ~2 inviernos) por estación/cota/lead para las dos
   columnas AROME. Se congela por commit y se referencia aquí cuando exista.
   **Primer baseline (T10, 2026-07-29):** `docs/verification/baseline-2026-07-29.md`
   (+ `.json`) — lead 24 h, columnas `arome_25`/`arome_hd`, estaciones Z1/Z2/Z9,
   ~1.5 inviernos (2024-01-19 → 2025-04-30). Hallazgos: la 2.5 nativa bate a la
   HD derivada en MAE/POD/CSI en todas las ventanas (pt 5); ambas infra-predicen
   (bias −0.7 cm/6 h, −2.9 cm/día; POD ~0.3; FAR ~0.05). **Congelado en el commit
   `fd4c035`** (T10/S0.6b) — reproducible desde los raws de la rama `datastore`
   (`raw/openmeteo_backtest/`, `raw/xema/`). Toda mejora futura del forecast se
   mide contra estos números.
3. **Umbral go/no-go explícito y falsable por item** (definidos en el roadmap;
   p. ej. bulbo húmedo: +≥5 puntos de acierto de fase en buckets marginales
   sobre ≥30 eventos sin degradar el MAE de cm más allá de la banda muerta).
   Lo que no supera su umbral, se revierte.
4. Las correcciones derivadas del backtest son **priors a confirmar en vivo**
   (las versiones de modelo derivan a lo largo del periodo archivado), no
   constantes finales.
5. La columna nativa AROME 2.5 es el **baseline interno primario**: la columna
   derivada de HD debe batirla para justificar su existencia. Comparaciones
   solo mismo-periodo y misma-verdad; números de literatura son contexto, nunca
   filas de la tabla.

## Contexto

El brief ya prohibía blends sin verificación. PiriNeu enseñó el modo de fallo
contrario: priors ajustados a mano en producción sin medir nada. Los recursos
que hacían "imposible" verificar antes del invierno ya no faltan (Previous Runs
API desde ~2024; histórico XEMA completo en open data sin cuota).

## Consecuencias

- Stage 0 (instrumentación) bloquea todo lo demás; es el precio de no volver a
  publicar números no medidos.
- Los datos pueden ingerirse y archivarse ANTES de su gate (ADR-0002: archive
  wide, publish narrow) — el gate aplica al render, no a la ingesta.
- Una página pública de verificación muestra los scores trailing junto al
  baseline congelado; dato ausente se muestra ausente, nunca como 0.
