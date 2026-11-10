"""Export CSV (novedades, histórico o por día) y rescore (recalcular scores sin reevaluar)."""

from __future__ import annotations

import csv
import glob
import json
import logging
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from job_radar import db
from job_radar.config import Config
from job_radar.models import EvaluationFeatures
from job_radar.scoring import JobContext, score

log = logging.getLogger(__name__)

COLUMNS = [
    "score",
    "title",
    "company",
    "location",
    "country",
    "url",
    "seniority",
    "role_family",
    "languages",
    "stack_match",
    "red_flags",
    "rationale",
    "evaluator",
    "source",
    "first_seen",
    "id",
    "decision",
]


def _lang_summary(f: dict) -> str:
    return "; ".join(
        f"{x['lang']} {x['level']}{' ' + x['cefr'] if x.get('cefr') else ''}"
        for x in f.get("lang_required", [])
    )


def mirror_dirs(cfg: Config) -> list[Path]:
    """Carpetas espejo existentes (con ~ y comodines expandidos)."""
    out: list[Path] = []
    for pattern in cfg.app.exports_mirror_dirs:
        expanded = os.path.expanduser(pattern)
        matches = (
            [Path(p) for p in glob.glob(expanded)] if glob.has_magic(expanded) else [Path(expanded)]
        )
        found = [p for p in matches if p.is_dir()]
        if not found:
            log.warning("carpeta espejo no encontrada: %s", pattern)
        out.extend(found)
    return out


def copy_to_mirrors(cfg: Config, path: Path) -> list[str]:
    """Copia el CSV a las carpetas espejo. Una copia fallida se loguea y no corta nada."""
    copied = []
    for d in mirror_dirs(cfg):
        try:
            shutil.copy2(path, d / path.name)
            copied.append(str(d / path.name))
        except OSError as e:
            log.warning("no pude copiar %s a %s: %s", path.name, d, e)
    return copied


def _stack_from_breakdown(breakdown_json: str | None) -> str:
    """Stack que sumó puntos (LLM + texto del aviso), tal como quedó en el breakdown."""
    for b in json.loads(breakdown_json or "[]"):
        if str(b.get("rule", "")).startswith("stack: "):
            return b["rule"].removeprefix("stack: ")
    return ""


def export_csv(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    mode: Literal["new", "all", "day"] = "new",
    day: date | None = None,
    min_score: float | None = None,
) -> tuple[Path, int]:
    """Exporta los jobs con score vigente >= umbral, ordenados por score.

    - mode="new" (default, el digest diario): solo jobs que nunca salieron en un reporte.
      Se agregan a exports/shortlist_<hoy>.csv y quedan marcados (exported_at).
    - mode="all": ranking histórico completo en exports/ranking_all.csv (no marca nada).
    - mode="day": jobs evaluados en `day`, en exports/evaluated_<day>.csv (no marca nada).
    """
    today = date.today()
    threshold = cfg.scoring.threshold if min_score is None else min_score
    prio = cfg.scoring.evaluator_priority
    out_dir = cfg.path(cfg.app.exports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    active = cfg.searches.active_codes
    if mode == "new":
        rows = db.current_evaluations(
            conn, prio, min_score=threshold, unexported_only=True, countries=active
        )
        path = out_dir / f"shortlist_{today.isoformat()}.csv"
    elif mode == "all":
        # El histórico incluye lo ya aplicado/descartado, con la columna "decision".
        rows = db.current_evaluations(conn, prio, min_score=threshold, include_decided=True)
        path = out_dir / "ranking_all.csv"
    elif mode == "day":
        d = (day or today).isoformat()
        rows = db.current_evaluations(
            conn, prio, min_score=threshold, evaluated_on=d, countries=active
        )
        path = out_dir / f"evaluated_{d}.csv"
    else:
        raise ValueError(f"modo desconocido: {mode}")

    # En modo "new" una segunda corrida del mismo día agrega al archivo en vez de pisarlo.
    append = mode == "new" and path.exists()
    with path.open("a" if append else "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if not append:
            w.writerow(COLUMNS)
        for r in rows:
            f = json.loads(r["e_features"])
            w.writerow(
                [
                    r["e_score"],
                    r["title"],
                    r["company"],
                    r["location"],
                    r["country"],
                    r["url"],
                    f.get("seniority"),
                    f.get("role_family"),
                    _lang_summary(f),
                    _stack_from_breakdown(r["e_breakdown"]),
                    "; ".join(f.get("red_flags", [])),
                    r["e_rationale"],
                    r["e_evaluator"],
                    r["source"],
                    r["first_seen"],
                    r["id"],
                    r["decision"] or "",
                ]
            )
    if mode == "new":
        db.mark_exported(conn, [r["id"] for r in rows])
        conn.commit()
    copy_to_mirrors(cfg, path)
    return path, len(rows)


def rescore(conn: sqlite3.Connection, cfg: Config) -> int:
    """Recalcula score_computed de todas las evaluaciones con config/scoring.yaml actual."""
    n = 0
    for row in db.evaluations_with_jobs(conn):
        f = EvaluationFeatures.model_validate_json(row["features"])
        ctx = JobContext.from_row(row)
        db.update_score(conn, row["id"], score(f, ctx, cfg.profile, cfg.scoring))
        n += 1
    conn.commit()
    return n
