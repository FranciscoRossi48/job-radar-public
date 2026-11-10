# Proyecto: job-radar — buscador y filtro de ofertas laborales (Suiza + Países Bajos + Luxemburgo)

Antes de escribir código, entrá en modo plan: proponé la estructura del repo y el plan por fases, y esperá mi OK. Implementá fase por fase y frená al final de cada una para que pruebe. Creá un `CLAUDE.md` con las convenciones del proyecto y mantenelo actualizado.

## Contexto
Soy Data Engineer / BI Lead con más de 5 años en banca regulada y farma. Busco trabajo en Suiza, Países Bajos y Luxemburgo. Soy ciudadano UE y no necesito sponsorship; estoy por mudarme a Suiza. Hablo inglés C1 y alemán B1. No hablo francés ni neerlandés.
Problema: muchos avisos en inglés exigen alemán, francés o neerlandés B2/C1, o residencia actual en el país. Quiero ingesta automática, filtro barato determinístico, evaluación con LLM que devuelva features estructuradas, y score calculado en Python.

## Restricciones
- Corre 100% local en macOS, desde mi IP residencial. Costo $0.
- Python 3.12 gestionado con `uv`. Usar `pyproject.toml`, `ruff` y `pytest`.
- Nunca usar cookies ni credenciales de mi cuenta de LinkedIn. Solo endpoints públicos.
- Toda la configuración (queries, ubicaciones, pesos, umbrales, perfil) va en YAML dentro de `config/`. Nada hardcodeado.
- Secretos en `.env` (por ejemplo `GEMINI_API_KEY`). Agregar `.gitignore` (incluir `.env`, `jobs.db`, `logs/`, `exports/`).

## Arquitectura

### 1. Ingesta (`ingest/`)
- **JobSpy:**
  - Sitios: `linkedin` (con `linkedin_fetch_description=True`), `indeed` (con `country_indeed` para switzerland/netherlands/luxembourg) y `google`.
  - `hours_old=24`, pocas queries por corrida, todas configurables.
  - Ante un 429: backoff exponencial con jitter. Si persiste, abortar la fuente para esa corrida sin romper las demás.
- **SwissDevJobs:** su API JSON pública.
- **ATS directos:** un adapter por plataforma, con interfaz común.
  - Greenhouse: `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
  - Lever: `api.lever.co/v0/postings/{x}?mode=json`
  - Ashby: `api.ashbyhq.com/posting-api/job-board/{x}`
  - SmartRecruiters
  - Workday: endpoint `cxs`
  - Recruitee y Personio (agregados)
  - La lista de empresas va en `config/companies.yaml` como `{nombre, ats, token, país}`. Dejala con 3-4 ejemplos comentados; la completo yo.
- **IamExpat.nl:** scraping liviano y respetuoso, si es viable. Si no, dejá el adapter como stub documentado.
- Cada fuente es un adapter que devuelve un modelo Pydantic `RawJob` normalizado.

### 2. Pre-filtro determinístico (`prefilter/`)
- **Regex de exclusión solo sobre el título**, con `\b`: intern, internship, praktikum, praktikant, werkstudent, trainee, stagiair, junior.
  - "stage" solo como palabra completa.
  - No excluir títulos combinados tipo "Junior/Senior" o "Medior/Senior".
  - Tests unitarios para estos casos borde (staging, backstage, Junior/Senior).
- **Idioma de la descripción** con `lingua-language-detector`. Guardar `desc_lang`.
- **Snippets de evidencia de idioma:** regex multilingüe (fluent/fließend/fliessend/verhandlungssicher/courant/vloeiend, German/Deutsch/Dutch/Nederlands/French/Français, C1/C2/native/Muttersprache) que guarde cada match con ±150 caracteres de contexto.
- **Limpieza de descripción:** sacar boilerplate (beneficios, "about us", EEO) y generar `desc_condensed` de ~2.000 caracteres máximo, priorizando requisitos y qualifications.

### 3. Persistencia (SQLite, `jobs.db`, modo WAL)
- `jobs`:
  - `id` = `source:job_id`
  - `source`, `url`, `title`, `company`, `location`, `country`
  - `description_raw`, `desc_condensed`, `desc_lang`, `lang_snippets` (JSON)
  - `first_seen`, `last_seen`, `simhash`
  - `status`: `pending | evaluated | excluded_prefilter | duplicate | stale`
  - `claimed_at`, `claimed_by`: lease independiente del status (un job `evaluated` se puede reclamar sin perder su estado)
- **Deduplicación** en tres niveles: `id`, (empresa + título normalizado + país) y similitud simhash de la descripción, con umbral configurable.
- **Leases:** un job en `claimed` hace más de 30 minutos vuelve a `pending`.
- Marcar `stale` los avisos sin `last_seen` en los últimos N días.
- `evaluations`:
  - `job_id`, `evaluator` (gemini/claude), `model`, `prompt_version`, `created_at`
  - Las features (ver abajo), `rationale` y `score_computed`
  - Permitir varias evaluaciones por job.

### 4. Schema de evaluación (Pydantic, compartido por las dos vías)
- `lang_required`: lista de `{lang: de|fr|nl|it|lb|other, level: none|nice|required, cefr: A1..C2|null}`
- `residency_hard_req`: bool. IMPORTANTE: "valid work permit / right to work in CH or the EU" NO cuenta, porque como ciudadano UE accedo al permiso con el contrato. Solo cuenta "must currently live in X" o "local candidates only".
- `onsite_only`: bool
- `seniority`: `junior | mid | senior | lead | unknown`
- `role_family`: `DE | AE | BI | DA | DS | other`
- `stack_match`: lista de tecnologías de mi perfil encontradas en el aviso
- `red_flags`: lista corta
- `rationale`: 25 palabras como máximo
- El rubric vive en `prompts/rubric_vN.md` (hoy v2) y se versiona.

### 5. Scoring determinístico (`scoring.py`, pesos en `config/scoring.yaml`)
- **Exclusión dura:**
  - Cualquier idioma distinto de inglés con `level=required` y nivel CEFR mayor a mi nivel (se compara contra `config/profile.yaml`: hoy alemán por encima de B1; francés/neerlandés cualquiera).
  - `residency_hard_req=true`
  - `seniority=junior`
- **Penalizaciones:**
  - Idioma `nice`.
  - Ubicación con alto prior de francés (Ginebra, Lausana, Vaud, Neuchâtel, Friburgo), configurable.
  - Descripción escrita en de/fr/nl.
- **Bonus:**
  - Overlap de `stack_match` con mi perfil.
  - Seniority senior/lead.
- **Resultado:** 0-100, recalculable sin reevaluar. Comando `rescore` para reponderar con pesos nuevos.

### 6. Vía Gemini, desatendida (`evaluate_gemini.py`)
- SDK `google-genai`, modelo `gemini-2.5-flash` configurable, structured output con el schema Pydantic.
- Rate limiter configurable: RPM y RPD en config, default conservador de 10 RPM.
- Reintentos ante RESOURCE_EXHAUSTED.
- Mandar solo `desc_condensed` + `lang_snippets` + resumen corto del perfil (`config/profile.yaml`). Nunca mi CV completo.
- Al final, exportar CSV del día en `exports/` con `score >= umbral` (`scoring.yaml: threshold`, hoy 70), ordenado.

### 7. Vía Claude, interactiva (`mcp_server.py`, FastMCP, stdio)
- **Herramientas:**
  - `get_batch(n=10, mode="shortlist"|"pending")` devuelve JSON compacto (id, título, empresa, ubicación, desc_condensed, lang_snippets, score y features de Gemini si existen) y marca los jobs `claimed`.
    - `shortlist`: top del día más dudosos con score de Gemini entre 50 y 80. Es el uso principal.
    - `pending`: triage completo, como fallback.
  - `save_evaluations(items: list[Evaluation])` guarda el lote en una sola llamada. Valida con Pydantic y devuelve qué ítems fallaron y por qué, sin abortar el lote.
  - `get_job_detail(id)` devuelve la descripción completa.
  - `get_shortlist(min_score=<threshold>, days=1)` (por defecto, el umbral de `scoring.yaml`)
  - `get_stats()`: conteos por estado y fuente.
- El rubric se expone como `@mcp.prompt`.
- Imprimir el snippet exacto para `~/Library/Application Support/Claude/claude_desktop_config.json`, usando `uv run` con ruta absoluta.

### 8. Orquestación
- CLI con `typer`: `ingest`, `evaluate`, `rescore`, `export`, `stats`, `run-daily` (ingest, evaluate y export en secuencia).
- Plist de `launchd` en `deploy/` que corra `run-daily` una vez al día, con logs rotados en `logs/`. Instrucciones para instalarlo y desinstalarlo.
- Logging estructurado con un resumen por corrida: nuevos, duplicados, excluidos por prefiltro, evaluados, shortlist.

## Fases (frenar al final de cada una)
1. Esqueleto, DB, modelos Pydantic, pre-filtro con tests, adapter JobSpy. Validación: una corrida real chica (1 query por país).
2. Adapters SwissDevJobs y ATS. Deduplicación con tests.
3. Schema, rubric, scoring con tests (incluí los casos de "work permit" vs "must reside"), vía Gemini.
4. Servidor MCP más config de Claude Desktop.
5. CLI, launchd, README con setup completo.

## Calidad
- Tests con fixtures de avisos reales anonimizados. Armame 10 casos sintéticos realistas cubriendo los bordes de idioma y residencia.
- Ninguna fuente rota debe tirar abajo la corrida completa.
- README conciso: setup, comandos, cómo agregar empresas y cómo ajustar pesos.

## Decisiones agregadas (acordadas al planificar)
- Include-list de títulos en `config/prefilter.yaml` (solo pasan títulos de data/BI/analytics).
- Descripciones redactadas enteras en fr/nl se excluyen en el prefiltro; en alemán solo penalizan.
- `profile.residency_countries`: "must currently live in X" no excluye si X está en la lista (agregar CH tras mudarse y correr `rescore`).
- Prior de francés para Luxemburgo y de italiano para Ticino, configurables.
- `evaluations.score_breakdown` (JSON) con cada componente del score.
- jobs.ch / jobup.ch fuera de alcance (sus términos prohíben scraping).
- SDK MCP 2.x: `FastMCP` se llama `MCPServer`; `get_batch` incluye el rubric y el perfil en cada lote.
- Gemini: `gemini-2.5-flash` no está disponible para cuentas nuevas; se usa una cadena de modelos (`config/llm.yaml`) porque el free tier de `gemini-3.8-flash` permite 20 requests/día.
- Pre-filtro: avisos redactados en alemán que piden alemán por encima de B1 se excluyen sin LLM.
