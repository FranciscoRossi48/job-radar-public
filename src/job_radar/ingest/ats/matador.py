from __future__ import annotations

import html
import re

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

# Matador Jobs (plugin de WordPress que publica avisos de Bullhorn). La ubicación viene solo en
# el bloque de metadatos al principio del contenido.
_LOCATION = re.compile(
    r'matador-job-field-job_general_location.*?matador-job-meta-value">([^<]+)<', re.S
)
_PAGE = 100  # máximo que acepta la API REST de WordPress


class MatadorAdapter(AtsAdapter):
    """{host}/wp-json/wp/v2/matador-job-listings: todos los avisos con contenido, sin N+1."""

    ats = "matador"

    def fetch(self):
        host = self.company.extra.get("host") or f"www.{self.company.token}.ch"
        base = f"https://{host}/wp-json/wp/v2/matador-job-listings"
        page = 1
        while True:
            items = self.ctx.http.get_json(
                base,
                params={
                    "per_page": _PAGE,
                    "page": page,
                    "_fields": "id,date_gmt,link,title,content",
                },
            )
            for j in items:
                content = (j.get("content") or {}).get("rendered") or ""
                loc = _LOCATION.search(content)
                job = self.make(
                    job_id=f"{self.company.token}:{j['id']}",
                    url=j.get("link"),
                    title=html.unescape((j.get("title") or {}).get("rendered") or "").strip(),
                    location=html.unescape(loc.group(1)).strip() if loc else None,
                    description=html_to_text(content),
                    posted_at=parse_date(j.get("date_gmt")),
                )
                if job:
                    yield job
            # Pedir una página de más devuelve 400: se corta con la primera incompleta.
            if len(items) < _PAGE:
                break
            page += 1
