"""Logging: consola legible + archivo JSON con rotación + resumen por corrida (runs.jsonl)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from job_radar.config import Config

_STD_ATTRS = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento. Los `extra=` del log se agregan como campos."""

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out |= {k: v for k, v in vars(record).items() if k not in _STD_ATTRS}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(
    cfg: Config, verbose: bool = False, to_file: bool = True, console: bool = True
) -> None:
    lc = cfg.app.logging
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if verbose else getattr(logging, lc.level.upper(), logging.INFO))

    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(ch)

    if to_file:
        logs_dir = cfg.path(cfg.app.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            logs_dir / lc.file, maxBytes=lc.max_bytes, backupCount=lc.backup_count, encoding="utf-8"
        )
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)

    for name in lc.quiet:
        logging.getLogger(name).setLevel(logging.WARNING)
    # JobSpy usa loggers "JobSpy:<sitio>" sin jerarquía: se silencian uno por uno.
    for name in list(logging.root.manager.loggerDict):
        if any(name.startswith(q) for q in lc.quiet):
            logging.getLogger(name).setLevel(logging.WARNING)


def _is_full_run(summary: dict) -> bool:
    """Corrida con ingest hecho con red: el paso no falló, al menos una fuente respondió y no
    hubo errores de red en el log (ej. la Mac en un despertar oscuro, con wifi intermitente)."""
    ing = (summary.get("steps") or {}).get("ingest")
    if not ing or "error" in ing or ing.get("network_errors"):
        return False
    return any(not s.get("error") for s in (ing.get("sources") or {}).values())


def _runs(cfg: Config) -> list[tuple[datetime, dict]]:
    """(inicio en hora local, resumen) de todas las corridas de runs.jsonl."""
    path = cfg.path(cfg.app.logs_dir) / cfg.app.logging.runs_file
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            summary = json.loads(line)
            started = datetime.fromisoformat(summary["started"]).astimezone()
        except (ValueError, KeyError, TypeError):
            continue
        runs.append((started, summary))
    return runs


def _ingests(cfg: Config) -> list[tuple[datetime, dict]]:
    """Las corridas que intentaron el ingest."""
    return [(t, s) for t, s in _runs(cfg) if "ingest" in (s.get("steps") or {})]


def _ingests_today(cfg: Config, now: datetime | None = None) -> list[dict]:
    """Resúmenes de las corridas de hoy (fecha local) que intentaron el ingest."""
    today = (now or datetime.now().astimezone()).date()
    return [s for started, s in _ingests(cfg) if started.date() == today]


def last_clean_ingest(cfg: Config, source: str) -> datetime | None:
    """Inicio de la última corrida en que `source` trajo su listado entero: sin error, sin
    errores de red y sin búsquedas cortadas por 429. None si no hay ninguna."""
    clean = []
    for started, s in _ingests(cfg):
        ing = s["steps"]["ingest"]
        src = (ing.get("sources") or {}).get(source)
        if (
            not src
            or "error" in ing
            or ing.get("network_errors")
            or src.get("error")
            or any("cortada" in w for w in src.get("warnings") or [])
        ):
            continue
        clean.append(started)
    return max(clean, default=None)


def source_today(cfg: Config, source: str, now: datetime | None = None) -> dict | None:
    """Resumen de `source` en la última corrida de hoy que la ingestó (None si no hubo)."""
    for s in reversed(_ingests_today(cfg, now)):
        if (src := (s["steps"]["ingest"].get("sources") or {}).get(source)) is not None:
            return src
    return None


def step_ran_today(cfg: Config, step: str, now: datetime | None = None) -> bool:
    today = (now or datetime.now().astimezone()).date()
    return any(t.date() == today and step in (s.get("steps") or {}) for t, s in _runs(cfg))


def full_run_today(cfg: Config, now: datetime | None = None) -> bool:
    return any(_is_full_run(s) for s in _ingests_today(cfg, now))


def daily_run_done(cfg: Config, now: datetime | None = None) -> bool:
    """True si hoy ya hubo una corrida completa, o si ya se intentó el ingest
    `network.max_daily_attempts` veces (para no reintentar todo el día si una fuente está caída)."""
    runs = _ingests_today(cfg, now)
    return any(_is_full_run(s) for s in runs) or len(runs) >= cfg.app.network.max_daily_attempts


def append_run_summary(cfg: Config, summary: dict) -> None:
    logs_dir = cfg.path(cfg.app.logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    with (logs_dir / cfg.app.logging.runs_file).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
