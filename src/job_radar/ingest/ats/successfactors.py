"""SAP SuccessFactors Career Site Builder (RMK / CSB): Swiss Re, Nestlé, Zurich Insurance, etc.

Listado HTML paginado (`/search/?q=...&locationsearch=...&startrow=...`) + detalle por aviso.
Filtra título y país antes de pedir el detalle y saltea ids ya conocidos en la base.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from urllib.parse import urljoin

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

log = logging.getLogger(__name__)

_COUNTRY_NAMES = {"CH": "Switzerland", "NL": "Netherlands", "LU": "Luxembourg"}
_ROW = re.compile(r'<tr[^>]*class="[^"]*\bdata-row\b[^"]*"[^>]*>(.*?)</tr>', re.S | re.I)
_LINK = re.compile(
    r'<a[^>]*href="([^"]*/job/[^"]+?/(\d+)/?)"[^>]*class="[^"]*\bjobTitle-link\b[^"]*"[^>]*>(.*?)</a>'
    r'|<a[^>]*class="[^"]*\bjobTitle-link\b[^"]*"[^>]*href="([^"]*/job/[^"]+?/(\d+)/?)"[^>]*>(.*?)</a>',
    re.S | re.I,
)
_LOC = re.compile(
    r'<td[^>]*class="[^"]*\bcolLocation\b[^"]*"[^>]*>.*?<span[^>]*class="[^"]*\bjobLocation\b[^"]*"[^>]*>(.*?)</span>',
    re.S | re.I,
)
_DATE = re.compile(
    r'<td[^>]*class="[^"]*\bcolDate\b[^"]*"[^>]*>.*?<span[^>]*class="[^"]*\bjobDate\b[^"]*"[^>]*>(.*?)</span>',
    re.S | re.I,
)
_TOTAL = re.compile(r'class="paginationLabel"[^>]*>.*?of\s*<b>(\d+)</b>', re.S | re.I)
_DESC = re.compile(
    r'<span[^>]*class="[^"]*\bjobdescription\b[^"]*"[^>]*>(.*?)</span>\s*</span>'
    r'|<div[^>]*itemprop="description"[^>]*>(.*?)</div>',
    re.S | re.I,
)
_TAGS = re.compile(r"<[^>]+>")
_POSTAL = re.compile(r"^\d{4,5}$")
_ISO2 = re.compile(r"^[A-Z]{2}$")


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", s or ""))).strip()


def parse_search_page(page: str) -> tuple[list[tuple[str, str, str, str, str]], int | None]:
    """Devuelve ([(req_id, path, title, location, date_str)], total_results)."""
    out: list[tuple[str, str, str, str, str]] = []
    for row in _ROW.findall(page):
        m = _LINK.search(row)
        if not m:
            continue
        path = m.group(1) or m.group(4)
        req_id = m.group(2) or m.group(5)
        title = _clean(m.group(3) or m.group(6))
        loc_m = _LOC.search(row)
        date_m = _DATE.search(row)
        loc = _clean(loc_m.group(1)) if loc_m else ""
        dt = _clean(date_m.group(1)) if date_m else ""
        out.append((req_id, path, title, loc, dt))
    tot_m = _TOTAL.search(page)
    total = int(tot_m.group(1)) if tot_m else None
    return out, total


def location_country(loc: str, resolve: Callable[[str], str | None]) -> str | None:
    """Código de país explícito para `make`, o None para que lo resuelva la ubicación.

    Las ubicaciones vienen como "Zürich, CH", "Zurich, Zurich, CH" o
    "La Tour-de-Peilz, VD, CH, 1814". Si los alias de `geo` ya reconocen la ciudad, no se pasa
    código: así el cantón de "Geneva, GE" no se lee como Georgia. Si no, se usa el último código
    de dos letras, ignorando el código postal.
    """
    if resolve(loc):
        return None
    parts = [p.strip() for p in loc.split(",") if p.strip() and not _POSTAL.match(p.strip())]
    return parts[-1] if parts and _ISO2.match(parts[-1]) else None


def parse_detail_description(page: str) -> str:
    m = _DESC.search(page)
    if not m:
        return ""
    return html_to_text(m.group(1) or m.group(2))


class SuccessFactorsAdapter(AtsAdapter):
    """Portales SAP SuccessFactors RMK (`/search/` + `/job/.../{id}/`).

    token: identificador corto (ej. `swissre`); extra.host: dominio del portal (por defecto
    `careers.{token}.com`).
    """

    ats = "successfactors"

    def fetch(self):
        host = self.company.extra.get("host") or f"careers.{self.company.token}.com"
        base = f"https://{host}"
        loc_filter = self.company.extra.get("location") or _COUNTRY_NAMES.get(
            (self.company.country or "").upper(), ""
        )
        seen: set[str] = set()
        details = 0
        for term in self.s.workday_search_terms:
            offset = 0
            while True:
                params: dict[str, str | int] = {"q": term, "startrow": offset}
                if loc_filter:
                    params["locationsearch"] = loc_filter
                resp = self.ctx.http.get(f"{base}/search/", params=params)
                if offset == 0:
                    # La primera página tiene que andar: si no, es una fuente rota, no "0 avisos".
                    resp.raise_for_status()
                elif resp.status_code != 200:
                    log.warning("successfactors: %s en %s", resp.status_code, resp.url)
                    break
                rows, total = parse_search_page(resp.text)
                if not rows:
                    break
                for req_id, path, title, loc, dt in rows:
                    if req_id in seen:
                        continue
                    seen.add(req_id)
                    job = self.make(
                        job_id=f"{self.company.token}:{req_id}",
                        url=urljoin(base, path),
                        title=title,
                        location=loc or None,
                        country=location_country(loc, self.ctx.geo.resolve),
                        posted_at=parse_date(dt),
                    )
                    if job is None or not self.ctx.is_relevant(job.title):
                        continue
                    if self.ctx.is_known(job.id):
                        yield job
                        continue
                    if details >= self.s.max_details_per_company:
                        continue
                    details += 1
                    d_resp = self.ctx.http.get(job.url)
                    if d_resp.status_code == 200:
                        job.description = parse_detail_description(d_resp.text)
                    if not job.description:
                        log.warning("successfactors: sin descripción en %s", job.url)
                    yield job
                offset += len(rows)
                if total is None or offset >= total:
                    break
