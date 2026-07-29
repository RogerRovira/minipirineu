# ADR 0004 — Pipeline de verdad (truth-A / truth-B, gates, banda muerta)

Estado: aceptada (2026-07-18; ampliada 2026-07-29 con el diseño concreto de
truth-B y los gates al implementarse T7). Concreta la "truth pipeline" del
roadmap (`docs/ROADMAP.md` §1) que el gate de verificación (ADR-0003) da por
supuesta. truth-A se implementa en T6 (`src/minipirineu/truth.py`); truth-B, los
gates de calidad y el merge A/B en T7 (`src/minipirineu/truth_b.py`).

## Problema

El forecast publica `snowfall_cm` = **altura de nieve nueva caída** (HN) por
bucket. Para puntuarlo necesitamos la misma magnitud medida en tierra. El único
sensor de nieve de la XEMA es el gruix de neu a terra (var 38, HS, cm, cada 30
min, `obs.gruix_neu` en el store). HS **no** es HN: entre nevadas el manto se
asienta (compacta) y HS baja sin fusión, y durante una nevada la nieve recién
caída ya se está asentando mientras se acumula. Los incrementos crudos de HS
**subestiman** HN. Hace falta una corrección de asentamiento defendible.

## Decisión

### truth-A — incrementos de gruix de neu corregidos por asentamiento

1. **Despiking + smoothing** de la serie HS de 30 min, en dos etapas (la
   segunda es la novedad frente al roadmap, forzada por los datos reales):
   - **Hampel**: mediana + MAD en ventana centrada; un punto que se desvía
     > `n·MAD` se sustituye por la mediana. Quita picos aislados del ultrasonido
     (pájaros, glitches) sin aplanar un escalón de nieve real; con MAD=0 (serie
     plana) un suelo de escala evita marcar nada.
   - **Media móvil centrada** (Helfricht et al. 2018 suavizan igual el HS
     automático). El Hampel **no** quita el jitter sostenido de ±1–3 cm de un
     paso a otro, y sumar cada subida como nieve fresca infla el total: en el
     storm real 2025-03-09 (Z2), 91 cm sin suavizar vs ~55 cm de subida neta.
     La media móvil (~±1 h) lo amansa (91→71 cm). Coste: difumina un escalón
     brusco a través de un borde de bucket — acotado y aceptable a 6 h. Ventana
     en config (`TRUTH_SMOOTH_WINDOW`, 1 = desactivar; los tests unitarios
     aíslan la lógica de incrementos con 1).

2. **Corrección de asentamiento, esquema de dos capas estilo Anderson (1976)**,
   por paso de 30 min. Física por paso `t → t+Δt` (h = HS despicado):

   ```
   HN(t) = (h(t+Δt) − h(t)) + asentamiento(t, Δt)      [cuando > 0]
   ```

   Dos capas, como en Helfricht et al. (2018): **nieve nueva** (joven) + **manto
   viejo**. Tasa fraccional de metamorfosis destructiva de Anderson (1976):

   ```
   S(T) = c3 · exp(c4 · T)      [por segundo],  T = T_nieve en °C, ≤ 0
   c3 = 2.777·10⁻⁶ s⁻¹  (≈ 1 %/h a 0 °C)
   c4 = 0.04 °C⁻¹        (más frío ⇒ asienta más lento)
   ```

   El factor de densidad de Anderson `f(ρ) = exp(−c5·(ρ−150))` para ρ>150
   kg/m³ (c5 = 0.046 m³/kg) hace que el manto estacional (ρ≈300–400) tenga
   `f≈10⁻⁴`: **su metamorfosis destructiva es despreciable**. Por eso solo la
   capa joven asienta; el manto viejo se trata como no-asentante. Una capa deja
   de ser "joven" tras `NEW_SNOW_AGE_H` (pasa al manto viejo). `T_nieve` se
   aproxima por `min(T_aire, 0)` (`obs.temperatura`, var 32; a falta de dato,
   un default de config). El estado se reconcilia con HS observado cada paso, de
   modo que `Σ(capas) == HS` siempre.

3. **Sin snowfall negativo**: si `HN ≤ 0` (el manto pierde altura más de lo que
   el modelo asienta: fusión, erosión por viento, o asentamiento real > modelado)
   el paso aporta 0, nunca un número negativo, y el stack de capas se reconcilia
   a la baja para seguir cuadrando con HS.

4. **Buckets de 6 h en UTC** alineados a 00/06/12/18. Los incrementos son
   *forward-labeled* (la lectura de las 11:00 cubre 11:00–11:30, ya pinado en
   T5): el incremento del par `(t_prev, t_cur)` se imputa al bucket de `t_prev`
   (inicio de la ventana de acumulación). Un hueco de datos mayor que
   `MAX_STEP_MIN` marca sus buckets como **incompletos → None**, nunca 0
   ("missing is missing", como en todo el pipeline). *La verificación bucketea
   en UTC; los buckets locales de `aggregate.to_buckets` son de presentación del
   sitio, otra cosa. T8/T9 deben usar estos mismos bordes UTC.*

### truth-B — verdad independiente desde el pluviómetro (implementada en T7)

Del pluviómetro calefactado (var 35, mm) a nieve fresca (cm), en tres pasos:

1. **Undercatch WMO-SPICE**, función de transferencia universal de
   **Kochendorfer et al. (2017)** para pluviómetro **sin apantallar**:
   `CE = exp(−a·U·(1 − atan(b·T) + c))`, con `U` = viento a altura del
   pluviómetro y `T` = temperatura del aire. `a=0.0785, b=0.729, c=0.407`.
   El viento XEMA es a 10 m (var 30): se reduce a altura de pluviómetro (~2 m)
   con un perfil logarítmico (`WIND_10M_TO_GAUGE≈0.767`, z0≈0.01 m) y se capa a
   la banda de ajuste SPICE (~7.2 m/s) — por encima el gate de viento ya excluyó
   el bucket. `precip_ajustada = gauge / CE`, con suelo en CE para que 1/CE no
   se dispare. CE=1 sin viento; frío y viento la bajan.
2. **Fracción sólida** (split nieve/lluvia grueso, **independiente** del taper
   del forecast; la fase fina es Stage 1/S1.1): rampa lineal 1→0 entre
   `TRUTHB_SNOW_ALL_T_C=0.5` y `TRUTHB_RAIN_ALL_T_C=2.0 °C` de T del aire.
3. **Densidad de nieve fresca** `ρ(T) = 67.9 + 51.3·exp(T/2.6)` kg/m³
   (**Hedstrom & Pomeroy 1998**). Su asíntota fría D0=67.9 **coincide** con la
   media de alta montaña de Helfricht et al. (2018), 68 ± 9 — porque la nieve
   nueva de alta cota cae fría. `cm = SWE_mm · 100 / ρ`, ρ acotada a
   [50, 200] kg/m³. Esto ata la densidad de truth-B a la misma fuente que el
   asentamiento de truth-A. La dependencia de viento (densificación por
   ventisca) se deja fuera: los buckets con viento sostenido caen antes en el
   gate de viento, así que rara vez llegan a truth-B.

Coeficientes en `config.py` (no hardcodeados), reafinables contra tormentas sin
tocar el algoritmo.

### Gates de calidad y merge A/B (T7)

El merge reporta el cm de **truth-A** (medida directa de nieve) y usa truth-B
para validarlo, en este orden por bucket:

1. **Gate de viento** → `excluded="wind"`. Se puertea con el **viento MEDIO a
   10 m (var 30) > `GATE_WIND_MEAN_MS=6.0`**, no con la ráfaga (var 50). El
   viento sostenido redistribuye la nieve (la sopla fuera del ultrasonido y
   pasa/entra del pluviómetro) → tanto ΔHS como la captura del gauge dejan de
   medir la caída real. 6 m/s es el umbral de inicio de ventisca de nieve seca
   (Li & Pomeroy 1997), dentro de la banda 6–8 del roadmap; conservador a
   propósito (la verificación prefiere pocos buckets limpios). **Corrección
   empírica frente al diseño previo (que decía "ráfaga var 50 > 6–8 m/s")**: la
   tormenta ventosa de Cadí Nord (Z9, 2025-03-09; golden de T7) mostró que
   puertear por la ráfaga máxima descarta el 58 % de los buckets — una sola
   ráfaga hunde un bucket de 6 h por lo demás en calma — y **mal-etiqueta como
   viento buckets que son de fusión**. La ráfaga se conserva como diagnóstico en
   `BucketB.gust_max_ms` pero no puertea.
2. **Firma de fusión / lluvia-sobre-nieve** (T>0 °C, HS bajando, pluviómetro
   acumulando) → flag `phase_only`, sin excluir. En fusión A y B *deben*
   discrepar (A≈0 mientras el gauge acumula), así que se salta el gate de
   divergencia; el cm es cota inferior y el bucket sólo es puntuable por fase
   (T8 manda los pares `phase_only` a métricas de evento, no a MAE de cm).
3. **Gate de divergencia A/B** → `excluded="ab_divergence"` si
   `|A−B| > max(GATE_AB_ABS_CM=3, GATE_AB_FRAC=0.6·max(A,B))`. Generoso porque
   ambos métodos cargan incertidumbre real; caza sólo conflictos gruesos (sonda
   rimada que suelta un catch-up, gauge congelado, ventisca).
4. Si sólo hay A (p. ej. Z1 sin viento) → `method="A"`, flag `unconfirmed`. Si
   sólo hay B (hueco en la sonda de nieve) → `method="B"`, flag `gauge_only`.
   Si no hay ninguno → `excluded="incomplete"`. **Missing sigue siendo
   missing**: nunca un 0 fabricado.

`exclusion_stats()` resume la disposición por bucket (% excluido por
estación/invierno — criterio de aceptación de T7).

- **Banda muerta** (definida en ADR-0003, aplicada por `verify.py` en T8):
  `|error| ≤ max(2 cm, 20 % de obs)` cuenta como acierto — no se persigue el
  ruido de sensor (±1–2 cm).

## Anclas de literatura (no inventadas)

- **Anderson, E. A. (1976)**, *A point energy and mass balance model of a snow
  cover*, NOAA Tech. Report NWS 19 — coeficientes c3/c4/c5 del asentamiento.
- **Helfricht, K. et al. (2018)**, *Obtaining sub-daily new snow density from
  automated measurements in high mountain regions*, HESS 22, 2655–2671 — método
  de dos capas sobre HS automático sub-diario, densidad de nieve nueva
  68 ± 9 kg/m³, corrección de asentamiento ≈ 13 %. Es el caso exacto (HS
  automático de alta montaña → nieve nueva) del que sale este diseño.
- **Kochendorfer, J. et al. (2017)**, *The quantification and correction of
  wind-induced precipitation measurement errors*, HESS 21, 1973 — función de
  transferencia universal WMO-SPICE del undercatch (coef. a/b/c del pluviómetro
  sin apantallar), consolidada en Kochendorfer et al. (2020). Base de truth-B.
- **Hedstrom, N. R. & Pomeroy, J. W. (1998)**, *Measurements and modelling of
  snow interception in the boreal forest*, Hydrol. Process. 12, 1611 —
  parametrización de densidad de nieve fresca `ρ(T)=67.9+51.3·exp(T/2.6)`; su
  asíntota fría 67.9 kg/m³ ancla la densidad de truth-B en el mismo 68 ± 9 de
  Helfricht.
- **Li, L. & Pomeroy, J. W. (1997)**, *Estimates of threshold wind speeds for
  snow transport…*, J. Appl. Meteorol. 36, 205 — umbral de inicio de ventisca
  (~6–7 m/s en nieve seca) del gate de viento.

## Simplificaciones asumidas (y por qué son aceptables)

- **Sin overburden explícito**: sin SWE ni densidad por capa no se puede
  parametrizar el término de sobrecarga de Anderson. Se omite; durante una
  nevada el asentamiento lo domina la metamorfosis destructiva de la nieve
  nueva, y la corrección total es ~13 % (Helfricht) → cae **dentro de la banda
  muerta**. El manto viejo no-asentante no genera falsos positivos: su
  asentamiento real se absorbe como "sin nieve nueva" (HN≤0 → 0) reconciliando
  HS.
- **Metamorfosis de Anderson sobreestima la compactación de nieve nueva** (bien
  documentado en la literatura reciente). Se toma truth-A como corrección
  **conservadora** y como *prior a confirmar* (ADR-0003), no como verdad
  absoluta: el gate A/B de T7 lo contrasta contra truth-B, que es independiente.
- **Sensor congelado/rimado**: el sonda de nieve puede quedarse plano durante la
  nevada y luego soltar la acumulación en un solo paso (visto en 2025-03-09 Z2:
  12 h clavado en 60.0 → salto de +43). El timing intra-6h de esos buckets es
  irrecuperable desde HS solo; truth-A lo imputa al bucket del salto. Detectarlo
  y excluirlo es trabajo del gate A/B de T7 (truth-B con pluviómetro no se
  congela igual), no de truth-A.

## Consecuencias

- Existe una verdad de nieve fresca por (estación, bucket 6 h UTC) para el
  backtest (T10) y el loop live (T11), computada con el **mismo código** que
  consumirá `verify.py`.
- ZD la Tosa d'Alp no sirve var 38 → fuera del set de nieve (ya decidido en T5;
  La Molina usa Z9 Cadí Nord). Las estaciones sin viento (p. ej. Z1) tendrán
  truth-A pero caerán en el gate de undercatch de truth-B por dato ausente (T7).
- Los coeficientes viven en `config.py` (no hardcodeados), de modo que se pueden
  reafinar contra tormentas documentadas sin tocar el algoritmo.
