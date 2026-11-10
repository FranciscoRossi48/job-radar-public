"""Vía Claude, interactiva: servidor MCP (stdio) para Claude Desktop.

Las funciones `tool_*` contienen la lógica y reciben (cfg, conn); el servidor solo las
envuelve. Así se testean sin levantar el protocolo.

Nota: en el SDK `mcp` 2.x, FastMCP pasó a llamarse MCPServer.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import ValidationError

from job_radar import db
from job_radar.config import Config, load_config
from job_radar.evaluate_gemini import profile_brief
from job_radar.models import Evaluation
from job_radar.scoring import JobContext, score

log = logging.getLogger(__name__)

WORKFLOW = """
## How to work with the job-radar tools

1. Call `get_batch` (mode "shortlist" by default) to receive and lock a batch of jobs.
2. For each job, extract the features exactly as the rubric above defines them. If the
   condensed description is not enough, call `get_job_detail` for the full text.
3. Call `save_evaluations` ONCE with one item per job (include `job_id`). The server computes
   the score; you do not. The response lists which items failed validation and why: fix only
   those and call `save_evaluations` again with them.
4. Then summarize for the user, in Spanish: the jobs worth applying to and why, and where you
   disagree with Gemini's previous evaluation (if any).
"""


def _features_summary(row: sqlite3.Row) -> dict | None:
    if row is None or row["e_features"] is None:
        return None
    return {
        "evaluator": row["e_evaluator"],
        "model": row["e_model"],
        "score": row["e_score"],
        "features": json.loads(row["e_features"]),
    }


def _compact(job: sqlite3.Row, current: sqlite3.Row | None) -> dict[str, Any]:
    snippets = json.loads(job["lang_snippets"] or "[]")
    return {
        "id": job["id"],
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "country": job["country"],
        "desc_lang": job["desc_lang"],
        "desc_condensed": job["desc_condensed"],
        "lang_snippets": [f"[{s['kind']}] {s['text']}" for s in snippets],
        "previous_evaluation": _features_summary(current),
    }


def _current_by_id(conn: sqlite3.Connection, cfg: Config, ids: list[str]) -> dict[str, sqlite3.Row]:
    rows = db.current_evaluations(conn, cfg.scoring.evaluator_priority, job_ids=ids)
    return {r["id"]: r for r in rows}


def _active_countries(cfg: Config) -> list[str]:
    """Países activos: los avisos viejos de un país en pausa no se ofrecen ni se reevalúan."""
    return cfg.searches.active_codes


# --- Lógica de las herramientas -----------------------------------------------------------


def tool_get_batch(
    cfg: Config,
    conn: sqlite3.Connection,
    n: int = 10,
    mode: str = "shortlist",
    min_score: float | None = None,
    max_score: float | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    m = cfg.app.mcp
    n = max(1, min(n, m.batch_max))
    if mode == "shortlist":
        lo, hi = m.shortlist_score_range
        custom = min_score is not None or max_score is not None
        ids = db.claim_shortlist(
            conn,
            n,
            m.evaluator,
            cfg.app.lease_minutes,
            lo=lo if min_score is None else min_score,
            hi=hi if max_score is None else max_score,
            days=m.shortlist_days if days is None else max(1, days),
            # Rango por defecto: los más dudosos primero. Rango propio: mejor score primero.
            pivot=None if custom else cfg.scoring.threshold,
            priority=cfg.scoring.evaluator_priority,
            countries=_active_countries(cfg),
        )
    elif mode == "pending":
        ids = db.claim_pending(
            conn, n, m.evaluator, cfg.app.lease_minutes, countries=_active_countries(cfg)
        )
    else:
        raise ValueError("mode must be 'shortlist' or 'pending'")
    current = _current_by_id(conn, cfg, ids)
    jobs = [_compact(j, current.get(j["id"])) for j in db.get_jobs(conn, ids)]
    return {
        "mode": mode,
        "count": len(jobs),
        "lease_minutes": cfg.app.lease_minutes,
        "jobs": jobs,
        "note": "Empty batch: nothing matches this mode right now." if not jobs else None,
        # Va en cada lote: en Claude Desktop el prompt "rubric" hay que elegirlo a mano y
        # sin esto Claude evalúa con lo que recuerde del usuario (ej. otro nivel de alemán).
        "evaluation_guide": rubric_prompt(cfg) if jobs else None,
    }


def tool_save_evaluations(
    cfg: Config, conn: sqlite3.Connection, items: list[dict[str, Any]], model: str | None = None
) -> dict[str, Any]:
    saved, failed = [], []
    for i, raw in enumerate(items):
        job_id = raw.get("job_id") if isinstance(raw, dict) else None
        try:
            ev = Evaluation.model_validate(raw)
        except ValidationError as e:
            errs = [f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()]
            failed.append({"index": i, "job_id": job_id, "errors": errs})
            continue
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (ev.job_id,)).fetchone()
        if job is None:
            failed.append({"index": i, "job_id": ev.job_id, "errors": ["job_id: unknown job"]})
            continue
        ctx = JobContext.from_row(job)
        result = score(ev, ctx, cfg.profile, cfg.scoring)
        db.insert_evaluation(
            conn,
            job_id=ev.job_id,
            evaluator=cfg.app.mcp.evaluator,
            model=model,
            prompt_version=cfg.llm.prompt_version,
            features=ev,
            result=result,
        )
        saved.append(
            {
                "job_id": ev.job_id,
                "score": result.score,
                "excluded": result.excluded,
                "breakdown": result.breakdown,
            }
        )
    conn.commit()
    return {"saved": len(saved), "failed": failed, "results": saved}


def tool_get_job_detail(cfg: Config, conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (id,)).fetchone()
    if job is None:
        raise ValueError(f"unknown job id: {id}")
    evals = [
        {
            "evaluator": e["evaluator"],
            "model": e["model"],
            "prompt_version": e["prompt_version"],
            "created_at": e["created_at"],
            "score": e["score_computed"],
            "features": json.loads(e["features"]),
            "breakdown": json.loads(e["score_breakdown"] or "[]"),
        }
        for e in db.evaluations_for(conn, id)
    ]
    keys = [
        "id",
        "source",
        "url",
        "title",
        "company",
        "location",
        "country",
        "posted_at",
        "first_seen",
        "last_seen",
        "status",
        "exclude_reason",
        "duplicate_of",
        "desc_lang",
    ]
    return {
        **{k: job[k] for k in keys},
        "description": job["description_raw"],
        "evaluations": evals,
    }


def tool_get_shortlist(
    cfg: Config,
    conn: sqlite3.Connection,
    min_score: float | None = None,
    days: int | None = 1,
    only_new: bool = False,
    include_decided: bool = False,
) -> dict[str, Any]:
    """`days=None`: histórico completo. `only_new`: solo jobs que nunca salieron en un CSV.
    Por defecto no incluye jobs ya aplicados o descartados."""
    min_score = cfg.scoring.threshold if min_score is None else min_score
    rows = db.current_evaluations(
        conn,
        cfg.scoring.evaluator_priority,
        min_score=min_score,
        since_days=days,
        unexported_only=only_new,
        include_decided=include_decided,
        countries=_active_countries(cfg),
    )
    jobs = [
        {
            "id": r["id"],
            "score": r["e_score"],
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "url": r["url"],
            "evaluator": r["e_evaluator"],
            "rationale": r["e_rationale"],
            "already_reported": r["exported_at"] is not None,
            "decision": r["decision"],
        }
        for r in rows
    ]
    return {
        "min_score": min_score,
        "days": days,
        "only_new": only_new,
        "count": len(jobs),
        "jobs": jobs,
    }


def tool_set_decisions(
    cfg: Config, conn: sqlite3.Connection, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """items: [{job_id, decision: applied|discarded|null, note?}]. Valida ítem por ítem."""
    saved, failed = [], []
    for i, it in enumerate(items):
        job_id = it.get("job_id") if isinstance(it, dict) else None
        decision = it.get("decision") if isinstance(it, dict) else None
        if not job_id:
            failed.append({"index": i, "job_id": None, "error": "job_id is required"})
            continue
        if decision is not None and decision not in db.DECISIONS:
            failed.append(
                {
                    "index": i,
                    "job_id": job_id,
                    "error": "decision must be 'applied', 'discarded' or null",
                }
            )
            continue
        if not db.set_decision(conn, job_id, decision, it.get("note")):
            failed.append({"index": i, "job_id": job_id, "error": "unknown job"})
            continue
        saved.append({"job_id": job_id, "decision": decision})
    conn.commit()
    return {"saved": len(saved), "failed": failed, "results": saved}


def tool_get_applications(cfg: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    rows = db.decided_jobs(conn, "applied")
    apps = [
        {
            "id": r["id"],
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "url": r["url"],
            "applied_at": r["decided_at"],
            "note": r["decision_note"],
            "score": r["last_score"],
        }
        for r in rows
    ]
    return {"count": len(apps), "applications": apps}


def tool_get_stats(cfg: Config, conn: sqlite3.Connection) -> dict[str, Any]:
    by_eval = dict(
        conn.execute("SELECT evaluator, COUNT(*) FROM evaluations GROUP BY evaluator").fetchall()
    )
    status = dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    leased = conn.execute("SELECT COUNT(*) FROM jobs WHERE claimed_at IS NOT NULL").fetchone()[0]
    return {
        "by_status": status,
        "by_source": db.stats(conn),
        "evaluations_by_evaluator": by_eval,
        "active_leases": leased,
    }


def rubric_prompt(cfg: Config) -> str:
    return f"{cfg.rubric()}\n\n## Candidate profile\n{profile_brief(cfg.profile)}\n{WORKFLOW}"


# --- Servidor ----------------------------------------------------------------------------


def build_server(cfg: Config):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="job-radar",
        instructions=(
            "Tools over the local job-radar database (job ads for a Data/BI profile, "
            "active countries only). Use the 'rubric' prompt for the evaluation criteria. Start with "
            "get_batch, then save_evaluations once per batch."
        ),
    )

    @contextmanager
    def conn():
        c = db.connect(cfg.db_path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    @server.tool()
    def get_batch(
        n: int = 10,
        mode: Literal["shortlist", "pending"] = "shortlist",
        min_score: float | None = None,
        max_score: float | None = None,
        days: int | None = None,
    ) -> dict:
        """Lock and return a batch of jobs to evaluate (lease of 30 minutes).

        The response includes `evaluation_guide`: the rubric, the candidate profile and the
        workflow. Follow it; it overrides anything you remember about the candidate.

        mode="shortlist": today's jobs not yet reviewed by Claude. Without min_score/max_score:
        the most doubtful ones (score 50-80, closest to the threshold first). With them: that
        score range, best first (e.g. min_score=60, max_score=100 for a full second opinion).
        days (shortlist mode): look back N days instead of the default 1 (e.g. days=7 to review
        the whole week). Call again until `count` is 0 to cover everything in the range.
        mode="pending": jobs nobody evaluated yet. Jobs Fran already applied to or discarded
        are never returned.
        Each job includes the condensed description, language/residency evidence snippets and
        the previous evaluation if any.
        """
        with conn() as c:
            return tool_get_batch(cfg, c, n, mode, min_score, max_score, days)

    @server.tool()
    def save_evaluations(items: list[dict[str, Any]], model: str | None = None) -> dict:
        """Save a whole batch of evaluations in one call; the server computes the score.

        Each item: job_id, lang_required [{lang: de|fr|nl|it|lb|other, level: none|nice|required,
        cefr: A1..C2|null}], residency_hard_req, residency_country (ISO-2|null), onsite_only,
        seniority (junior|mid|senior|lead|unknown), role_family (DE|AE|BI|DA|DS|other),
        stack_match [..], red_flags [max 5], rationale (max 25 words). Invalid items are
        reported in `failed` with the reason; valid ones are saved anyway.
        `model`: optional name of the Claude model doing the evaluation.
        """
        with conn() as c:
            return tool_save_evaluations(cfg, c, items, model)

    @server.tool()
    def get_job_detail(id: str) -> dict:
        """Full job ad (raw description) plus every evaluation stored for it."""
        with conn() as c:
            return tool_get_job_detail(cfg, c, id)

    @server.tool()
    def get_shortlist(
        min_score: float = 75,
        days: int | None = 1,
        only_new: bool = False,
        include_decided: bool = False,
    ) -> dict:
        """Best jobs by current score (>= min_score), newest evaluations first by score.

        days: only evaluations from the last N days; null = full historical ranking.
        only_new: only jobs never included in a daily CSV report (what the user hasn't seen).
        Each job says `already_reported` so you can tell new from previously seen.
        include_decided: also return jobs Fran already applied to or discarded (`decision`).
        """
        with conn() as c:
            return tool_get_shortlist(cfg, c, min_score, days, only_new, include_decided)

    @server.tool()
    def set_decisions(items: list[dict[str, Any]]) -> dict:
        """Record Fran's decisions in one call: [{job_id, decision, note?}].

        decision: "applied" (Fran sent the application), "discarded" (never show it again)
        or null (undo). Only record what Fran confirmed; never decide on Fran's behalf.
        Decided jobs disappear from get_batch, get_shortlist and the daily CSV.
        """
        with conn() as c:
            return tool_set_decisions(cfg, c, items)

    @server.tool()
    def get_applications() -> dict:
        """Jobs Fran marked as applied, most recent first, with date and note."""
        with conn() as c:
            return tool_get_applications(cfg, c)

    @server.tool()
    def get_stats() -> dict:
        """Job counts by status and source, evaluations by evaluator, active leases."""
        with conn() as c:
            return tool_get_stats(cfg, c)

    @server.prompt(name="rubric", description="Evaluation rubric, candidate profile and workflow")
    def rubric() -> str:
        return rubric_prompt(cfg)

    return server


def desktop_config(cfg: Config) -> dict:
    """Snippet para ~/Library/Application Support/Claude/claude_desktop_config.json."""
    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    return {
        "mcpServers": {
            "job-radar": {
                "command": uv,
                "args": ["--directory", str(cfg.root.resolve()), "run", "job-radar-mcp"],
            }
        }
    }


def main() -> None:
    # stdout es el canal del protocolo: todo log va a stderr.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    build_server(load_config()).run("stdio")


if __name__ == "__main__":
    main()
