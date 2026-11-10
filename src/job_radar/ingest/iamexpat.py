"""IamExpat.nl: scraping liviano y respetuoso.

- robots.txt permite /career/ (prohíbe /job/ y /search/) y pide crawl-delay de 1s; el cliente
  HTTP ya espera entre requests (`searches.yaml: http.delay_s`).
- Listado: links /career/jobs-netherlands/{categoria}/{slug}/{id} en las páginas de categoría.
- Detalle: JSON-LD `JobPosting` embebido en `self.__next_s.push(...)` (Next.js).
- Las páginas pesan ~2.7 MB: se limitan páginas y detalles por corrida y se saltan los avisos
  ya conocidos. Si el sitio cambia de estructura, el adapter falla solo y la corrida sigue.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from job_radar.config import IamExpatSettings
from job_radar.ingest.common import FetchContext, html_to_text, parse_date
from job_radar.models import RawJob

log = logging.getLogger(__name__)

BASE = "https://www.iamexpat.nl"
_LINK = re.compile(
    r'href="(/career/jobs-netherlands/[a-z0-9-]+-positions/([^"/]+)/([A-Za-z0-9]+))"'
)
_LDJSON = re.compile(r'\{"type":"application/ld\+json","children":("(?:[^"\\]|\\.)*")\}')


def parse_listing(page: str) -> list[tuple[str, str, str]]:
    """[(url, slug, id)] únicos, en orden de aparición."""
    seen: dict[str, tuple[str, str, str]] = {}
    for path, slug, job_id in _LINK.findall(page):
        seen.setdefault(job_id, (urljoin(BASE, path), slug, job_id))
    return list(seen.values())


def parse_job_posting(page: str) -> dict | None:
    for m in _LDJSON.finditer(page):
        try:
            data = json.loads(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _location(posting: dict) -> str | None:
    loc = posting.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = (loc or {}).get("address") or {}
    if isinstance(addr, dict):
        return ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x)
    return None


class IamExpatAdapter:
    name = "iamexpat"

    def __init__(self, settings: IamExpatSettings, ctx: FetchContext):
        self.s = settings
        self.ctx = ctx

    def _listing(self) -> list[tuple[str, str, str]]:
        out: dict[str, tuple[str, str, str]] = {}
        for base_url in self.s.listing_urls:
            for page in range(1, self.s.max_pages + 1):
                url = base_url if page == 1 else f"{base_url}?page={page}"
                resp = self.ctx.http.get(url)
                if resp.status_code != 200:
                    break
                links = parse_listing(resp.text)
                new = [x for x in links if x[2] not in out]
                if not new:
                    break
                out.update({x[2]: x for x in new})
        return list(out.values())

    def fetch(self) -> Iterator[RawJob]:
        details = 0
        for url, slug, job_id in self._listing():
            guess_title = slug.replace("-", " ")
            if not self.ctx.is_relevant(guess_title):
                continue
            job = RawJob(source=self.name, job_id=job_id, url=url, title=guess_title, country="NL")
            if self.ctx.is_known(job.id):
                yield job
                continue
            if details >= self.s.max_details:
                continue
            details += 1
            resp = self.ctx.http.get(url)
            posting = parse_job_posting(resp.text) if resp.status_code == 200 else None
            if posting is None:
                log.warning("iamexpat: sin JobPosting en %s", url)
                continue
            org = posting.get("hiringOrganization") or {}
            job.title = posting.get("title") or guess_title
            job.company = org.get("name") if isinstance(org, dict) else None
            job.location = _location(posting)
            job.description = html_to_text(posting.get("description"))
            job.posted_at = parse_date(posting.get("datePosted"))
            yield job
