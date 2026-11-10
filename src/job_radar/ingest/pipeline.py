"""Ingesta completa: adapters -> prefiltro -> dedup -> SQLite, con resumen por fuente."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from job_radar import db
from job_radar.config import Config
from job_radar.geo import CountryResolver
from job_radar.http import Http
from job_radar.ingest.ats import ADAPTERS
from job_radar.ingest.base import Adapter, run_adapters
from job_radar.ingest.common import FetchContext
from job_radar.ingest.iamexpat import IamExpatAdapter
from job_radar.ingest.jobspy_adapter import JobSpyAdapter, Search, window_hours
from job_radar.ingest.swissdevjobs import SwissDevJobsAdapter
from job_radar.logs import last_clean_ingest, source_today, step_ran_today
from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer
from job_radar.prefilter.title import TitleFilter

log = logging.getLogger(__name__)


def make_context(cfg: Config, known: set[str]) -> FetchContext:
    h = cfg.searches.http
    title = TitleFilter(cfg.prefilter.title)
    return FetchContext(
        http=Http(
            timeout_s=h.timeout_s,
            delay_s=h.delay_s,
            max_retries=h.max_retries,
            backoff_base_s=h.backoff_base_s,
            backoff_max_s=h.backoff_max_s,
        ),
        geo=CountryResolver(cfg.searches.geo, cfg.searches.active_countries),
        is_relevant=lambda t: title.check(t) is None,
        is_known=known.__contains__,
    )


def build_adapters(cfg: Config, ctx: FetchContext, only: set[str] | None = None) -> list[Adapter]:
    """Todas las fuentes configuradas. `only` filtra por nombre ('linkedin', 'greenhouse',
    'greenhouse:getyourguide', 'ats', 'jobspy', ...)."""
    s = cfg.searches
    active = {c.upper() for c in s.active_countries}
    countries = [c for c in s.countries if not active or c.code.upper() in active]
    adapters: list[Adapter] = [
        JobSpyAdapter(
            site,
            s.jobspy,
            s.queries_for(date.today()),
            countries,
            is_known=ctx.is_known,
            is_relevant=ctx.is_relevant,
            fixed_queries=s.queries,
            fixed_hours_old=window_hours(
                s.jobspy, last_clean_ingest(cfg, f"jobspy:{site}"), datetime.now().astimezone()
            ),
        )
        for site in s.jobspy.sites
    ]
    if s.swissdevjobs.enabled:
        adapters.append(SwissDevJobsAdapter(s.swissdevjobs, ctx))
    if s.iamexpat.enabled:
        adapters.append(IamExpatAdapter(s.iamexpat, ctx))
    for company in cfg.companies:
        # Empresas de un país pausado: ni se consultan (todos sus avisos se descartarían igual).
        if active and company.country and company.country.upper() not in active:
            continue
        adapters.append(ADAPTERS[company.ats](company, ctx, s.ats))
    if only:
        adapters = [a for a in adapters if _matches(a, only)]
    return adapters


RETRY_STEP = "linkedin_retry"


def linkedin_retry_adapter(
    cfg: Config, conn: sqlite3.Connection, now: datetime | None = None
) -> JobSpyAdapter | None:
    """Para el rescate: retoma las búsquedas de LinkedIn de hoy que se cortaron por 429 (desde
    donde quedaron) o que no llegaron a correr, y pide las descripciones que faltaron. Horas
    después el bloqueo de LinkedIn suele haberse levantado. None si no hay nada que reintentar
    o si ya se reintentó hoy."""
    s, js = cfg.searches, cfg.searches.jobspy
    now = now or datetime.now().astimezone()
    if not js.retry_in_rescue or "linkedin" not in js.sites or step_ran_today(cfg, RETRY_STEP, now):
        return None
    src = source_today(cfg, "jobspy:linkedin", now)
    if src is None or "searches" not in src:
        return None  # hoy no corrió LinkedIn (o es un resumen viejo, sin detalle por búsqueda)
    active = {c.upper() for c in s.active_countries}
    countries = [c for c in s.countries if not active or c.code.upper() in active]
    previous = {(x["query"], x["country"]): x for x in src["searches"]}
    fixed = window_hours(js, last_clean_ingest(cfg, "jobspy:linkedin"), now)
    plan = []
    for c in countries:
        for q in s.queries_for(now.date()):
            prev = previous.get((q, c.code))
            if prev and not prev["cut"]:
                continue  # completa
            got = prev["results"] if prev else 0
            if got >= js.results_wanted:
                continue
            hours = prev["hours"] if prev else (fixed if fixed and q in s.queries else js.hours_old)
            plan.append(Search(q, c, hours, offset=got, wanted=js.results_wanted - got))
    midnight = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    rows = db.missing_descriptions(
        conn, "linkedin", midnight.astimezone(UTC).isoformat(timespec="seconds"), s.active_codes
    )
    missing = [
        RawJob(
            source="linkedin",
            job_id=r["id"].split(":", 1)[1],
            url=r["url"],
            title=r["title"],
            company=r["company"],
            location=r["location"],
            country=r["country"],
            posted_at=date.fromisoformat(r["posted_at"]) if r["posted_at"] else None,
        )
        for r in rows
    ]
    if not plan and not missing:
        return None
    ctx = make_context(cfg, db.known_ids(conn))
    log.info(
        "rescate de LinkedIn: %d búsquedas para retomar, %d avisos sin descripción",
        len(plan),
        len(missing),
    )
    return JobSpyAdapter(
        "linkedin",
        js,
        [],
        countries,
        is_known=ctx.is_known,
        is_relevant=ctx.is_relevant,
        plan=plan,
        retry_details=missing,
    )


def _matches(adapter: Adapter, only: set[str]) -> bool:
    name = adapter.name  # jobspy:linkedin | swissdevjobs | iamexpat | greenhouse:token
    parts = set(name.split(":"))
    if "ats" in only and getattr(adapter, "ats", ""):
        return True
    return name in only or bool(parts & only)


def ingest(conn: sqlite3.Connection, cfg: Config, adapters: Iterable[Adapter]) -> dict:
    pre = Prefilterer(cfg.prefilter)
    dd = cfg.app.dedup
    keys = ("new", "seen", "excluded_prefilter", "duplicate")
    summary: dict = {"sources": {}} | dict.fromkeys(keys, 0)
    for res in run_adapters(adapters):
        src = {"fetched": len(res.jobs)} | dict.fromkeys(keys, 0)
        if res.error:
            src["error"] = res.error
        if res.warnings:
            src["warnings"] = res.warnings
        if res.searches is not None:
            src["searches"] = res.searches
        for job in res.jobs:
            result = pre.run(job)
            if db.upsert_job(conn, job, result, dd):
                src["new"] += 1
                status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job.id,)).fetchone()[
                    0
                ]
                if status in ("excluded_prefilter", "duplicate"):
                    src[status] += 1
            else:
                src["seen"] += 1
        conn.commit()
        summary["sources"][res.name] = src
        for k in keys:
            summary[k] += src[k]
    summary["stale"] = db.mark_stale(conn, cfg.app.stale_days)
    summary["leases_released"] = db.release_expired_leases(conn, cfg.app.lease_minutes)
    conn.commit()
    return summary
