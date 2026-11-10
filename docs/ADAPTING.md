# Adapting it to your own search

job-radar is set up for one specific search: Data Engineering / BI in Switzerland, with the
Netherlands and Luxembourg configured but paused. Everything that depends on that search lives
in `config/*.yaml` and in the rubric under `prompts/`, so you can adapt it without touching the
code. This guide walks through those files in order.

## 1. What you need

- **macOS.** The daily run uses launchd and the alerts use `osascript`. On Linux the commands
  work by hand, but you'd schedule the daily run with cron and the native alert won't work
  (set `notify_on_error: false`).
- **[uv](https://docs.astral.sh/uv/)** and Python 3.12 (uv installs it for you).
- **A Gemini API key** from <https://aistudio.google.com/apikey>. The free plan is enough to try
  it out, but it caps requests per day. For daily use, the prepaid plan works better.
  `uv run job-radar usage` shows how much you're using.

```bash
cp .env.example .env
```

Open `.env` and paste the key into `GEMINI_API_KEY=`.

## 2. Your profile: `config/profile.yaml`

This is all the LLM knows about you, so don't paste your whole CV.

- `summary`: 4 or 5 lines covering your role, years of experience, industry, citizenship or work
  permit, and what you're looking for.
- `languages`: your languages with their CEFR level (`en: C1`, `de: B1`...). A language that
  isn't listed counts as "none", so an ad that requires it is excluded.
- `residency_countries`: the countries you already live in. With this set, an ad that says
  "must currently live in X" no longer excludes you.
- `stack` and `stack_aliases`: your technologies. Each one that appears in an ad adds points.

The tests use the languages and stack from the sample profile. If you change the profile, some
scoring tests may fail: adjust them, or check what each one expects.

## 3. The rubric: `prompts/rubric_v2.md`

Near the top there's a `Key facts:` line with the sample candidate's citizenship and languages.
Replace it with yours. If you change something substantial (what counts as a residency
requirement, how languages are tagged), copy the file to `rubric_v3.md` and update
`prompt_version` and `rubric_path` in `config/llm.yaml`. That way each evaluation stays linked
to the rubric version that produced it.

## 4. What and where to search: `config/searches.yaml`

- `queries`: the LinkedIn and Indeed searches that run every day.
- `rotating_queries` and `rotating_per_day`: the searches that take turns. LinkedIn responds
  with 429 if you ask too much; about 3 searches per day works.
- `countries`: one block per country, with the names LinkedIn (`linkedin_location`) and Indeed
  (`indeed_country`) use for it.
- `active_countries`: the countries being searched right now.
- `geo`: city aliases per country. Company career sites return ads from all over the world, and
  only those that match these countries are stored. For a new country, add its block to both
  `countries` and `geo`.
- `swissdevjobs` and `iamexpat`: job boards for Switzerland and the Netherlands. Turn them off
  (`enabled: false`) if you're searching elsewhere.

## 5. Companies: `config/companies.yaml`

Each entry is `{name, ats, token, country}`. The token comes from the URL of the careers page,
and the comments in the file explain how to find it for each ATS. Before adding a company, test
it:

```bash
uv run job-radar ingest -s greenhouse:getyourguide
```

Careful with YAML: `name: On` is read as a boolean and has to be quoted.

## 6. Filters and scoring

- `config/prefilter.yaml`: the cheap filter that runs before the LLM.
  - `title.exclude_words`: titles that are dropped (intern, junior...).
  - `title.include_any`: words a title must contain to pass.
  - `description.exclude_langs`: ads written in these languages are dropped. Today that's
    French and Dutch; if you speak French, remove it.
- `config/scoring.yaml`: the weights behind the 0–100 score.
  - `threshold`: the minimum score for an ad to make it into the CSV.
  - `bonus.country`: extra points per country.
  - `bonus.lang`: languages that add points when an ad asks for them.
  - `penalties.location_priors`: Romandie, Ticino and Luxembourg subtract points because ads
    there often ask for French or Italian. These rules are Swiss-specific: change or remove
    them if you search elsewhere.

You don't need to call the LLM again after editing these files. After changing
`prefilter.yaml`, run:

```bash
uv run job-radar reprefilter && uv run job-radar dedup
```

After changing `scoring.yaml`, run:

```bash
uv run job-radar rescore
```

## 7. First run

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

Check `exports/ranking_all.csv`. If the scores don't look right, adjust `scoring.yaml` and run
`rescore`.

## 8. Optional

- **Daily run:** `deploy/install.sh`. More details in the README, section "Daily run with
  launchd".
- **Copy to Google Drive and database backup:** add a folder to
  `app.yaml: exports_mirror_dirs` and turn on `backup.enabled`.
- **Push notifications to your phone:**
  1. Install the ntfy app and subscribe to a long, random topic.
  2. Put the topic in `.env` (`NTFY_TOPIC=`) and turn on `ntfy.enabled` in `app.yaml`.

  The topic works like a password: anyone who knows it can read your notifications.
- **Second opinion with Claude Desktop:** `uv run job-radar mcp-config` (see the MCP section of
  the README).

## Responsible use

Use public endpoints only: no cookies or credentials for LinkedIn or any other site. Respect
each site's `robots.txt` and terms of use, and keep the pauses that ship with the
configuration. This is meant for a personal job search, not mass scraping.
