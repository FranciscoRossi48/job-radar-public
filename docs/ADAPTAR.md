# Usarlo para tu búsqueda

job-radar está armado para una búsqueda concreta: Data Engineering / BI en Suiza, con
Países Bajos y Luxemburgo configurados pero en pausa. Todo lo que depende de esa búsqueda vive
en `config/*.yaml` y en el rubric de `prompts/`, así que se adapta sin tocar código. Esta guía
recorre esos archivos en orden.

## 1. Lo que necesitás

- **macOS.** La corrida diaria usa launchd y las alertas usan `osascript`. En Linux funcionan
  los comandos a mano, pero la corrida diaria se arma con cron y la alerta nativa no anda
  (poné `notify_on_error: false`).
- **[uv](https://docs.astral.sh/uv/)** y Python 3.12 (uv lo instala solo).
- **Una clave de Gemini** de <https://aistudio.google.com/apikey>. El plan gratis alcanza para
  probar, pero tiene un tope de requests por día. Para usarlo todos los días conviene el plan
  pago prepago. `uv run job-radar usage` muestra cuánto consumís.

```bash
cp .env.example .env
```

Abrí `.env` y pegá la clave en `GEMINI_API_KEY=`.

## 2. Tu perfil: `config/profile.yaml`

Es lo único que el LLM sabe de vos, así que no pongas el CV entero.

- `summary`: 4 o 5 líneas con rol, años de experiencia, sector, ciudadanía o permiso de trabajo y
  qué estás buscando.
- `languages`: tus idiomas con nivel CEFR (`en: C1`, `de: B1`...). Un idioma que no está en la
  lista cuenta como "ninguno": un aviso que lo pida como requisito queda excluido.
- `residency_countries`: los países donde ya vivís. Con eso, un aviso que pide "must currently
  live in X" deja de excluirte.
- `stack` y `stack_aliases`: tus tecnologías. Cada una que aparezca en el aviso suma puntos.

Los tests usan los idiomas y el stack del ejemplo. Si cambiás el perfil, algunos tests de
scoring pueden fallar: ajustalos o mirá qué espera cada uno.

## 3. El rubric: `prompts/rubric_v2.md`

Arriba hay una línea `Key facts:` con datos del candidato del ejemplo (ciudadanía e idiomas).
Reemplazala por los tuyos. Si cambiás algo de fondo (qué cuenta como requisito de residencia,
cómo se marcan los idiomas), copiá el archivo como `rubric_v3.md` y cambiá `prompt_version` y
`rubric_path` en `config/llm.yaml`. Así cada evaluación queda asociada a la versión del rubric
que la generó.

## 4. Qué y dónde buscar: `config/searches.yaml`

- `queries`: las búsquedas de LinkedIn e Indeed que corren todos los días.
- `rotating_queries` y `rotating_per_day`: las que corren por turnos. LinkedIn responde 429 si
  le pedís demasiado; con 3 búsquedas por día anda.
- `countries`: un bloque por país, con cómo lo nombran LinkedIn (`linkedin_location`) e Indeed
  (`indeed_country`).
- `active_countries`: los países que se buscan hoy.
- `geo`: alias de ciudades por país. Los portales de las empresas devuelven avisos de todo el
  mundo, y solo se guardan los que caen en estos países. Para un país nuevo, agregá su bloque
  en `countries` y en `geo`.
- `swissdevjobs` e `iamexpat`: portales de Suiza y de Países Bajos. Apagalos (`enabled: false`)
  si buscás en otro lado.

## 5. Empresas: `config/companies.yaml`

Cada entrada es `{name, ats, token, country}`. El token sale de la URL de la página de empleos;
los comentarios del archivo explican cómo encontrarlo para cada ATS. Antes de sumar una empresa,
probala:

```bash
uv run job-radar ingest -s greenhouse:getyourguide
```

Ojo con YAML: `name: On` se lee como booleano y tiene que ir entre comillas.

## 6. Filtros y puntaje

- `config/prefilter.yaml`: filtro barato antes del LLM.
  - `title.exclude_words`: títulos que se descartan (intern, junior...).
  - `title.include_any`: palabras que tiene que tener el título para que el aviso pase.
  - `description.exclude_langs`: avisos redactados en esos idiomas. Hoy son francés y
    neerlandés: si hablás francés, sacalo de ahí.
- `config/scoring.yaml`: pesos del puntaje 0–100.
  - `threshold`: desde qué puntaje un aviso entra al CSV.
  - `bonus.country`: suma por país.
  - `bonus.lang`: idiomas que suman si el aviso los pide.
  - `penalties.location_priors`: Romandía, Ticino y Luxemburgo restan porque ahí suelen pedir
    francés o italiano. Son reglas de Suiza: cambialas o borralas si buscás en otro país.

Después de tocar estos archivos no hace falta volver a llamar al LLM:

```bash
uv run job-radar reprefilter && uv run job-radar dedup
```

```bash
uv run job-radar rescore
```

`reprefilter` (seguido de `dedup`) es para cambios en `prefilter.yaml`; `rescore` es para
cambios en `scoring.yaml`.

## 7. Primera corrida

```bash
uv run pytest
```

```bash
uv run job-radar ingest --limit 10
```

```bash
uv run job-radar evaluate -n 5
```

```bash
uv run job-radar export --all
```

Revisá `exports/ranking_all.csv`. Si los puntajes no te cierran, ajustá `scoring.yaml` y corré
`rescore`.

## 8. Opcional

- **Corrida diaria:** `deploy/install.sh`. Más detalles en el README, sección
  "Corrida diaria con launchd".
- **Copia en Google Drive y respaldo de la base:** poné una carpeta en
  `app.yaml: exports_mirror_dirs` y activá `backup.enabled`.
- **Push al celular:** instalá la app ntfy, suscribite a un tema largo y al azar, ponelo en
  `.env` (`NTFY_TOPIC=`) y activá `ntfy.enabled` en `app.yaml`. El tema funciona como una
  contraseña: cualquiera que lo sepa lee tus avisos.
- **Segunda opinión con Claude Desktop:** `uv run job-radar mcp-config` (ver la sección MCP del
  README).

## Uso responsable

Usá solo endpoints públicos: nada de cookies ni credenciales de LinkedIn ni de otros sitios.
Respetá el `robots.txt` y los términos de cada sitio, y mantené las pausas que trae la
configuración. La idea es una búsqueda personal, no un scraper masivo.
