"""Read models for the web API. All queries are bounded and return JSON-safe dictionaries."""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from job_radar import db
from job_radar.config import Config

INTERNSHIP_TERMS = (
    "intern",
    "internship",
    "trainee",
    "stage",
    "stagiair",
    "praktik",
    "werkstudent",
    "alternan",
    "tirocin",
    "práctic",
    "practic",
    "estági",
    "estagi",
    "becari",
)
DATA_TERMS = (
    "data",
    "daten",
    "dato",
    "donnée",
    "donnee",
    "analytics",
    "analyst",
    "business intelligence",
    "machine learning",
    "ai ",
    " ai ",
    "ml ",
    " ml ",
    "etl",
    "dwh",
    "warehouse",
    "sql",
    "database",
    "reporting",
)


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _ranked_evaluations(priority: list[str]) -> tuple[str, list[str]]:
    order = " ".join(f"WHEN ? THEN {i}" for i in range(len(priority)))
    case = f"CASE e.evaluator {order} ELSE {len(priority)} END" if priority else "0"
    return (
        f"""ranked_evaluations AS (
                SELECT e.*, ROW_NUMBER() OVER (
                    PARTITION BY e.job_id
                    ORDER BY {case}, e.created_at DESC, e.id DESC
                ) AS rn
                FROM evaluations e
            ),
            current_evaluation AS (
                SELECT * FROM ranked_evaluations WHERE rn = 1
            )""",
        priority,
    )


def _like_any(column: str, terms: Iterable[str], params: list[Any]) -> str:
    clauses = []
    for term in terms:
        clauses.append(f"LOWER({column}) LIKE ?")
        params.append(f"%{term.lower()}%")
    return "(" + " OR ".join(clauses) + ")"


def _job_summary(row: sqlite3.Row) -> dict[str, Any]:
    breakdown = _json(row["score_breakdown"], [])
    exclusions = [
        item["exclude"] for item in breakdown if isinstance(item, dict) and item.get("exclude")
    ]
    return {
        "id": row["id"],
        "source": row["source"],
        "url": row["url"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "country": row["country"],
        "posted_at": row["posted_at"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "status": row["status"],
        "exclude_reason": row["exclude_reason"],
        "duplicate_of": row["duplicate_of"],
        "decision": row["decision"],
        "decided_at": row["decided_at"],
        "decision_note": row["decision_note"],
        "score": row["score"],
        "evaluator": row["evaluator"],
        "model": row["model"],
        "rationale": row["rationale"],
        "features": _json(row["features"], {}),
        "fit_excluded": bool(exclusions),
        "fit_exclusion_reasons": exclusions,
    }


def query_jobs(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    page: int = 1,
    page_size: int = 30,
    q: str = "",
    countries: list[str] | None = None,
    statuses: list[str] | None = None,
    decision: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    kind: str | None = None,
    sort: str = "first_seen",
    direction: str = "desc",
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    cte, priority_params = _ranked_evaluations(cfg.scoring.evaluator_priority)
    where: list[str] = ["1=1"]
    params: list[Any] = []

    if q.strip():
        needle = f"%{q.strip()}%"
        where.append(
            "(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ? OR j.description_raw LIKE ?)"
        )
        params.extend([needle] * 4)
    if countries:
        countries = [c.upper() for c in countries]
        where.append(f"j.country IN ({','.join('?' for _ in countries)})")
        params.extend(countries)
    if statuses:
        where.append(f"j.status IN ({','.join('?' for _ in statuses)})")
        params.extend(statuses)
    if decision == "none":
        where.append("j.decision IS NULL")
    elif decision in db.DECISIONS:
        where.append("j.decision = ?")
        params.append(decision)
    if min_score is not None:
        where.append("e.score_computed >= ?")
        params.append(min_score)
    if max_score is not None:
        where.append("e.score_computed <= ?")
        params.append(max_score)
    if kind == "shortlist":
        where.extend(
            [
                "e.score_computed >= ?",
                "j.status NOT IN ('duplicate', 'excluded_prefilter', 'stale')",
                "j.decision IS NULL",
            ]
        )
        params.append(cfg.scoring.threshold)
    elif kind == "internship":
        where.append(_like_any("j.title", INTERNSHIP_TERMS, params))
        where.append(_like_any("j.title", DATA_TERMS, params))
    elif kind == "ranking":
        where.extend(
            [
                "e.score_computed IS NOT NULL",
                "j.status NOT IN ('duplicate', 'excluded_prefilter', 'stale')",
            ]
        )

    sort_columns = {
        "first_seen": "j.first_seen",
        "score": "e.score_computed",
        "title": "j.title",
        "company": "j.company",
        "country": "j.country",
        "status": "j.status",
    }
    order_column = sort_columns.get(sort, "j.first_seen")
    order_direction = "ASC" if direction.lower() == "asc" else "DESC"
    where_sql = " AND ".join(where)
    base = f"""
        WITH {cte}
        FROM jobs j
        LEFT JOIN current_evaluation e ON e.job_id = j.id
        WHERE {where_sql}
    """
    total = conn.execute(
        f"WITH {cte} SELECT COUNT(*) FROM jobs j "
        "LEFT JOIN current_evaluation e ON e.job_id = j.id "
        f"WHERE {where_sql}",
        [*priority_params, *params],
    ).fetchone()[0]
    rows = conn.execute(
        f"""WITH {cte}
            SELECT j.id, j.source, j.url, j.title, j.company, j.location, j.country,
                   j.posted_at, j.first_seen, j.last_seen, j.status, j.exclude_reason,
                   j.duplicate_of, j.decision, j.decided_at, j.decision_note,
                   e.score_computed AS score, e.evaluator, e.model, e.rationale, e.features,
                   e.score_breakdown
            FROM jobs j
            LEFT JOIN current_evaluation e ON e.job_id = j.id
            WHERE {where_sql}
            ORDER BY {order_column} {order_direction}, j.id
            LIMIT ? OFFSET ?""",
        [*priority_params, *params, page_size, (page - 1) * page_size],
    ).fetchall()
    del base  # keeps the count/list clauses visually aligned above

    countries_facet = [
        {"value": r["country"] or "?", "count": r["n"]}
        for r in conn.execute(
            "SELECT country, COUNT(*) n FROM jobs GROUP BY country ORDER BY country"
        )
    ]
    statuses_facet = [
        {"value": r["status"], "count": r["n"]}
        for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY status")
    ]
    return {
        "items": [_job_summary(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, math.ceil(total / page_size)),
        "facets": {"countries": countries_facet, "statuses": statuses_facet},
    }


def job_detail(conn: sqlite3.Connection, cfg: Config, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    priority = {name: i for i, name in enumerate(cfg.scoring.evaluator_priority)}
    evaluations = []
    for evaluation in db.evaluations_for(conn, job_id):
        item = dict(evaluation)
        item["features"] = _json(item.get("features"), {})
        item["score_breakdown"] = _json(item.get("score_breakdown"), [])
        evaluations.append(item)
    evaluations.sort(
        key=lambda item: (
            priority.get(item["evaluator"], len(priority)),
            -datetime.fromisoformat(item["created_at"]).timestamp(),
            -item["id"],
        )
    )
    data = dict(row)
    data["lang_snippets"] = _json(data.get("lang_snippets"), [])
    data["evaluations"] = evaluations
    data["current_evaluation"] = evaluations[0] if evaluations else None
    if data.get("duplicate_of"):
        duplicate = conn.execute(
            "SELECT id, title, company FROM jobs WHERE id = ?", (data["duplicate_of"],)
        ).fetchone()
        data["duplicate"] = dict(duplicate) if duplicate else None
    return data


def dashboard(conn: sqlite3.Connection, cfg: Config) -> dict[str, Any]:
    status_rows = list(
        conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY status")
    )
    status = {r["status"]: r["n"] for r in status_rows}
    total = sum(status.values())
    country = [
        {"country": r["country"] or "?", "total": r["n"], "pending": r["pending"]}
        for r in conn.execute(
            """SELECT country, COUNT(*) n,
                      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending
               FROM jobs GROUP BY country ORDER BY n DESC"""
        )
    ]
    source = [
        {"source": r["source"], "total": r["n"]}
        for r in conn.execute("SELECT source, COUNT(*) n FROM jobs GROUP BY source ORDER BY n DESC")
    ]
    cte, priority_params = _ranked_evaluations(cfg.scoring.evaluator_priority)
    evaluated = conn.execute("SELECT COUNT(DISTINCT job_id) FROM evaluations").fetchone()[0]
    shortlisted = conn.execute(
        f"""WITH {cte}
            SELECT COUNT(*) FROM jobs j JOIN current_evaluation e ON e.job_id=j.id
            WHERE e.score_computed >= ? AND j.decision IS NULL
              AND j.status NOT IN ('duplicate','excluded_prefilter','stale')""",
        [*priority_params, cfg.scoring.threshold],
    ).fetchone()[0]
    applied = (
        status.get("applied", 0)
        or conn.execute("SELECT COUNT(*) FROM jobs WHERE decision='applied'").fetchone()[0]
    )
    today = date.today().isoformat()
    new_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE date(first_seen, 'localtime') = ?", (today,)
    ).fetchone()[0]
    score_buckets = []
    for label, lo, hi in (
        ("0–49", 0, 50),
        ("50–69", 50, 70),
        ("70–84", 70, 85),
        ("85–100", 85, 101),
    ):
        n = conn.execute(
            f"""WITH {cte}
                SELECT COUNT(*) FROM current_evaluation
                WHERE score_computed >= ? AND score_computed < ?""",
            [*priority_params, lo, hi],
        ).fetchone()[0]
        score_buckets.append({"label": label, "count": n})
    return {
        "metrics": {
            "total": total,
            "new_today": new_today,
            "pending": status.get("pending", 0),
            "evaluated": evaluated,
            "shortlisted": shortlisted,
            "applied": applied,
            "threshold": cfg.scoring.threshold,
        },
        "pipeline": [
            {"label": "Discovered", "value": total},
            {"label": "Eligible", "value": status.get("pending", 0) + status.get("evaluated", 0)},
            {"label": "Evaluated", "value": evaluated},
            {"label": "Shortlisted", "value": shortlisted},
            {"label": "Applied", "value": applied},
        ],
        "status": [{"status": key, "count": value} for key, value in status.items()],
        "countries": country,
        "sources": source,
        "score_buckets": score_buckets,
        "recent": query_jobs(conn, cfg, page_size=8)["items"],
        "top_matches": query_jobs(
            conn, cfg, page_size=5, kind="shortlist", sort="score", direction="desc"
        )["items"],
    }


def usage(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    days = min(365, max(1, days))
    rows = [dict(r) for r in db.llm_usage(conn, days)]
    return {
        "days": days,
        "rows": rows,
        "totals": {
            "requests": sum(r["requests"] for r in rows),
            "ok": sum(r["ok"] for r in rows),
            "tokens_in": sum(r["tokens_in"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
        },
    }


def run_history(cfg: Config, limit: int = 50) -> list[dict[str, Any]]:
    path = cfg.path(cfg.app.logs_dir) / cfg.app.logging.runs_file
    if not path.exists():
        return []
    rows = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def log_tail(cfg: Config, lines: int = 100) -> list[dict[str, Any]]:
    path = cfg.path(cfg.app.logs_dir) / cfg.app.logging.file
    if not path.exists():
        return []
    parsed = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
        try:
            parsed.append(json.loads(line))
        except ValueError:
            parsed.append({"level": "INFO", "msg": line})
    return parsed


def exports(cfg: Config) -> list[dict[str, Any]]:
    directory = cfg.path(cfg.app.exports_dir)
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        with path.open(encoding="utf-8", errors="replace") as file:
            row_count = max(0, sum(1 for _ in file) - 1)
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "rows": row_count,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
            }
        )
    return items


def activity(conn: sqlite3.Connection, days: int = 14) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(days=days - 1)).date()
    counts = {
        r["day"]: r["n"]
        for r in conn.execute(
            """SELECT date(first_seen, 'localtime') day, COUNT(*) n FROM jobs
               WHERE first_seen >= ? GROUP BY day""",
            (since.isoformat(),),
        )
    }
    return [
        {
            "day": (since + timedelta(days=i)).isoformat(),
            "jobs": counts.get((since + timedelta(days=i)).isoformat(), 0),
        }
        for i in range(days)
    ]


def database_info(cfg: Config) -> dict[str, Any]:
    path = cfg.db_path
    return {
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else 0,
        "modified_at": (
            datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
            if path.exists()
            else None
        ),
    }
