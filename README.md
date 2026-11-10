# job-radar

Buscador y filtro local de avisos de Data Engineering / BI en **Suiza, Países Bajos y
Luxemburgo**. Corre en tu Mac, desde tu IP, con costo $0.

> **Proyecto personal.** Lo armé para mi propia búsqueda y lo comparto como ejemplo. Respetá los
> términos de uso y el `robots.txt` de cada sitio, usá solo endpoints públicos (nunca cookies ni
> credenciales) y mantené un volumen bajo de pedidos. `config/profile.yaml` es un perfil de
> ejemplo: reemplazalo por el tuyo. Licencia MIT (ver `LICENSE`).

```
fuentes ──> pre-filtro ──> dedup ──> SQLite ──> Gemini (features) ──> score en Python ──> CSV
(JobSpy, SwissDevJobs,   (título,   (id, empresa+título,              │
 IamExpat, ATS)          idioma)     simhash)                          └─> Claude Desktop (MCP):
                                                                          segunda opinión
```

- **Ingesta:** LinkedIn e Indeed vía JobSpy (solo endpoints públicos, sin cookies; `data engineer`
  todos los días desde la última corrida y dos búsquedas rotativas por día);
  SwissDevJobs, IamExpat y los ATS de las empresas que elijas (Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday, Recruitee, Personio, SuccessFactors, BrassRing) y consultoras con
  Matador Jobs en WordPress (RM Group) o con feed RSS de avisos (PROSTAFF).
- **Pre-filtro barato:** excluye por título (intern, junior, Werkstudent…) y por idioma del
  aviso (fr/nl, o en alemán pidiendo C1) antes de gastar LLM.
- **Gemini** solo extrae datos (idiomas y nivel, residencia, seniority, rol, stack). El
  **score 0–100 lo calcula Python** con los pesos de `config/scoring.yaml`, así que se puede
  recalcular sin volver a llamar al LLM.

## Setup

Requisitos: macOS y [uv](https://docs.astral.sh/uv/). Si es la primera vez y la búsqueda no es la
mía, seguí [docs/ADAPTAR.md](docs/ADAPTAR.md): qué archivos cambiar para tu perfil, tus países y
tus empresas.

Clonalo fuera de `~/Documents` (macOS no deja que launchd lea ahí), por ejemplo en `~/code`:

```bash
git clone https://github.com/FranciscoRossi48/job-radar-public.git ~/code/job-radar
```

```bash
cd ~/code/job-radar && uv sync
```

```bash
cp .env.example .env
```

Abrí `.env` y pegá tu clave de Gemini en `GEMINI_API_KEY=` (se crea gratis en
<https://aistudio.google.com/apikey>). Ese archivo no se sube a git.

Revisá tu perfil en `config/profile.yaml` (resumen, idiomas, stack, países donde ya residís).

Probá que todo esté bien:

```bash
uv run pytest
```

## Comandos

| Comando | Qué hace |
|---|---|
| `uv run job-radar run-daily` | Corrida completa: ingest → evaluate → export. Es lo que corre launchd. |
| `uv run job-radar ingest` | Solo ingesta. Opciones: `-s linkedin` / `-s ats` / `-s greenhouse:token`, `-c CH`, `-q "data engineer"`, `--limit 10` |
| `uv run job-radar evaluate -n 20` | Evalúa con Gemini hasta 20 avisos pendientes. |
| `uv run job-radar export` | **Novedades**: avisos con score ≥ umbral que todavía no salieron en ningún reporte. Se agregan a `exports/shortlist_<hoy>.csv` y quedan marcados. |
| `uv run job-radar export --all` | **Histórico**: ranking completo de todo lo aprobado, en `exports/ranking_all.csv` (no marca nada). |
| `uv run job-radar export --day 2026-10-15` | Lo evaluado ese día, en `exports/evaluated_<día>.csv`. `--min-score 60` cambia el umbral. |
| `uv run job-radar dedup` | Re-deduplica la base (útil tras cambiar los umbrales de dedup). |
| `uv run job-radar rescore` | Recalcula todos los scores con los pesos actuales (no usa el LLM). |
| `uv run job-radar reprefilter` | Re-aplica el pre-filtro a lo ya guardado (después de cambiar `prefilter.yaml`). |
| `uv run job-radar backup` | Respalda `jobs.db`, comprimida, en `Job Radar/backups` de Drive (lo hace `run-daily` en cada corrida). Se conservan 7 días. Para restaurar: `gunzip -c jobs_FECHA.db.gz > jobs.db`. |
| `uv run job-radar stats` | Conteos por estado, fuente y evaluador. |
| `uv run job-radar decide <id> applied\|discarded\|clear --note "..."` | Registra una decisión a mano (también lo hace Claude con `set_decisions`). |
| `uv run job-radar applications` | Lista de ofertas a las que aplicaste. |
| `uv run job-radar usage` | Requests y tokens de Gemini por día y modelo (para controlar el gasto). |
| `uv run job-radar mcp-config` | Bloque de configuración para Claude Desktop. |

## Uso diario

1. launchd corre `run-daily` todas las mañanas (ver abajo).
2. Abrí `exports/shortlist_AAAA-MM-DD.csv`: solo avisos **nuevos para vos** (nunca salieron en
   un CSV anterior) con score ≥ umbral, ordenados. Para el ranking acumulado: `export --all`.
   El CSV también se copia a **Google Drive** (`Mi unidad/Job Radar`, vía Google Drive para
   escritorio), así que lo podés abrir desde el celular. Otras carpetas: `exports_mirror_dirs`
   en `config/app.yaml`.
3. Para los dudosos, en **Claude Desktop** (pestaña Chat) pedí:
   *"Usá job-radar: traeme un lote en modo shortlist, evaluálo y guardá las evaluaciones."*
   Claude revisa los que Gemini puntuó entre 50 y 80 y su evaluación pasa a ser la vigente.

## Claude Desktop (MCP)

```bash
uv run job-radar mcp-config
```

Copiá el bloque que imprime dentro de
`~/Library/Application Support/Claude/claude_desktop_config.json`, **con Claude Desktop
cerrado (Cmd+Q)**: la app reescribe ese archivo al cerrarse y borra los cambios hechos
mientras corría. Al reabrirla, job-radar aparece en los conectores.

Herramientas:
- `get_batch`: reserva un lote por 30 min con el aviso completo y el rubric. Modo `shortlist`
  (los dudosos, o un rango con `min_score`/`max_score`) o `pending` (sin evaluar).
- `save_evaluations`: guarda las evaluaciones de Claude; el servidor calcula el score.
- `get_shortlist`, `get_job_detail`, `get_stats`.
- `set_decisions`: registra que aplicaste (`applied`) o descartaste (`discarded`) una oferta.
  Las decididas no vuelven a aparecer en lotes, shortlist ni en el CSV diario.
- `get_applications`: tus postulaciones, con fecha y nota.
- Prompt `rubric`.

## Agregar empresas

Editá `config/companies.yaml`. Cada empresa es una línea:

```yaml
companies:
  - {name: GetYourGuide, ats: greenhouse, token: getyourguide, country: CH}
  - {name: Roche, ats: workday, token: roche, country: CH, extra: {host: wd3, site: roche-ext}}
```

El `token` sale de la URL de la página de empleos de la empresa; el archivo explica dónde
mirar para cada ATS. Probá una empresa nueva sola:

```bash
uv run job-radar ingest -s greenhouse:getyourguide
```

Solo se guardan avisos de CH/NL/LU (ciudades en `config/searches.yaml: geo`).

## Ajustar pesos y filtros

| Archivo | Qué controla |
|---|---|
| `config/scoring.yaml` | Puntaje base, exclusiones duras, penalizaciones (idioma, Romandía/Ticino, on-site), bonus (stack, seniority, rol), umbral del CSV. |
| `config/profile.yaml` | Tus idiomas (CEFR), stack (+ `stack_aliases`) y `residency_countries`. El stack suma puntos si el LLM lo detecta **o** si aparece literalmente en el aviso. |
| `config/prefilter.yaml` | Palabras excluidas en títulos, títulos relevantes, idiomas excluidos, patrones de alemán alto. |
| `config/searches.yaml` | Queries fijas y rotativas, ventana desde la última corrida, países activos, fuentes y pausas entre requests. |
| `config/llm.yaml` | Cadena de modelos de Gemini y sus límites. |

Después de tocar `scoring.yaml` o `profile.yaml`, corré `uv run job-radar rescore`. Después
de tocar `prefilter.yaml`, corré `uv run job-radar reprefilter` y después `dedup` (reabre avisos descartados por título que ahora son relevantes).

**Cuando te mudes:** agregá `CH` a `residency_countries` en `profile.yaml` y corré `rescore`:
los avisos que piden "vivir actualmente en Suiza" dejan de excluirse.

## Gemini: plan y costos

La suscripción **Google AI Pro** (la app de Gemini) **no** incluye la API: es un producto
aparte. job-radar usa la API con tu clave de `.env`.

- **Plan gratis:** los modelos Flash tienen 20 pedidos por día y, cuando hay mucha demanda,
  Google rechaza primero a las cuentas gratis (error 503).
- **Plan pago (prepago, activo):** sin esos topes y con prioridad. Cada aviso usa unos
  2.000 tokens. Controlá el consumo con `uv run job-radar usage` y el costo real en
  <https://aistudio.google.com/usage>. Si el saldo llega a $0, la evaluación corta y los
  avisos quedan pendientes hasta que cargues crédito.

`config/llm.yaml` define la cadena de modelos (el mejor primero, los Lite de respaldo) y un
tope diario por modelo (`rpd`), que también limita el gasto. Al LLM solo se le manda la
descripción condensada y un resumen corto de tu perfil, nunca tu CV.

## Corrida diaria con launchd

```bash
deploy/install.sh 7 30 13 0
```

Instala dos tareas (hora local de la Mac):
- **Corrida completa, desde las 07:30:** launchd la prueba cada 30 minutos y corre una sola vez
  por día (`--once-per-day --not-before 07:30`). Baja avisos, los evalúa y genera
  `exports/shortlist_<hoy>.csv`. Tarda entre 5 y 15 minutos y el CSV aparece al final.
- **13:00, rescate:** si LinkedIn cortó búsquedas por 429 a la mañana, las retoma desde donde
  quedaron y pide las descripciones que faltaron (una vez por día, `retry_in_rescue`). Después
  evalúa lo que haya quedado pendiente (por ejemplo, si Gemini estaba saturado a la mañana) y
  agrega lo que pase el umbral al CSV del día.

Si la Mac está **dormida**, la corrida completa arranca poco después de un despertar real (no
en los despertares oscuros de macOS). Si no llegó a correr, el rescate manda un push que avisa
que hoy no hubo búsqueda. Si está **apagada**, ese día no corre: disparala a mano.

```bash
launchctl kickstart gui/$(id -u)/com.jobradar.daily
```

Para desinstalar ambas tareas (no borra la base ni los logs):

```bash
deploy/uninstall.sh
```

## Logs

- `logs/job-radar.log`: una línea JSON por evento; rota a los 5 MB y guarda 5 archivos.
- `logs/runs.jsonl`: una línea por corrida con el resumen (nuevos, duplicados, excluidos,
  evaluados, shortlist, fuentes que fallaron).
- `logs/launchd.err.log`: solo errores que ocurran antes de que arranque el logging.

Una fuente rota (por ejemplo LinkedIn con 429) no corta la corrida: queda anotada en el
resumen y el resto sigue.

## Problemas comunes

- **launchd no corre o `launchd.err.log` dice "Operation not permitted":** macOS protege
  `~/Documents`. Mantené el repo fuera de esa carpeta (`~/code/job-radar`) y volvé a correr
  `deploy/install.sh`.
- **`evaluate` dice "daily quota":** se agotó la cuota del día de todos los modelos de la
  cadena. Se retoma solo en la próxima corrida.
- **LinkedIn devuelve 429:** es normal de a ratos. Lo cortado se retoma en el rescate de las
  13:00. Si también ahí sale cortado varios días seguidos, bajá `results_wanted` o la cantidad
  de queries en `searches.yaml`.
- **Claude Desktop no muestra job-radar:** ver la sección de Claude Desktop (editar con la
  app cerrada). El log del servidor está en `~/Library/Logs/Claude/mcp-server-job-radar.log`.

## Desarrollo

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format .
```

```bash
JOB_RADAR_LIVE=1 uv run pytest -m live -s
```

El último valida el rubric contra 10 casos sintéticos con Gemini real (~10 requests).
Convenciones del proyecto en `CLAUDE.md`; especificación en `SPEC.md`.
