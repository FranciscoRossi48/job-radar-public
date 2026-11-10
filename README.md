# job-radar

A local pipeline that finds and filters Data Engineering / BI job ads in **Switzerland, the
Netherlands and Luxembourg**. It runs on your Mac, from your own IP. There is no server; the
only cost is Gemini API usage.

> **Personal project.** I built it for my own job search and share it as an example. Respect
> each site's terms of use and `robots.txt`, use public endpoints only (never cookies or
> credentials) and keep request volume low. `config/profile.yaml` is a sample profile: replace
> it with yours. MIT license (see `LICENSE`).
>
> Code comments, YAML comments, `SPEC.md` and `CLAUDE.md` are in Spanish. Identifiers and this
> README are in English.

```
sources ──> prefilter ──> dedup ──> SQLite ──> Gemini (features) ──> score in Python ──> CSV
(JobSpy, SwissDevJobs,  (title,    (id, company+title,               │
 IamExpat, ATS)          language)  simhash)                          └─> Claude Desktop (MCP):
                                                                          second opinion
```

- **Ingestion:**
  - LinkedIn and Indeed via JobSpy (public endpoints only, no cookies). Fixed queries cover the
    time since the last run; rotating queries take turns.
  - SwissDevJobs and IamExpat.
  - The career sites of the companies you choose: Greenhouse, Lever, Ashby, SmartRecruiters,
    Workday, Recruitee, Personio, SuccessFactors and BrassRing.
  - Recruiting agencies that publish through Matador Jobs on WordPress, or through an RSS feed.
- **Cheap prefilter:** before any LLM call, drops ads by title (intern, junior,
  Werkstudent...) and by ad language (French/Dutch, or German ads that ask for C1).
- **Gemini only extracts facts:** required languages and level, residency requirements,
  seniority, role and tech stack. The **0–100 score is computed in Python** with the weights in
  `config/scoring.yaml`, so you can re-rank everything without calling the LLM again.

## Setup

Requirements: macOS and [uv](https://docs.astral.sh/uv/). If this is your first time and your
search is different from mine, follow [docs/ADAPTING.md](docs/ADAPTING.md). It lists which
files to change for your profile, countries and companies.

Clone it outside `~/Documents` (macOS doesn't let launchd read there), for example into
`~/code`:

```bash
git clone https://github.com/FranciscoRossi48/job-radar-public.git ~/code/job-radar
```

```bash
cd ~/code/job-radar && uv sync
```

```bash
cp .env.example .env
```

Open `.env` and paste your Gemini key into `GEMINI_API_KEY=`. You can create one for free at
<https://aistudio.google.com/apikey>. This file is not committed to git.

Review your profile in `config/profile.yaml`: summary, languages, stack and the countries you
already live in.

Check that everything works:

```bash
uv run pytest
```

## Commands

| Command | What it does |
|---|---|
| `uv run job-radar run-daily` | Full run: ingest → evaluate → export. This is what launchd runs. |
| `uv run job-radar ingest` | Ingestion only. Options: `-s linkedin` / `-s ats` / `-s greenhouse:token`, `-c CH`, `-q "data engineer"`, `--limit 10`. |
| `uv run job-radar evaluate -n 20` | Evaluates up to 20 pending ads with Gemini. |
| `uv run job-radar export` | **New**: ads with score ≥ threshold that haven't appeared in any report yet. They're appended to `exports/shortlist_<today>.csv` and marked as exported. |
| `uv run job-radar export --all` | **History**: full ranking of everything that passed, in `exports/ranking_all.csv` (marks nothing). |
| `uv run job-radar export --day 2026-10-15` | What was evaluated that day, in `exports/evaluated_<day>.csv`. `--min-score 60` changes the threshold. |
| `uv run job-radar dedup` | Re-runs deduplication over the database (useful after changing dedup thresholds). |
| `uv run job-radar rescore` | Recomputes every score with the current weights (no LLM calls). |
| `uv run job-radar reprefilter` | Re-applies the prefilter to stored ads (after changing `prefilter.yaml`). |
| `uv run job-radar backup` | Backs up `jobs.db`, compressed, into `<mirror folder>/backups` (`run-daily` does it on every run when enabled). Keeps 7 days. To restore: `gunzip -c jobs_DATE.db.gz > jobs.db`. |
| `uv run job-radar stats` | Counts by status, source and evaluator. |
| `uv run job-radar decide <id> applied\|discarded\|clear --note "..."` | Records a decision by hand (Claude can also do it with `set_decisions`). |
| `uv run job-radar applications` | The jobs you applied to. |
| `uv run job-radar usage` | Gemini requests and tokens per day and model (to keep spending in check). |
| `uv run job-radar mcp-config` | Configuration block for Claude Desktop. |

## Daily use

1. launchd runs `run-daily` every morning (see below).
2. Open `exports/shortlist_YYYY-MM-DD.csv`. It only has ads that are **new to you** (never in an
   earlier CSV) with score ≥ threshold, sorted. For the cumulative ranking, use `export --all`.
   Each CSV can also be copied to other folders, such as Google Drive for desktop, so you can
   open it on your phone. Set them in `exports_mirror_dirs` in `config/app.yaml`.
3. For borderline ads, ask **Claude Desktop** (Chat tab):
   *"Use job-radar: get a batch in shortlist mode, evaluate it and save the evaluations."*
   Claude reviews the ads Gemini scored between 50 and 80, and its evaluation becomes the
   current one.

## Claude Desktop (MCP)

```bash
uv run job-radar mcp-config
```

Paste the printed block into `~/Library/Application Support/Claude/claude_desktop_config.json`
**with Claude Desktop closed (Cmd+Q)**. The app rewrites that file when it quits and drops any
change made while it was running. When you reopen it, job-radar shows up among the connectors.

Tools:
- `get_batch`: reserves a batch for 30 minutes, with the full ad and the rubric. Two modes:
  `shortlist` (borderline ads, or a range with `min_score`/`max_score`) or `pending` (not yet
  evaluated).
- `save_evaluations`: stores Claude's evaluations; the server computes the score.
- `get_shortlist`, `get_job_detail`, `get_stats`.
- `set_decisions`: records that you applied to (`applied`) or discarded (`discarded`) a job.
  Decided jobs no longer show up in batches, the shortlist or the daily CSV.
- `get_applications`: your applications, with date and note.
- `rubric` prompt.

## Adding companies

Edit `config/companies.yaml`. Each company is one line:

```yaml
companies:
  - {name: GetYourGuide, ats: greenhouse, token: getyourguide, country: CH}
  - {name: Roche, ats: workday, token: roche, country: CH, extra: {host: wd3, site: roche-ext}}
```

The `token` comes from the URL of the company's careers page; the comments in the file explain
where to look for each ATS. Test a new company on its own:

```bash
uv run job-radar ingest -s greenhouse:getyourguide
```

Only ads from the configured countries are stored (the city aliases are in
`config/searches.yaml: geo`).

## Tuning weights and filters

| File | What it controls |
|---|---|
| `config/scoring.yaml` | Base score, hard exclusions, penalties (language, French/Italian-speaking regions, on-site), bonuses (stack, seniority, role, languages), CSV threshold. |
| `config/profile.yaml` | Your languages (CEFR), stack (plus `stack_aliases`) and `residency_countries`. A stack item adds points if the LLM detects it **or** if it appears literally in the ad. |
| `config/prefilter.yaml` | Excluded title words, relevant titles, excluded ad languages, patterns for high-level German requirements. |
| `config/searches.yaml` | Fixed and rotating queries, the since-last-run window, active countries, sources and pauses between requests. |
| `config/llm.yaml` | The Gemini model chain and its limits. |

After changing `scoring.yaml` or `profile.yaml`, run `uv run job-radar rescore`. After
changing `prefilter.yaml`, run `uv run job-radar reprefilter` and then `dedup`. This reopens
ads that were dropped by title and are now relevant.

If you move to one of the target countries, add it to `residency_countries` in `profile.yaml`
and run `rescore`. Ads that require you to "currently live in" that country stop being
excluded.

## Gemini: plans and costs

The **Google AI Pro** subscription (the Gemini app) does **not** include the API: they're
separate products. job-radar uses the API with the key in `.env`.

- **Free plan:** Flash models are capped at a small number of requests per day. Under heavy
  demand, Google rejects free accounts first (error 503).
- **Paid plan (prepaid):** no daily cap, and priority under load. Each ad uses about 2,000
  tokens. Track usage with `uv run job-radar usage` and the real cost at
  <https://aistudio.google.com/usage>. If the balance hits $0, evaluation stops and the ads stay
  pending until you top up.

`config/llm.yaml` defines the model chain (the best one first, Lite models as fallback) and a
daily cap per model (`rpd`), which also caps spending. The LLM only receives the condensed
description and a short summary of your profile, never your CV.

## Daily run with launchd

```bash
deploy/install.sh 7 30 13 0
```

This installs two jobs (in the Mac's local time):
- **Full run, from 07:30:** launchd tries every 30 minutes and it runs once per day
  (`--once-per-day --not-before 07:30`). It fetches ads, evaluates them and writes
  `exports/shortlist_<today>.csv`. It takes 5 to 15 minutes, and the CSV appears at the end.
- **13:00, catch-up:**
  - If LinkedIn cut searches with a 429 in the morning, it resumes them where they stopped
    and fetches the missing descriptions. This happens once per day (`retry_in_rescue`).
  - Then it evaluates whatever is still pending (for example, if Gemini was overloaded in the
    morning) and adds what passes the threshold to the day's CSV.

If the Mac is **asleep**, the full run starts shortly after a real wake-up (not during macOS
dark wakes). If it hasn't run by the catch-up, the catch-up sends a push saying there was no
search today. If the Mac is **off**, nothing runs that day; trigger it by hand:

```bash
launchctl kickstart gui/$(id -u)/com.jobradar.daily
```

To uninstall both jobs (this doesn't delete the database or the logs):

```bash
deploy/uninstall.sh
```

## Logs

- `logs/job-radar.log`: one JSON line per event; rotates at 5 MB and keeps 5 files.
- `logs/runs.jsonl`: one line per run with its summary (new, duplicates, excluded, evaluated,
  shortlist, failed sources).
- `logs/launchd.err.log`: only errors that happen before logging starts.

A broken source (for example LinkedIn with a 429) doesn't stop the run: it's recorded in the
summary and the other steps go on.

## Troubleshooting

- **launchd doesn't run, or `launchd.err.log` says "Operation not permitted":** macOS protects
  `~/Documents`. Keep the repo outside that folder (for example `~/code/job-radar`) and run
  `deploy/install.sh` again.
- **`evaluate` says "daily quota":** every model in the chain used up its daily quota. It
  resumes on its own in the next run.
- **LinkedIn returns 429:** this happens from time to time. Whatever was cut is resumed in the
  13:00 catch-up. If the catch-up is also cut for several days in a row, lower `results_wanted`
  or the number of queries in `searches.yaml`.
- **Claude Desktop doesn't show job-radar:** see the Claude Desktop section (edit the config
  with the app closed). The server log is at
  `~/Library/Logs/Claude/mcp-server-job-radar.log`.

## Development

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format .
```

```bash
JOB_RADAR_LIVE=1 uv run pytest -m live -s
```

The last one checks the rubric against 10 synthetic cases with the real Gemini API (about 10
requests). Project conventions are in `CLAUDE.md`, and the specification is in `SPEC.md`.
