"""Persistencia SQLite (WAL). Toda la SQL del proyecto vive acá."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from job_radar.config import DedupSettings
from job_radar.dedup import dedup_key, hamming, normalize_title, simhash
from job_radar.models import EvaluationFeatures, JobStatus, PrefilterResult, RawJob
from job_radar.scoring import ScoreResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,          -- source:job_id
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    country         TEXT,
    posted_at       TEXT,
    description_raw TEXT,
    desc_condensed  TEXT,
    desc_lang       TEXT,
    lang_snippets   TEXT,                      -- JSON: [{kind, match, text}]
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    simhash         TEXT,                      -- hex de 64 bits
    status          TEXT NOT NULL DEFAULT 'pending',
    exclude_reason  TEXT,
    duplicate_of    TEXT,
    -- Lease independiente del status: un job 'evaluated' puede reclamarse
    -- para revisión con Claude sin perder su estado.
    claimed_at      TEXT,
    claimed_by      TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS ix_jobs_first_seen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS evaluations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    evaluator       TEXT NOT NULL,             -- gemini | claude
    model           TEXT,
    prompt_version  TEXT,
    created_at      TEXT NOT NULL,
    features        TEXT NOT NULL,             -- JSON del schema Evaluation
    rationale       TEXT,
    score_computed  REAL,
    score_breakdown TEXT                       -- JSON: componentes del score
);
CREATE INDEX IF NOT EXISTS ix_eval_job ON evaluations(job_id);

-- Registro de llamadas a LLMs, para respetar el límite diario (RPD) entre corridas.
CREATE TABLE IF NOT EXISTS llm_calls (
    ts       TEXT NOT NULL,
    provider TEXT NOT NULL,
    model    TEXT,
    ok       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(provider, ts);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Columnas agregadas después de la versión inicial: (tabla, nombre, tipo). Se agregan si faltan.
_MIGRATIONS = [
    ("jobs", "dedup_key", "TEXT"),
    ("jobs", "exported_at", "TEXT"),
    ("llm_calls", "tokens_in", "INTEGER"),
    ("llm_calls", "tokens_out", "INTEGER"),  # incluye tokens de "thinking"
    # Decisión de Fran sobre el aviso: applied | discarded (NULL = sin decidir).
    ("jobs", "decision", "TEXT"),
    ("jobs", "decided_at", "TEXT"),
    ("jobs", "decision_note", "TEXT"),
]


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table, name, typ in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if name not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_dedup_key ON jobs(dedup_key)")
    return conn


def known_ids(conn: sqlite3.Connection) -> set[str]:
    """Ids que no hace falta volver a pedir en detalle (los que quedaron sin descripción sí)."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT id FROM jobs WHERE exclude_reason IS NULL OR exclude_reason != 'no_description'"
        )
    }


def missing_descriptions(
    conn: sqlite3.Connection, source: str, since: str, countries: list[str] | None = None
) -> list[sqlite3.Row]:
    """Avisos de `source` vistos desde `since` (ISO UTC) que quedaron sin descripción."""
    country = f"AND {_country_clause(countries, 'country')}" if countries else ""
    return conn.execute(
        f"""SELECT id, url, title, company, location, country, posted_at FROM jobs
            WHERE source = ? AND exclude_reason = 'no_description' AND last_seen >= ? {country}
            ORDER BY last_seen DESC""",
        (source, since, *(countries or [])),
    ).fetchall()


def _find_duplicate(
    conn: sqlite3.Connection,
    job: RawJob,
    key: str | None,
    sh: int | None,
    dd: DedupSettings,
    seen_before: tuple[str, str] | None = None,
) -> str | None:
    """Niveles 2 y 3. Solo compara contra avisos vivos (pending/evaluated) de la ventana.

    `seen_before=(first_seen, id)`: solo considera avisos anteriores a ese (para re-deduplicar
    la base sin que el original quede marcado como copia del más nuevo).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=dd.window_days)).isoformat(timespec="seconds")
    alive = "status IN ('pending', 'evaluated') AND first_seen >= ? AND id != ?"
    params: list = [cutoff, job.id]
    if seen_before:
        alive += " AND (first_seen < ? OR (first_seen = ? AND id < ?))"
        params += [seen_before[0], seen_before[0], seen_before[1]]
    if key:
        row = conn.execute(
            f"SELECT id FROM jobs WHERE dedup_key = ? AND {alive} ORDER BY first_seen, id LIMIT 1",
            [key, *params],
        ).fetchone()
        if row:
            return row["id"]
    if sh is not None:
        t_norm = normalize_title(job.title)
        country_up = (job.country or "").upper()
        for row in conn.execute(
            f"SELECT id, title, country, simhash FROM jobs WHERE simhash IS NOT NULL AND {alive} "
            "ORDER BY first_seen, id",
            params,
        ):
            same_tc = (
                bool(t_norm)
                and (row["country"] or "").upper() == country_up
                and normalize_title(row["title"]) == t_norm
            )
            max_d = (
                max(dd.simhash_max_distance, dd.simhash_same_title_max_distance)
                if same_tc
                else dd.simhash_max_distance
            )
            if hamming(sh, int(row["simhash"], 16)) <= max_d:
                return row["id"]
    return None


def _classify(
    conn: sqlite3.Connection, job: RawJob, pre: PrefilterResult, dd: DedupSettings | None
) -> tuple[str, str | None, str | None, str | None]:
    """(status, duplicate_of, dedup_key, simhash_hex) para un job nuevo o re-procesado."""
    key = dedup_key(job.company, job.title, job.country)
    desc = job.description or ""
    sh = simhash(desc) if dd and len(desc) >= dd.simhash_min_chars else None
    sh_hex = f"{sh:016x}" if sh is not None else None
    if pre.excluded:
        return JobStatus.excluded_prefilter, None, key, sh_hex
    dup = _find_duplicate(conn, job, key, sh, dd) if dd else None
    return (JobStatus.duplicate if dup else JobStatus.pending), dup, key, sh_hex


def upsert_job(
    conn: sqlite3.Connection,
    job: RawJob,
    pre: PrefilterResult,
    dd: DedupSettings | None = None,
) -> bool:
    """Inserta un job nuevo o refresca last_seen. Devuelve True si era nuevo.

    Deduplicación: nivel 1 por id (PK); niveles 2 y 3 (clave y simhash) si se pasa `dd`.
    """
    ts = now_iso()
    cur = conn.execute(
        """UPDATE jobs SET last_seen = ?,
               status = CASE WHEN status = 'stale' THEN 'pending' ELSE status END
           WHERE id = ?""",
        (ts, job.id),
    )
    if cur.rowcount:
        # Si la primera vez llegó sin descripción (ej. 429 de LinkedIn en el detalle)
        # y ahora sí la trae, se re-procesa con el resultado nuevo del prefiltro.
        if job.description.strip():
            row = conn.execute("SELECT exclude_reason FROM jobs WHERE id = ?", (job.id,)).fetchone()
            if row["exclude_reason"] == "no_description":
                status, dup, key, sh = _classify(conn, job, pre, dd)
                conn.execute(
                    """UPDATE jobs SET description_raw = ?, desc_condensed = ?, desc_lang = ?,
                           lang_snippets = ?, status = ?, exclude_reason = ?, duplicate_of = ?,
                           dedup_key = ?, simhash = ?
                       WHERE id = ?""",
                    (
                        job.description,
                        pre.desc_condensed,
                        pre.desc_lang,
                        _snippets_json(pre),
                        status,
                        pre.reason,
                        dup,
                        key,
                        sh,
                        job.id,
                    ),
                )
        return False
    status, dup, key, sh = _classify(conn, job, pre, dd)
    conn.execute(
        """INSERT INTO jobs (id, source, url, title, company, location, country, posted_at,
               description_raw, desc_condensed, desc_lang, lang_snippets,
               first_seen, last_seen, status, exclude_reason, duplicate_of, dedup_key, simhash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job.id,
            job.source,
            job.url,
            job.title,
            job.company,
            job.location,
            job.country,
            job.posted_at.isoformat() if job.posted_at else None,
            job.description,
            pre.desc_condensed,
            pre.desc_lang,
            _snippets_json(pre),
            ts,
            ts,
            status,
            pre.reason,
            dup,
            key,
            sh,
        ),
    )
    return True


def _snippets_json(pre: PrefilterResult) -> str:
    return json.dumps([s.model_dump() for s in pre.snippets], ensure_ascii=False)


def release_expired_leases(conn: sqlite3.Connection, minutes: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE jobs SET claimed_at = NULL, claimed_by = NULL "
        "WHERE claimed_at IS NOT NULL AND claimed_at < ?",
        (cutoff,),
    )
    return cur.rowcount


def mark_stale(conn: sqlite3.Connection, days: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE jobs SET status = 'stale' "
        "WHERE last_seen < ? AND status IN ('pending', 'evaluated')",
        (cutoff,),
    )
    return cur.rowcount


def stats(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Conteos por fuente y status: {source: {status: n}}."""
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT source, status, COUNT(*) n FROM jobs GROUP BY source, status ORDER BY source"
    ):
        out.setdefault(row["source"], {})[row["status"]] = row["n"]
    return out


# --- Evaluaciones ----------------------------------------------------------------------------


def insert_evaluation(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    evaluator: str,
    model: str | None,
    prompt_version: str,
    features: EvaluationFeatures,
    result: ScoreResult,
) -> int:
    cur = conn.execute(
        """INSERT INTO evaluations (job_id, evaluator, model, prompt_version, created_at,
               features, rationale, score_computed, score_breakdown)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id,
            evaluator,
            model,
            prompt_version,
            now_iso(),
            features.model_dump_json(exclude={"job_id"}),
            features.rationale,
            result.score,
            json.dumps(result.breakdown, ensure_ascii=False),
        ),
    )
    # Libera el lease y pasa a 'evaluated' (sin tocar 'stale', 'duplicate', etc.).
    conn.execute(
        """UPDATE jobs SET claimed_at = NULL, claimed_by = NULL,
               status = CASE WHEN status = 'pending' THEN 'evaluated' ELSE status END
           WHERE id = ?""",
        (job_id,),
    )
    return cur.lastrowid


def pending_for_evaluation(
    conn: sqlite3.Connection, limit: int, countries: list[str] | None = None
) -> list[sqlite3.Row]:
    """Jobs pendientes sin lease vigente, los más nuevos primero. `countries` vacío = todos."""
    country = f"AND {_country_clause(countries, 'country')}" if countries else ""
    return conn.execute(
        f"""SELECT * FROM jobs WHERE status = 'pending' AND claimed_at IS NULL {country}
            ORDER BY first_seen DESC, id LIMIT ?""",
        (*(countries or []), limit),
    ).fetchall()


def evaluations_with_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todas las evaluaciones con los datos del job que usa el scoring (para rescore)."""
    return conn.execute(
        """SELECT e.id, e.features, j.location, j.country, j.desc_lang, j.title,
                  j.description_raw
           FROM evaluations e JOIN jobs j ON j.id = e.job_id"""
    ).fetchall()


def update_score(conn: sqlite3.Connection, eval_id: int, result: ScoreResult) -> None:
    conn.execute(
        "UPDATE evaluations SET score_computed = ?, score_breakdown = ? WHERE id = ?",
        (result.score, json.dumps(result.breakdown, ensure_ascii=False), eval_id),
    )


def current_evaluations(
    conn: sqlite3.Connection,
    priority: list[str],
    *,
    min_score: float | None = None,
    max_score: float | None = None,
    evaluated_on: str | None = None,  # 'YYYY-MM-DD' en hora local
    since_days: int | None = None,
    job_ids: list[str] | None = None,
    unexported_only: bool = False,
    include_decided: bool = False,
    countries: list[str] | None = None,  # vacío o None = todos
) -> list[sqlite3.Row]:
    """Evaluación vigente por job: el primer evaluador de `priority` y, dentro de él, la más
    reciente. Devuelve columnas del job + e_* de la evaluación."""
    order = " ".join(f"WHEN ? THEN {i}" for i in range(len(priority)))
    where, params = ["rn = 1", "j.status NOT IN ('duplicate', 'excluded_prefilter')"], []
    if min_score is not None:
        where.append("r.score_computed >= ?")
        params.append(min_score)
    if max_score is not None:
        where.append("r.score_computed <= ?")
        params.append(max_score)
    if evaluated_on:
        where.append("date(r.created_at, 'localtime') = ?")
        params.append(evaluated_on)
    if since_days is not None:
        where.append("r.created_at >= ?")
        params.append(
            (datetime.now(UTC) - timedelta(days=since_days)).isoformat(timespec="seconds")
        )
    if job_ids:
        where.append(f"j.id IN ({','.join('?' * len(job_ids))})")
        params.extend(job_ids)
    if unexported_only:
        where.append("j.exported_at IS NULL")
    if not include_decided:
        where.append("j.decision IS NULL")
    if countries:
        where.append(_country_clause(countries, "j.country"))
        params.extend(countries)
    case = f"CASE e.evaluator {order} ELSE {len(priority)} END" if priority else "0"
    sql = f"""
        WITH r AS (
            SELECT e.*, ROW_NUMBER() OVER (
                PARTITION BY e.job_id ORDER BY {case}, e.created_at DESC, e.id DESC
            ) AS rn
            FROM evaluations e
        )
        SELECT j.*, r.id AS e_id, r.evaluator AS e_evaluator, r.model AS e_model,
               r.created_at AS e_created_at, r.features AS e_features,
               r.rationale AS e_rationale, r.score_computed AS e_score,
               r.score_breakdown AS e_breakdown
        FROM r JOIN jobs j ON j.id = r.job_id
        WHERE {" AND ".join(where)}
        ORDER BY r.score_computed DESC, j.first_seen DESC"""
    return conn.execute(sql, [*priority, *params]).fetchall()


def log_llm_call(
    conn: sqlite3.Connection,
    provider: str,
    model: str,
    ok: bool,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO llm_calls (ts, provider, model, ok, tokens_in, tokens_out) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now_iso(), provider, model, int(ok), tokens_in, tokens_out),
    )
    conn.commit()


def llm_calls_since(
    conn: sqlite3.Connection, provider: str, since: datetime, model: str | None = None
) -> int:
    sql = "SELECT COUNT(*) FROM llm_calls WHERE provider = ? AND ts >= ?"
    params = [provider, since.astimezone(UTC).isoformat(timespec="seconds")]
    if model:
        sql += " AND model = ?"
        params.append(model)
    return conn.execute(sql, params).fetchone()[0]


def reprefilter(conn: sqlite3.Connection, pre_run, reopen_days: int = 14) -> dict[str, int]:
    """Vuelve a correr el prefiltro sobre description_raw de todos los jobs.

    Refresca desc_condensed, desc_lang y snippets en todos. El status cambia en jobs 'pending'
    (pasan a excluded_prefilter si ahora corresponde) y en los descartados por título vistos
    en los últimos `reopen_days` días, si el título ya es relevante: lo evaluado no se toca.
    """
    out = {"refreshed": 0, "newly_excluded": 0, "reopened": 0}
    cutoff = (datetime.now(UTC) - timedelta(days=reopen_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, source, url, title, company, location, country, description_raw, status, "
        "exclude_reason, last_seen FROM jobs WHERE exclude_reason IS NULL OR exclude_reason != 'no_description'"
    ).fetchall()
    for r in rows:
        job = RawJob(
            source=r["source"],
            job_id=r["id"].split(":", 1)[1],
            url=r["url"],
            title=r["title"],
            company=r["company"],
            location=r["location"],
            country=r["country"],
            description=r["description_raw"] or "",
        )
        res = pre_run(job)
        conn.execute(
            "UPDATE jobs SET desc_condensed = ?, desc_lang = ?, lang_snippets = ? WHERE id = ?",
            (res.desc_condensed, res.desc_lang, _snippets_json(res), r["id"]),
        )
        out["refreshed"] += 1
        if r["status"] == JobStatus.pending and res.excluded:
            conn.execute(
                "UPDATE jobs SET status = 'excluded_prefilter', exclude_reason = ? WHERE id = ?",
                (res.reason, r["id"]),
            )
            out["newly_excluded"] += 1
        elif (
            r["exclude_reason"] == "title_not_relevant"
            and r["last_seen"] >= cutoff
            and res.reason != "title_not_relevant"
        ):
            # Sin descripción queda en no_description: se pide cuando el aviso reaparezca.
            conn.execute(
                "UPDATE jobs SET status = ?, exclude_reason = ? WHERE id = ?",
                ("excluded_prefilter" if res.excluded else "pending", res.reason, r["id"]),
            )
            out["reopened"] += not res.excluded
    conn.commit()
    return out


# --- Leases para la vía Claude (MCP) -------------------------------------------------------


def _claim(conn: sqlite3.Connection, ids: list[str], who: str) -> None:
    conn.executemany(
        "UPDATE jobs SET claimed_at = ?, claimed_by = ? WHERE id = ?",
        [(now_iso(), who, i) for i in ids],
    )


def _country_clause(countries: list[str], column: str) -> str:
    return f"({column} IS NULL OR {column} IN ({','.join('?' * len(countries))}))"


def claim_pending(
    conn: sqlite3.Connection,
    n: int,
    who: str,
    lease_minutes: int,
    *,
    countries: list[str] | None = None,
) -> list[str]:
    """Reclama hasta n jobs 'pending' sin lease vigente. Atómico (BEGIN IMMEDIATE)."""
    countries = countries or []
    country = f"AND {_country_clause(countries, 'country')} " if countries else ""
    conn.execute("BEGIN IMMEDIATE")
    try:
        release_expired_leases(conn, lease_minutes)
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM jobs WHERE status = 'pending' AND claimed_at IS NULL "
                f"AND decision IS NULL {country}ORDER BY first_seen DESC, id LIMIT ?",
                (*countries, n),
            )
        ]
        _claim(conn, ids, who)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


def claim_shortlist(
    conn: sqlite3.Connection,
    n: int,
    who: str,
    lease_minutes: int,
    *,
    lo: float,
    hi: float,
    days: int,
    pivot: float | None,
    priority: list[str],
    countries: list[str] | None = None,
) -> list[str]:
    """Reclama jobs con score vigente en [lo, hi], evaluados en los últimos `days` días, sin
    evaluación previa de `who` ni decisión tomada. Orden: cercanía a `pivot` (los más dudosos
    primero) o, si `pivot` es None, score descendente."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        release_expired_leases(conn, lease_minutes)
        rows = current_evaluations(
            conn, priority, min_score=lo, max_score=hi, since_days=days, countries=countries
        )
        reviewed = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT job_id FROM evaluations WHERE evaluator = ?", (who,)
            )
        }
        cands = [
            r
            for r in rows
            if r["claimed_at"] is None and r["id"] not in reviewed and r["status"] == "evaluated"
        ]
        if pivot is None:
            cands.sort(key=lambda r: (-r["e_score"], r["id"]))
        else:
            cands.sort(key=lambda r: (abs(r["e_score"] - pivot), r["id"]))
        ids = [r["id"] for r in cands[:n]]
        _claim(conn, ids, who)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


def get_jobs(conn: sqlite3.Connection, ids: list[str]) -> list[sqlite3.Row]:
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    order = {i: k for k, i in enumerate(ids)}
    return sorted(rows, key=lambda r: order[r["id"]])


def evaluations_for(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evaluations WHERE job_id = ? ORDER BY created_at, id", (job_id,)
    ).fetchall()


def mark_exported(conn: sqlite3.Connection, ids: list[str]) -> None:
    """Marca jobs como ya incluidos en un reporte (no vuelven a salir en modo novedades)."""
    conn.executemany(
        "UPDATE jobs SET exported_at = ? WHERE id = ? AND exported_at IS NULL",
        [(now_iso(), i) for i in ids],
    )


def backfill_dedup(conn: sqlite3.Connection, dd: DedupSettings) -> dict:
    """Completa/actualiza dedup_key y simhash y vuelve a deduplicar los avisos vivos: el más
    viejo queda, los posteriores pasan a 'duplicate'. Las evaluaciones se conservan; los
    duplicados solo dejan de salir en reportes."""
    filled = 0
    for r in conn.execute(
        "SELECT id, company, title, country, description_raw, dedup_key, simhash FROM jobs"
    ).fetchall():
        desc = r["description_raw"] or ""
        new_key = dedup_key(r["company"], r["title"], r["country"])
        new_sh = (
            r["simhash"]
            if r["simhash"] is not None
            else (f"{simhash(desc):016x}" if len(desc) >= dd.simhash_min_chars else None)
        )
        if r["dedup_key"] != new_key or r["simhash"] != new_sh:
            conn.execute(
                "UPDATE jobs SET dedup_key = ?, simhash = ? WHERE id = ?",
                (new_key, new_sh, r["id"]),
            )
            filled += 1
    marked = []
    for r in conn.execute(
        "SELECT * FROM jobs WHERE status IN ('pending', 'evaluated') ORDER BY first_seen, id"
    ).fetchall():
        job = RawJob(
            source=r["source"],
            job_id=r["id"].split(":", 1)[1],
            url=r["url"],
            title=r["title"],
            company=r["company"],
            country=r["country"],
        )
        sh = int(r["simhash"], 16) if r["simhash"] else None
        dup = _find_duplicate(conn, job, r["dedup_key"], sh, dd, (r["first_seen"], r["id"]))
        if dup:
            conn.execute(
                "UPDATE jobs SET status = 'duplicate', duplicate_of = ? WHERE id = ?",
                (dup, r["id"]),
            )
            marked.append({"id": r["id"], "duplicate_of": dup, "title": r["title"]})
    conn.commit()
    return {"filled": filled, "duplicates": marked}


def llm_usage(conn: sqlite3.Connection, days: int = 30) -> list[sqlite3.Row]:
    """Requests y tokens por día y modelo (para controlar el gasto)."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    return conn.execute(
        """SELECT date(ts, 'localtime') AS day, model, COUNT(*) AS requests, SUM(ok) AS ok,
                  COALESCE(SUM(tokens_in), 0) AS tokens_in,
                  COALESCE(SUM(tokens_out), 0) AS tokens_out
           FROM llm_calls WHERE ts >= ? GROUP BY 1, 2 ORDER BY 1 DESC, 2""",
        (cutoff,),
    ).fetchall()


# --- Decisiones (aplicada / descartada) ----------------------------------------------------

DECISIONS = ("applied", "discarded")


def set_decision(
    conn: sqlite3.Connection, job_id: str, decision: str | None, note: str | None = None
) -> bool:
    """Registra (o borra, con decision=None) la decisión sobre un job. False si no existe."""
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS} or null")
    cur = conn.execute(
        "UPDATE jobs SET decision = ?, decided_at = ?, decision_note = ?, "
        "claimed_at = NULL, claimed_by = NULL WHERE id = ?",
        (decision, now_iso() if decision else None, note if decision else None, job_id),
    )
    return cur.rowcount == 1


def decided_jobs(conn: sqlite3.Connection, decision: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT j.*, (SELECT e.score_computed FROM evaluations e WHERE e.job_id = j.id
                        ORDER BY e.created_at DESC, e.id DESC LIMIT 1) AS last_score
           FROM jobs j WHERE j.decision = ? ORDER BY j.decided_at DESC""",
        (decision,),
    ).fetchall()
