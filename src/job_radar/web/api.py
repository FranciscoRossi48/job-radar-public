"""Typed JSON API used by the local React dashboard."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from job_radar import db, export
from job_radar.backup import backup_db
from job_radar.config import Config, load_config
from job_radar.cv_profile import (
    CvImportError,
    ProfileUpdate,
    apply_profile,
    generate_profile_draft,
)
from job_radar.daily import run_daily
from job_radar.evaluate import build_evaluator
from job_radar.ingest.pipeline import build_adapters, make_context
from job_radar.ingest.pipeline import ingest as run_ingest
from job_radar.logs import append_run_summary
from job_radar.prefilter import Prefilterer
from job_radar.web import data
from job_radar.web.schemas import (
    ConfigFileRequest,
    CvGenerateRequest,
    DailyRequest,
    DecisionRequest,
    EvaluateAllRequest,
    EvaluateRequest,
    ExportRequest,
    IngestRequest,
)
from job_radar.web.settings import (
    ConfigSaveError,
    read_config_file,
    save_config_file,
    settings_summary,
    validate_config_file,
)

router = APIRouter(prefix="/api")


def _root(request: Request) -> Path:
    return request.app.state.root


def _cfg(request: Request) -> Config:
    return load_config(_root(request))


def _with_connection(cfg: Config, func: Callable[[Any], Any]) -> Any:
    conn = db.connect(cfg.db_path)
    try:
        return func(conn)
    finally:
        conn.close()


@router.get("/health")
def health(request: Request):
    cfg = _cfg(request)
    return {"ok": True, "database": data.database_info(cfg)}


@router.get("/dashboard")
def get_dashboard(request: Request):
    cfg = _cfg(request)
    result = _with_connection(cfg, lambda conn: data.dashboard(conn, cfg))
    result["activity"] = _with_connection(cfg, lambda conn: data.activity(conn))
    result["runner"] = request.app.state.runner.snapshot()
    return result


@router.get("/jobs")
def get_jobs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", max_length=250),
    country: Annotated[list[str] | None, Query()] = None,
    job_status: Annotated[list[str] | None, Query(alias="status")] = None,
    decision: str | None = Query(None),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    kind: str | None = Query(None, pattern="^(shortlist|internship|ranking)$"),
    sort: str = Query("first_seen"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
):
    cfg = _cfg(request)
    return _with_connection(
        cfg,
        lambda conn: data.query_jobs(
            conn,
            cfg,
            page=page,
            page_size=page_size,
            q=q,
            countries=country,
            statuses=job_status,
            decision=decision,
            min_score=min_score,
            max_score=max_score,
            kind=kind,
            sort=sort,
            direction=direction,
        ),
    )


@router.get("/jobs/{job_id:path}")
def get_job(request: Request, job_id: str):
    cfg = _cfg(request)
    result = _with_connection(cfg, lambda conn: data.job_detail(conn, cfg, job_id))
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return result


@router.put("/jobs/{job_id:path}/decision")
def update_decision(request: Request, job_id: str, body: DecisionRequest):
    cfg = _cfg(request)

    def update(conn):
        if not db.set_decision(conn, job_id, body.decision, body.note):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        conn.commit()
        return data.job_detail(conn, cfg, job_id)

    return _with_connection(cfg, update)


@router.get("/analytics/usage")
def get_usage(request: Request, days: int = Query(30, ge=1, le=365)):
    cfg = _cfg(request)
    return _with_connection(cfg, lambda conn: data.usage(conn, days))


@router.get("/analytics/activity")
def get_activity(request: Request, days: int = Query(14, ge=2, le=90)):
    cfg = _cfg(request)
    return _with_connection(cfg, lambda conn: data.activity(conn, days))


@router.get("/runs")
def get_runs(request: Request, limit: int = Query(30, ge=1, le=200)):
    cfg = _cfg(request)
    return {"items": data.run_history(cfg, limit), "runner": request.app.state.runner.snapshot()}


@router.get("/logs")
def get_logs(request: Request, lines: int = Query(100, ge=1, le=1000)):
    return {"items": data.log_tail(_cfg(request), lines)}


@router.get("/exports")
def get_exports(request: Request):
    return {"items": data.exports(_cfg(request))}


@router.get("/exports/{name}")
def download_export(request: Request, name: str):
    cfg = _cfg(request)
    directory = cfg.path(cfg.app.exports_dir).resolve()
    path = (directory / name).resolve()
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found") from exc
    if path.suffix.lower() != ".csv" or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    return FileResponse(path, filename=path.name, media_type="text/csv")


@router.get("/settings")
def get_settings(request: Request):
    cfg = _cfg(request)
    return {**settings_summary(cfg), "database": data.database_info(cfg)}


@router.get("/settings/config/{name}")
def get_config_file(request: Request, name: str):
    try:
        return read_config_file(_root(request), name)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Config file not found") from exc


@router.put("/settings/config/{name}")
def update_config_file(request: Request, name: str, body: ConfigFileRequest):
    try:
        return save_config_file(_root(request), name, body.content)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Config file not found") from exc
    except ConfigSaveError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/settings/config/{name}/validate")
def validate_config(name: str, body: ConfigFileRequest):
    try:
        return validate_config_file(name, body.content)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Config file not found") from exc
    except ConfigSaveError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/profile/cv/preview")
async def preview_cv(request: Request, file: Annotated[UploadFile, File()]):
    payload = await file.read(5 * 1024 * 1024 + 1)
    try:
        document = request.app.state.cv_store.add(file.filename or "cv", file.content_type, payload)
    except CvImportError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return document.preview()


@router.post("/profile/cv/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_cv_profile(request: Request, body: CvGenerateRequest):
    if not body.consent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Confirm that the redacted CV text may be sent to the configured LLM provider",
        )
    root = _root(request)

    def task():
        document = request.app.state.cv_store.take(body.token)
        cfg = load_config(root)
        conn = db.connect(cfg.db_path)
        try:
            return generate_profile_draft(cfg, conn, document)
        finally:
            conn.close()

    return _launch(request, "cv-profile", task)


@router.post("/profile/cv/apply")
def apply_cv_profile(request: Request, body: ProfileUpdate):
    cfg = _cfg(request)
    try:
        result = apply_profile(cfg, body)
    except (CvImportError, ConfigSaveError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    updated = load_config(_root(request))
    conn = db.connect(updated.db_path)
    try:
        rescored = export.rescore(conn, updated)
        active = updated.searches.active_codes
        if active:
            placeholders = ",".join("?" for _ in active)
            pending = conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE status='pending' AND country IN ({placeholders})",
                active,
            ).fetchone()[0]
        else:
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    finally:
        conn.close()
    return {**result, "rescored": rescored, "pending": pending}


def _launch(request: Request, name: str, func: Callable[[], Any]):
    run = request.app.state.runner
    if not run.start(name, func):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{run.snapshot().get('task') or 'Another operation'} is already running",
        )
    return run.snapshot()


@router.get("/operations/status")
def operation_status(request: Request):
    return request.app.state.runner.snapshot()


@router.post("/operations/ingest", status_code=status.HTTP_202_ACCEPTED)
def start_ingest(request: Request, body: IngestRequest):
    root = _root(request)

    def task():
        cfg = load_config(root)
        if body.queries:
            cfg.searches.queries = body.queries
            cfg.searches.rotating_queries = []
            cfg.searches.jobspy.since_last_run = False
        if body.countries:
            wanted = {country.upper() for country in body.countries}
            cfg.searches.active_countries = sorted(wanted)
            cfg.searches.countries = [
                country for country in cfg.searches.countries if country.code.upper() in wanted
            ]
        if body.limit:
            cfg.searches.jobspy.results_wanted = body.limit
        conn = db.connect(cfg.db_path)
        try:
            context = make_context(cfg, db.known_ids(conn))
            adapters = build_adapters(
                cfg, context, only=set(body.sources) if body.sources else None
            )
            return run_ingest(conn, cfg, adapters)
        finally:
            conn.close()

    return _launch(request, "ingest", task)


@router.post("/operations/evaluate", status_code=status.HTTP_202_ACCEPTED)
def start_evaluate(request: Request, body: EvaluateRequest):
    root = _root(request)

    def task():
        cfg = load_config(root)
        conn = db.connect(cfg.db_path)
        try:
            return build_evaluator(cfg, conn).run(body.limit)
        finally:
            conn.close()

    return _launch(request, "evaluate", task)


@router.post("/operations/evaluate-all", status_code=status.HTTP_202_ACCEPTED)
def start_evaluate_all(request: Request, body: EvaluateAllRequest):
    root = _root(request)

    def task():
        cfg = load_config(root)
        conn = db.connect(cfg.db_path)
        total = {"candidates": 0, "evaluated": 0, "failed": 0, "batches": 0, "stopped": None}
        try:
            while total["evaluated"] + total["failed"] < body.max_jobs:
                remaining = body.max_jobs - total["evaluated"] - total["failed"]
                summary = build_evaluator(cfg, conn).run(remaining)
                total["batches"] += 1
                total["candidates"] += summary["candidates"]
                total["evaluated"] += summary["evaluated"]
                total["failed"] += summary["failed"]
                if summary.get("stopped"):
                    total["stopped"] = summary["stopped"]
                    break
                if summary["candidates"] == 0 or summary["evaluated"] == 0:
                    break
            return total
        finally:
            conn.close()

    return _launch(request, "evaluate-all", task)


@router.post("/operations/daily", status_code=status.HTTP_202_ACCEPTED)
def start_daily(request: Request, body: DailyRequest):
    root = _root(request)

    def task():
        cfg = load_config(root)
        conn = db.connect(cfg.db_path)
        try:
            result = run_daily(
                cfg,
                conn,
                skip_ingest=body.skip_ingest,
                skip_evaluate=body.skip_evaluate,
                eval_limit=body.eval_limit,
            )
            append_run_summary(cfg, result)
            return result
        finally:
            conn.close()

    return _launch(request, "run-daily", task)


@router.post("/operations/reprefilter", status_code=status.HTTP_202_ACCEPTED)
def start_reprefilter(request: Request):
    root = _root(request)

    def task():
        cfg = load_config(root)
        return _with_connection(
            cfg,
            lambda conn: db.reprefilter(conn, Prefilterer(cfg.prefilter).run, cfg.app.stale_days),
        )

    return _launch(request, "reprefilter", task)


@router.post("/operations/rescore", status_code=status.HTTP_202_ACCEPTED)
def start_rescore(request: Request):
    root = _root(request)

    def task():
        cfg = load_config(root)
        return {"rescored": _with_connection(cfg, lambda conn: export.rescore(conn, cfg))}

    return _launch(request, "rescore", task)


@router.post("/operations/dedup", status_code=status.HTTP_202_ACCEPTED)
def start_dedup(request: Request):
    root = _root(request)

    def task():
        cfg = load_config(root)
        return _with_connection(cfg, lambda conn: db.backfill_dedup(conn, cfg.app.dedup))

    return _launch(request, "dedup", task)


@router.post("/operations/backup", status_code=status.HTTP_202_ACCEPTED)
def start_backup(request: Request):
    root = _root(request)

    def task():
        cfg = load_config(root)
        return _with_connection(cfg, lambda conn: backup_db(cfg, conn))

    return _launch(request, "backup", task)


@router.post("/operations/export", status_code=status.HTTP_202_ACCEPTED)
def start_export(request: Request, body: ExportRequest):
    root = _root(request)

    def task():
        cfg = load_config(root)

        def perform(conn):
            path, count = export.export_csv(
                conn,
                cfg,
                mode=body.mode,
                day=body.day,
                min_score=body.min_score,
            )
            return {"path": str(path), "jobs": count}

        return _with_connection(cfg, perform)

    return _launch(request, "export", task)
