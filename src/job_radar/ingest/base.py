"""Interfaz común de adapters y runner que aísla fallas por fuente."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from job_radar.models import RawJob

log = logging.getLogger(__name__)


class RateLimited(Exception):
    """La fuente respondió 429 (o equivalente) de forma persistente."""


class Adapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawJob]: ...


@dataclass
class SourceResult:
    name: str
    jobs: list[RawJob] = field(default_factory=list)
    error: str | None = None
    aborted: bool = False  # abortada por rate limit
    warnings: list[str] = field(default_factory=list)  # trajo datos, pero incompletos
    searches: list[dict] | None = None  # resultado por búsqueda (solo JobSpy)


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Backoff exponencial con full jitter: uniforme en [base/2, min(cap, base*2^attempt)]."""
    upper = min(cap, base * (2**attempt))
    return random.uniform(min(base / 2, upper), upper)


def with_backoff(
    fn: Callable[[], list],
    *,
    is_rate_limited: Callable[[], bool],
    max_retries: int,
    base_s: float,
    max_s: float,
    label: str,
    sleep: Callable[[float], None] = time.sleep,
) -> list:
    """Llama fn() y reintenta con backoff mientras reporte rate limit; agotado -> RateLimited."""
    for attempt in range(max_retries + 1):
        result = fn()
        if not is_rate_limited():
            return result
        if attempt == max_retries:
            break
        delay = backoff_delay(attempt, base_s, max_s)
        log.warning(
            "%s: rate limited, reintento %d/%d en %.0fs", label, attempt + 1, max_retries, delay
        )
        sleep(delay)
    raise RateLimited(label)


def run_adapters(adapters: Iterable[Adapter]) -> list[SourceResult]:
    """Ejecuta cada adapter por separado. Ninguna excepción cruza la frontera de la fuente."""
    results = []
    for adapter in adapters:
        res = SourceResult(name=adapter.name)
        try:
            for job in adapter.fetch():
                res.jobs.append(job)
        except RateLimited as e:
            res.aborted, res.error = True, f"rate limited: {e}"
            log.error("%s abortada por rate limit (%d jobs parciales)", adapter.name, len(res.jobs))
        except Exception as e:  # noqa: BLE001 — una fuente rota nunca tira la corrida
            res.error = f"{type(e).__name__}: {e}"
            log.exception("%s falló (%d jobs parciales)", adapter.name, len(res.jobs))
        res.warnings = list(getattr(adapter, "warnings", []))
        if (searches := getattr(adapter, "searches", None)) is not None:
            res.searches = list(searches)
        results.append(res)
    return results
