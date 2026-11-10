"""Corrida diaria: ingest -> evaluate -> export, con un resumen único por corrida.

Cada paso está aislado: si uno falla, se registra el error y los siguientes corren igual
(ej. si LinkedIn está caído igual se evalúa lo pendiente y se exporta).
"""

from __future__ import annotations

import logging
import os
import re
import socket
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from job_radar import db
from job_radar.backup import backup_db
from job_radar.config import Config, NetworkSettings
from job_radar.export import export_csv
from job_radar.ingest.pipeline import (
    RETRY_STEP,
    build_adapters,
    linkedin_retry_adapter,
    make_context,
)
from job_radar.ingest.pipeline import ingest as run_ingest
from job_radar.logs import full_run_today

log = logging.getLogger("job_radar.run")


def wait_for_network(
    net: NetworkSettings,
    *,
    resolve: Callable[[str], object] = lambda host: socket.getaddrinfo(host, 443),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """True cuando todos los `check_hosts` resuelven por DNS; False si no pasa en `wait_s`."""
    deadline = clock() + net.wait_s
    while True:
        pending = []
        for host in net.check_hosts:
            try:
                resolve(host)
            except OSError:
                pending.append(host)
        if not pending:
            return True
        if clock() >= deadline:
            log.warning("sin red: no resuelven %s", ", ".join(pending))
            return False
        sleep(net.poll_s)


class NetworkErrorCounter(logging.Handler):
    """Cuenta los ERROR del log que son falta de red. JobSpy no lanza excepción ante un error
    de conexión (solo lo loguea y devuelve 0 resultados), así que mirar las fuentes caídas no
    alcanza para saber si el ingest se hizo con red."""

    def __init__(self, patterns: str):
        super().__init__(level=logging.ERROR)
        self.rx = re.compile(patterns, re.IGNORECASE)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            text += f" {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
        if self.rx.search(text):
            self.count += 1


def _safe(fn: Callable):
    try:
        return fn()
    except Exception:  # noqa: BLE001 — armar el reintento nunca rompe el rescate
        log.exception("no pude armar el reintento de LinkedIn")
        return None


def _step(summary: dict, name: str, fn: Callable[[], dict]) -> None:
    t0 = time.monotonic()
    try:
        summary["steps"][name] = fn()
    except Exception as e:  # noqa: BLE001 — un paso roto no corta la corrida
        log.exception("paso %s falló", name)
        summary["steps"][name] = {"error": f"{type(e).__name__}: {e}"}
    summary["steps"][name]["seconds"] = round(time.monotonic() - t0, 1)


def run_daily(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    skip_ingest: bool = False,
    skip_evaluate: bool = False,
    eval_limit: int | None = None,
    evaluator_factory: Callable | None = None,
    retry_factory: Callable = linkedin_retry_adapter,
    network_ok: Callable[[NetworkSettings], bool] = wait_for_network,
) -> dict:
    summary: dict = {
        "run_id": uuid.uuid4().hex[:8],
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "steps": {},
    }
    t0 = time.monotonic()
    if skip_ingest:
        # El rescate de las 13:00 es lo único que corre seguro con la Mac dormida: launchd no
        # lanza la corrida diaria en un despertar oscuro, pero sí un horario fijo.
        summary["no_search_today"] = not full_run_today(cfg)

    def counted_ingest(adapters) -> dict:
        counter = NetworkErrorCounter(cfg.app.network.error_patterns)
        logging.getLogger().addHandler(counter)
        try:
            result = run_ingest(conn, cfg, adapters)
        finally:
            logging.getLogger().removeHandler(counter)
        result["network_errors"] = counter.count
        if counter.count:
            log.warning("ingest con %d errores de red", counter.count)
        return result

    if not skip_ingest:
        _step(
            summary,
            "ingest",
            lambda: counted_ingest(build_adapters(cfg, make_context(cfg, db.known_ids(conn)))),
        )
    elif (retry := _safe(lambda: retry_factory(cfg, conn))) is not None:
        # Rescate: LinkedIn cortó búsquedas a la mañana; se retoman ahora. Va como paso aparte
        # (no "ingest") para que no cuente como corrida completa ni como corrida limpia.
        if network_ok(cfg.app.network):
            _step(summary, RETRY_STEP, lambda: counted_ingest([retry]))
        else:
            log.info("rescate de LinkedIn: sin red, no se reintenta")

    if not skip_evaluate:

        def do_evaluate() -> dict:
            if evaluator_factory is None:
                from job_radar.evaluate_gemini import GeminiEvaluator

                ev = GeminiEvaluator(cfg, conn)
            else:
                ev = evaluator_factory(cfg, conn)
            return ev.run(eval_limit)

        _step(summary, "evaluate", do_evaluate)

    def do_export() -> dict:
        # Lo que va a salir en el CSV (ordenado por score), antes de que export lo marque.
        top = db.current_evaluations(
            conn,
            cfg.scoring.evaluator_priority,
            min_score=cfg.scoring.threshold,
            unexported_only=True,
            countries=cfg.searches.active_codes,
        )[: cfg.app.ntfy.top_n]
        path, n = export_csv(conn, cfg, mode="new")
        return {
            "path": str(path),
            "shortlist": n,
            "top": [
                {"score": r["e_score"], "company": r["company"], "title": r["title"]} for r in top
            ],
        }

    _step(summary, "export", do_export)

    if cfg.app.backup.enabled:
        _step(summary, "backup", lambda: backup_db(cfg, conn))

    ing = summary["steps"].get("ingest") or summary["steps"].get(RETRY_STEP) or {}
    summary["totals"] = {
        "new": ing.get("new", 0),
        "seen": ing.get("seen", 0),
        "duplicates": ing.get("duplicate", 0),
        "excluded_prefilter": ing.get("excluded_prefilter", 0),
        "evaluated": summary["steps"].get("evaluate", {}).get("evaluated", 0),
        "shortlist": summary["steps"]["export"].get("shortlist", 0),
        "failed_sources": sorted(
            name for name, s in ing.get("sources", {}).items() if s.get("error")
        ),
        "failed_steps": sorted(n for n, s in summary["steps"].items() if "error" in s),
        "partial_sources": {
            name: s["warnings"] for name, s in ing.get("sources", {}).items() if s.get("warnings")
        },
    }
    summary["seconds"] = round(time.monotonic() - t0, 1)
    log.info(
        "corrida %s: %d nuevos, %d duplicados, %d excluidos por prefiltro, %d evaluados, "
        "%d en shortlist",
        summary["run_id"],
        summary["totals"]["new"],
        summary["totals"]["duplicates"],
        summary["totals"]["excluded_prefilter"],
        summary["totals"]["evaluated"],
        summary["totals"]["shortlist"],
        extra={"run_summary": summary["totals"], "run_id": summary["run_id"]},
    )
    if cfg.app.notify_on_error and (alert := error_alert(summary)):
        notify_macos(alert)
    if cfg.app.ntfy.enabled and (msg := ntfy_message(summary, cfg.app.ntfy.notify_success)):
        send_ntfy(cfg, msg)
    return summary


def ntfy_message(summary: dict, notify_success: bool = True) -> dict | None:
    """Push del resumen: falla grave (prioridad alta), corrida con problemas o CSV listo.
    None si no hay nada que contar (ej. el rescate de las 13:00 sin novedades)."""
    totals = summary.get("totals") or {}
    steps = summary.get("steps") or {}
    ing = steps.get("ingest") or steps.get(RETRY_STEP)
    n = totals.get("shortlist", 0)
    top = [
        f"{t['score']:.0f} · {t['company'] or '?'} · {t['title']}"
        for t in (steps.get("export") or {}).get("top", [])
    ]
    counts = f"{n} ofertas nuevas en el CSV"
    if ing:
        counts += f" (de {totals.get('new', 0)} avisos nuevos)"

    if alert := error_alert(summary):
        return {
            "title": "Job Radar: falló la corrida",
            "message": "\n".join([alert, counts]),
            "priority": 4,
            "tags": ["rotating_light"],
        }
    if summary.get("no_search_today"):
        return {
            "title": "Job Radar: hoy no hubo búsqueda",
            "message": "Todavía no corrió la búsqueda completa del día. "
            "Va a correr cuando la Mac esté despierta y con red.",
            "priority": 4,
            "tags": ["warning"],
        }
    problems = []
    if failed := totals.get("failed_sources"):
        problems.append(f"Fuentes caídas: {', '.join(failed)}")
    for name, warnings in (totals.get("partial_sources") or {}).items():
        problems.append(f"{name}: {'; '.join(warnings)}")
    if ing and (net := ing.get("network_errors")):
        problems.append(f"{net} errores de red: se reintenta en media hora")
    if problems:
        return {
            "title": "Job Radar: CSV con problemas",
            "message": "\n".join([counts, *problems, *top]),
            "priority": 3,
            "tags": ["warning"],
        }
    if not notify_success or (not ing and n == 0):
        return None
    return {
        "title": "Job Radar: CSV listo",
        "message": "\n".join([counts, *top]),
        "priority": 3,
        "tags": ["white_check_mark"],
    }


def send_ntfy(
    cfg: Config,
    msg: dict,
    post: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Publica en ntfy (JSON, así título y texto pueden tener acentos). Reintenta `retries`
    veces y nunca lanza excepción."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        log.warning("ntfy activado pero falta NTFY_TOPIC en .env")
        return False
    nt = cfg.app.ntfy
    for attempt in range(1, nt.retries + 1):
        try:
            resp = (post or httpx.post)(
                nt.server.rstrip("/"), json={"topic": topic, **msg}, timeout=nt.timeout_s
            )
            resp.raise_for_status()
            return True
        except Exception as e:  # noqa: BLE001 — el aviso nunca rompe la corrida
            log.warning("no pude mandar el push de ntfy (intento %d): %s", attempt, e)
            if attempt < nt.retries:
                sleep(nt.retry_wait_s)
    return False


def error_alert(summary: dict) -> str | None:
    """Mensaje corto si la corrida tuvo un problema importante; None si salió bien."""
    totals = summary.get("totals") or {}
    if failed := totals.get("failed_steps"):
        return f"Falló el paso: {', '.join(failed)}"
    if stopped := (summary.get("steps", {}).get("evaluate") or {}).get("stopped"):
        return f"Evaluación cortada: {stopped[:100]}"
    return None


def notify_macos(message: str, title: str = "Job Radar") -> bool:
    """Muestra una notificación nativa en macOS vía osascript. Nunca lanza excepción."""
    esc = message.replace("\\", "\\\\").replace('"', '\\"')
    t_esc = title.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc}" with title "{t_esc}"'
    try:
        res = subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=5, check=False
        )
        return res.returncode == 0
    except OSError as e:
        log.warning("no pude mostrar notificación: %s", e)
        return False
