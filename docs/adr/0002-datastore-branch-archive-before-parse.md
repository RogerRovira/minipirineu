# ADR 0002 — Rama `datastore` y archivo raw antes de parsear

Estado: aceptada (2026-07-17). Implementa el principio *archive wide, publish
narrow* del roadmap (`docs/ROADMAP.md`) y la enmienda 1 del brief
(`docs/brief-amendments.md`), que acota el non-goal "NO histórico" a "no
re-servir históricos a usuarios".

## Decisión

1. **Archivo raw obligatorio antes de parsear** para toda fuente sin archivo
   upstream: pronòstic Meteocat (ventana móvil de 3 días — validado en PiriNeu
   2026-07-12) y observaciones XEMA vía API. También se archivan las respuestas
   crudas de Open-Meteo del cron de 6h (la cobertura de variables de niveles de
   presión en la Previous Runs API no está confirmada; autoarchivarse es la
   única garantía de histórico de perfiles). Implementación:
   `src/minipirineu/archive.py` (`Archive.store()` jamás inspecciona el
   payload), layout `raw/<fuente>/YYYY/MM/DD/<STAMP>_<nombre>.gz`,
   byte-compatible con el archivo de PiriNeu.
2. **Los datos viven en una rama `datastore`** del mismo repo, separada de
   `main`: archivo raw + `verification.sqlite` (vista reconstruible del
   archivo, `src/minipirineu/store.py`, upserts idempotentes). Los workflows la
   clonan/bootstrapean y commitean con retry (acciones compuestas en
   `.github/actions/`). `main` conserva en `data/` solo los JSON publicados que
   la web renderiza. En local, `./datastore/` (gitignored) o
   `$MINIPIRINEU_DATA_DIR`.

## Contexto

La verificación (milestone "Medible") necesita historia que las fuentes no
guardan. Sin archivo propio, la columna Meteocat no podrá puntuarse jamás. El
patrón rama-datastore está validado en PiriNeu (ADR-0002 de aquel repo):
gratis, sin infraestructura nueva, y un fallo de parseo nunca pierde datos.

## Alternativas rechazadas

- **Commitear raws a `main`**: infla el historial del repo de producto y mezcla
  datos con código; cada clone/CI paga el peso.
- **Artifacts de GitHub Actions**: retención máxima 90 días — inservible como
  archivo.
- **Almacenamiento externo (S3, B2…)**: coste y credenciales nuevas; contra la
  restricción free-tier del proyecto.
- **Repo separado de datos**: funciona, pero añade un segundo repo/token que
  mantener; la rama en el mismo repo da lo mismo con menos piezas.

## Consecuencias y mitigaciones

- La rama crece (~1–2 MB/día estimados con las respuestas Open-Meteo anchas
  gzip). El historial de la rama es irrelevante — el estado son los ficheros —
  así que puede **squashearse periódicamente a un solo commit** si el repo
  engorda; los raws en sí no se borran nunca.
- SQLite en la rama se sobreescribe en cada commit (fichero binario): es vista
  reconstruible, su historial no importa.
- Concurrencia: un solo escritor a la vez (grupo de concurrencia en los
  workflows + push con rebase-retry, patrón PiriNeu).
