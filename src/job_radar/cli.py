"""CLI de job-radar."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import typer

from job_radar import db
from job_radar.config import load_config
from job_radar.logs import append_run_summary, setup_logging

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Buscador de avisos CH/NL/LU.")
log = logging.getLogger("job_radar.run")


def _echo(data) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


@app.command()
def ingest(
    source: list[str] = typer.Option(
        None, "--source", "-s", help="Ej: linkedin, swissdevjobs, ats, greenhouse:getyourguide"
    ),
    query: list[str] = typer.Option(None, "--query", "-q", help="Reemplaza las queries del YAML"),
    country: list[str] = typer.Option(None, "--country", "-c", help="Filtra países: CH, NL, LU"),
    limit: int = typer.Option(None, "--limit", help="results_wanted por llamada"),
    companies: Path = typer.Option(None, "--companies", help="companies.yaml alternativo"),
    verbose: bool = False,
):
    """Ingesta las fuentes configuradas, aplica el pre-filtro y deduplica."""
    from job_radar.ingest.pipeline import build_adapters, make_context
    from job_radar.ingest.pipeline import ingest as run_ingest

    cfg = load_config(companies_file=companies)
    setup_logging(cfg, verbose)
    if query:
        cfg.searches.queries = query
        cfg.searches.rotating_queries = []
        cfg.searches.jobspy.since_last_run = False
    if country:
        wanted = {c.upper() for c in country}
        cfg.searches.countries = [c for c in cfg.searches.countries if c.code in wanted]
    if limit:
        cfg.searches.jobspy.results_wanted = limit
    conn = db.connect(cfg.db_path)
    ctx = make_context(cfg, db.known_ids(conn))
    summary = run_ingest(conn, cfg, build_adapters(cfg, ctx, set(source) if source else None))
    log.info("ingest", extra={"ingest_summary": summary})
    _echo(summary)


@app.command()
def evaluate(
    limit: int = typer.Option(None, "--limit", "-n", help="Máximo de jobs a evaluar"),
    verbose: bool = False,
):
    """Evalúa jobs pendientes con Gemini y calcula el score."""
    from job_radar.evaluate_gemini import GeminiEvaluator

    cfg = load_config()
    setup_logging(cfg, verbose)
    summary = GeminiEvaluator(cfg, db.connect(cfg.db_path)).run(limit)
    log.info("evaluate", extra={"evaluate_summary": summary})
    _echo(summary)


@app.command()
def rescore():
    """Recalcula todos los scores con config/scoring.yaml (sin llamar al LLM)."""
    from job_radar.export import rescore as run_rescore

    cfg = load_config()
    typer.echo(f"{run_rescore(db.connect(cfg.db_path), cfg)} evaluaciones recalculadas")


@app.command()
def export(
    all_: bool = typer.Option(
        False, "--all", help="Ranking histórico completo (exports/ranking_all.csv)"
    ),
    day: str = typer.Option(
        None, "--day", help="Solo lo evaluado ese día, YYYY-MM-DD (exports/evaluated_<día>.csv)"
    ),
    min_score: float = typer.Option(None, "--min-score", help="Default: threshold de scoring.yaml"),
):
    """Exporta el CSV de mejores ofertas. Por defecto: solo las que no salieron en reportes previos."""
    from job_radar.export import export_csv

    if all_ and day:
        raise typer.BadParameter("usá --all o --day, no los dos")
    mode = "all" if all_ else "day" if day else "new"
    cfg = load_config()
    path, n = export_csv(
        db.connect(cfg.db_path),
        cfg,
        mode=mode,
        day=date.fromisoformat(day) if day else None,
        min_score=min_score,
    )
    typer.echo(f"{n} jobs -> {path}")


@app.command("run-daily")
def run_daily(
    skip_ingest: bool = typer.Option(False, help="No ingestar (solo evaluar y exportar)"),
    skip_evaluate: bool = typer.Option(False, help="No llamar a Gemini"),
    eval_limit: int = typer.Option(None, "--eval-limit", help="Máximo de jobs a evaluar"),
    log_console: bool = typer.Option(
        True, help="Logs también por consola (launchd usa --no-log-console: solo archivo rotado)"
    ),
    once_per_day: bool = typer.Option(
        False, help="No hace nada si hoy ya hubo una corrida completa (launchd la reintenta)"
    ),
    not_before: str = typer.Option(
        None, "--not-before", help="Con --once-per-day: no corre antes de esta hora local (HH:MM)"
    ),
    verbose: bool = False,
):
    """Corrida completa: ingest, evaluate y export. La usa launchd una vez por día."""
    from datetime import datetime, time

    from job_radar.daily import run_daily as do_run
    from job_radar.daily import wait_for_network
    from job_radar.logs import daily_run_done

    cfg = load_config()
    setup_logging(cfg, verbose, console=log_console)
    if once_per_day:
        # launchd la lanza cada tanto; así corre apenas la Mac está despierta, una vez por día.
        if not_before and datetime.now().time() < time.fromisoformat(not_before):
            log.debug("run-daily: todavía no son las %s", not_before)
            return
        if daily_run_done(cfg):
            log.debug("run-daily: hoy ya hubo una corrida completa")
            return
        if not skip_ingest and not wait_for_network(cfg.app.network):
            log.info("run-daily: sin red, se reintenta en la próxima pasada de launchd")
            return
    summary = do_run(
        cfg,
        db.connect(cfg.db_path),
        skip_ingest=skip_ingest,
        skip_evaluate=skip_evaluate,
        eval_limit=eval_limit,
    )
    append_run_summary(cfg, summary)
    _echo(summary["totals"] | {"csv": summary["steps"]["export"].get("path")})
    if summary["totals"]["failed_steps"]:
        raise typer.Exit(code=1)


@app.command()
def backup():
    """Respalda jobs.db, comprimida, en las carpetas espejo (lo hace run-daily en cada corrida)."""
    from job_radar.backup import backup_db

    cfg = load_config()
    setup_logging(cfg)
    _echo(backup_db(cfg, db.connect(cfg.db_path)))


@app.command()
def reprefilter():
    """Re-aplica el pre-filtro (config actual) a los jobs guardados, sin red ni LLM."""
    from job_radar.prefilter import Prefilterer

    cfg = load_config()
    _echo(
        db.reprefilter(db.connect(cfg.db_path), Prefilterer(cfg.prefilter).run, cfg.app.stale_days)
    )


@app.command()
def dedup():
    """Completa huellas de deduplicación faltantes y re-deduplica la base (sin red ni LLM)."""
    cfg = load_config()
    _echo(db.backfill_dedup(db.connect(cfg.db_path), cfg.app.dedup))


@app.command()
def decide(
    job_id: str = typer.Argument(..., help="Id del job, ej. linkedin:li-4479544148"),
    decision: str = typer.Argument(..., help="applied | discarded | clear"),
    note: str = typer.Option(None, "--note", help="Nota opcional (ej. 'mandé CV + carta')"),
):
    """Registra que aplicaste o descartaste una oferta (o lo borra con 'clear')."""
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    if not db.set_decision(conn, job_id, None if decision == "clear" else decision, note):
        raise typer.BadParameter(f"no existe el job {job_id}")
    conn.commit()
    typer.echo(f"{job_id}: {decision}")


@app.command()
def applications():
    """Ofertas a las que aplicaste, las más recientes primero."""
    cfg = load_config()
    rows = db.decided_jobs(db.connect(cfg.db_path), "applied")
    if not rows:
        typer.echo("Todavía no registraste ninguna postulación.")
    for r in rows:
        note = f"  ({r['decision_note']})" if r["decision_note"] else ""
        typer.echo(f"{r['decided_at'][:10]}  {r['title'][:45]:45}  {r['company'] or '':25}{note}")
        typer.echo(f"            {r['url']}")


@app.command()
def usage(days: int = typer.Option(7, help="Días hacia atrás")):
    """Requests y tokens de Gemini por día y modelo (para controlar el gasto)."""
    cfg = load_config()
    rows = db.llm_usage(db.connect(cfg.db_path), days)
    typer.echo(
        f"{'día':10}  {'modelo':24} {'req':>5} {'ok':>5} {'tokens in':>10} {'tokens out':>10}"
    )
    for r in rows:
        typer.echo(
            f"{r['day']:10}  {r['model']:24} {r['requests']:>5} {r['ok']:>5} "
            f"{r['tokens_in']:>10,} {r['tokens_out']:>10,}"
        )
    typer.echo("Costo real: https://aistudio.google.com/usage (o la consola de Google Cloud).")


@app.command("mcp-config")
def mcp_config():
    """Imprime el bloque para claude_desktop_config.json (servidor MCP de job-radar)."""
    from job_radar.mcp_server import desktop_config

    path = "~/Library/Application Support/Claude/claude_desktop_config.json"
    typer.echo(
        f"Agregá esto dentro de {path} (combinalo con mcpServers si ya existe).\n"
        "IMPORTANTE: editalo con Claude Desktop CERRADO (Cmd+Q). La app reescribe ese archivo\n"
        "con su copia en memoria al cerrarse y borra los cambios hechos mientras corría.\n"
    )
    _echo(desktop_config(load_config()))


@app.command()
def stats():
    """Conteos por status y fuente, evaluaciones por evaluador y leases activos."""
    from job_radar.mcp_server import tool_get_stats

    cfg = load_config()
    _echo(tool_get_stats(cfg, db.connect(cfg.db_path)))


if __name__ == "__main__":
    app()
