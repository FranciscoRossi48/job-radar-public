"""Adapter JobSpy: LinkedIn (endpoints públicos guest), Indeed y Google Jobs.

Nunca usa cookies ni credenciales. JobSpy no lanza excepción ante un 429: lo loguea y
devuelve lo que tenga. Por eso escuchamos sus loggers para detectar el rate limit.
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime

from job_radar.config import CountrySearch, JobSpySettings
from job_radar.ingest.base import RateLimited, with_backoff
from job_radar.models import RawJob

log = logging.getLogger(__name__)

_RATE_LIMIT_RX = re.compile(r"\b429\b|too many requests|blocked|throttl|rate.?limit", re.I)


class _RateLimitSniffer(logging.Handler):
    """Captura mensajes de los loggers 'JobSpy:*' que indican rate limit."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.hit = False

    def emit(self, record: logging.LogRecord) -> None:
        if _RATE_LIMIT_RX.search(record.getMessage()):
            self.hit = True


def _jobspy_loggers() -> list[logging.Logger]:
    names = [n for n in logging.root.manager.loggerDict if n.startswith("JobSpy")]
    return [logging.getLogger(n) for n in names]


def _adopt_jobspy_loggers() -> None:
    """JobSpy pone su propio StreamHandler y propagate=False: sus mensajes saltean nuestro
    logging (y bajo launchd terminan en launchd.err.log, que no rota). Los reconectamos al
    logger raíz para que vayan al log JSON rotado."""
    for lg in _jobspy_loggers():
        for h in list(lg.handlers):
            if type(h) is logging.StreamHandler:
                lg.removeHandler(h)
        lg.propagate = True


def _default_scrape(**kwargs):
    from jobspy import scrape_jobs  # import diferido: pandas/numpy tardan en cargar

    return scrape_jobs(**kwargs)


def _linkedin_details() -> Callable[[str], str | None]:
    """Descripción de un aviso por id, con el mismo parser de JobSpy (ya usado en el listado).
    `_fetch_details` es interno de JobSpy: la versión está fijada en uv.lock."""
    from types import SimpleNamespace

    from jobspy.linkedin import LinkedIn
    from jobspy.model import DescriptionFormat

    scraper = LinkedIn()
    scraper.scraper_input = SimpleNamespace(description_format=DescriptionFormat.MARKDOWN)
    return lambda job_id: scraper._fetch_details(job_id).get("description")


def _clean(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return None if s.lower() in {"", "nan", "none", "nat"} else s


def window_hours(settings: JobSpySettings, last: datetime | None, now: datetime) -> int | None:
    """Ventana de las búsquedas fijas: horas desde la última corrida limpia más el margen,
    entre since_last_run_min_h y hours_old. None (usar hours_old) si no hay corrida previa."""
    if not settings.since_last_run or last is None:
        return None
    since = math.ceil((now - last).total_seconds() / 3600) + settings.since_last_run_margin_h
    return max(settings.since_last_run_min_h, min(since, settings.hours_old))


@dataclass
class Search:
    """Una búsqueda (query, país). `offset`/`wanted` sirven para retomar una cortada por 429."""

    query: str
    country: CountrySearch
    hours: int
    offset: int = 0
    wanted: int | None = None


class JobSpyAdapter:
    """Una instancia por sitio (linkedin | indeed | google), para aislar fallas."""

    def __init__(
        self,
        site: str,
        settings: JobSpySettings,
        queries: list[str],
        countries: list[CountrySearch],
        scrape: Callable = _default_scrape,
        sleep: Callable[[float], None] = time.sleep,
        is_known: Callable[[str], bool] | None = None,
        is_relevant: Callable[[str], bool] | None = None,
        details: Callable[[str], str | None] | None = None,
        fixed_queries: list[str] | None = None,
        fixed_hours_old: int | None = None,
        plan: list[Search] | None = None,
        retry_details: list[RawJob] | None = None,
    ):
        self.site = site
        self.name = f"jobspy:{site}"
        self.s = settings
        self.queries = queries
        self.countries = countries
        self._scrape = scrape
        self._sleep = sleep
        self._is_known = is_known
        self._is_relevant = is_relevant or (lambda title: True)
        self._details = details
        # Con is_known, el listado va sin descripciones y se piden después solo las nuevas.
        self._lazy = site == "linkedin" and settings.linkedin_fetch_description and bool(is_known)
        self.warnings: list[str] = []  # corrida con datos, pero incompleta
        # Las búsquedas fijas usan una ventana más corta (ver window_hours).
        self._fixed = set(fixed_queries or [])
        self._fixed_hours = fixed_hours_old
        # Reintento del rescate: búsquedas explícitas y avisos a los que les falta la descripción.
        self._plan = plan
        self._retry_details = list(retry_details or [])
        # Resultado de cada búsqueda (queda en el resumen; el rescate retoma las cortadas).
        self.searches: list[dict] = []

    def hours_for(self, query: str) -> int:
        if self._fixed_hours and query in self._fixed:
            return self._fixed_hours
        return self.s.hours_old

    def plan(self) -> list[Search]:
        if self._plan is not None:
            return self._plan
        return [Search(q, c, self.hours_for(q)) for c in self.countries for q in self.queries]

    def _kwargs(self, search: Search) -> dict:
        query, c = search.query, search.country
        kw = dict(
            site_name=[self.site],
            search_term=query,
            hours_old=search.hours,
            results_wanted=search.wanted or self.s.results_wanted,
            description_format="markdown",
            verbose=1,
        )
        if search.offset:
            kw["offset"] = search.offset
        if self.site == "linkedin":
            kw |= dict(
                location=c.linkedin_location,
                linkedin_fetch_description=self.s.linkedin_fetch_description and not self._lazy,
            )
        elif self.site == "indeed":
            kw |= dict(country_indeed=c.indeed_country, location=c.indeed_location or None)
        elif self.site == "google":
            kw["google_search_term"] = self.s.google_template.format(
                query=query, location=c.google_location
            )
        return kw

    def _call(self, search: Search) -> list[dict]:
        query, c = search.query, search.country
        sniffer = _RateLimitSniffer()
        if self._scrape is _default_scrape:
            import jobspy  # noqa: F401 — crea los loggers 'JobSpy:*' antes de engancharnos

            _adopt_jobspy_loggers()

        def attempt() -> list[dict]:
            sniffer.hit = False
            for lg in _jobspy_loggers():
                if sniffer not in lg.handlers:
                    lg.addHandler(sniffer)
            try:
                df = self._scrape(**self._kwargs(search))
            except Exception as e:
                if _RATE_LIMIT_RX.search(str(e)):
                    sniffer.hit = True
                    return []
                raise
            rows = [] if df is None or len(df) == 0 else df.to_dict("records")
            # Si igual trajo resultados, no lo tratamos como rate limit, pero queda registrado.
            if rows:
                if sniffer.hit:
                    self.warnings.append(f"'{query}' cortada en {len(rows)} avisos por 429")
                sniffer.hit = False
            return rows

        try:
            return with_backoff(
                attempt,
                is_rate_limited=lambda: sniffer.hit,
                max_retries=self.s.backoff.max_retries,
                base_s=self.s.backoff.base_s,
                max_s=self.s.backoff.max_s,
                label=f"{self.name} '{query}' {c.code}",
                sleep=self._sleep,
            )
        finally:
            for lg in _jobspy_loggers():
                if sniffer in lg.handlers:
                    lg.removeHandler(sniffer)
            # JobSpy re-crea su StreamHandler en cada scrape_jobs si el logger quedó sin handlers.
            _adopt_jobspy_loggers()

    def _to_raw(self, row: dict, country: str) -> RawJob | None:
        job_id, url, title = (
            _clean(row.get("id")),
            _clean(row.get("job_url")),
            _clean(row.get("title")),
        )
        if not (job_id and url and title):
            return None
        posted = row.get("date_posted")
        return RawJob(
            source=self.site,
            job_id=job_id,
            url=_clean(row.get("job_url_direct")) or url,
            title=title,
            company=_clean(row.get("company")),
            location=_clean(row.get("location")),
            country=country,
            description=_clean(row.get("description")) or "",
            posted_at=posted if isinstance(posted, date) else None,
        )

    def _with_details(self, jobs: list[RawJob]) -> Iterator[RawJob]:
        """Completa la descripción de cada aviso. Los que queden sin ella salen igual: el prefiltro
        los marca `no_description` y se reintentan cuando vuelvan a aparecer en el listado."""
        details = self._details or _linkedin_details()
        failures = missing = 0
        for i, job in enumerate(jobs):
            if failures >= self.s.linkedin_detail_max_failures:
                missing += len(jobs) - i
                yield from jobs[i:]
                break
            if i:
                self._sleep(random.uniform(*self.s.linkedin_detail_delay_s))
            text = _clean(details(job.job_id.removeprefix("li-")))
            if text:
                failures = 0
                yield job.model_copy(update={"description": text})
            else:
                failures += 1
                missing += 1
                yield job
        if missing >= self.s.linkedin_missing_warn_min:
            self.warnings.append(f"{missing} avisos sin descripción, se reintentan")

    def fetch(self) -> Iterator[RawJob]:
        self.warnings, self.searches = [], []
        seen: set[str] = {j.id for j in self._retry_details}
        need_details: list[RawJob] = list(self._retry_details)
        first, cut = True, False
        for search in self.plan():
            q, c = search.query, search.country
            if not first:
                pause = self.s.cooldown_after_cut_s if cut else self.s.delay_between_calls_s
                self._sleep(random.uniform(*pause))
            first = False
            before = len(self.warnings)
            try:
                rows = self._call(search)
            except RateLimited:
                raise  # el runner aborta esta fuente; las demás siguen
            cut = len(self.warnings) > before  # la búsqueda salió cortada por 429
            self.searches.append(
                {
                    "query": q,
                    "country": c.code,
                    "hours": search.hours,
                    "results": search.offset + len(rows),
                    "cut": cut,
                }
            )
            log.info(
                "%s '%s' %s: %d resultados (%d h%s)",
                self.name,
                q,
                c.code,
                len(rows),
                search.hours,
                f", desde el {search.offset}" if search.offset else "",
            )
            for row in rows:
                job = self._to_raw(row, c.code)
                if job is None or job.id in seen:  # la misma oferta sale en varias búsquedas
                    continue
                seen.add(job.id)
                if self._lazy and self._is_relevant(job.title) and not self._is_known(job.id):
                    need_details.append(job)
                else:
                    yield job
        if need_details and cut:
            self._sleep(random.uniform(*self.s.cooldown_after_cut_s))
        yield from self._with_details(need_details)
