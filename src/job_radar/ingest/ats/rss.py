from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

_LDJSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)


def parse_job_posting(page: str) -> dict | None:
    """Primer JSON-LD `JobPosting` de la página (strict=False: algunos traen saltos de línea
    sin escapar dentro de los textos)."""
    for m in _LDJSON.finditer(page):
        try:
            data = json.loads(m.group(1), strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def posting_location(posting: dict) -> str | None:
    loc = posting.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = (loc or {}).get("address") if isinstance(loc, dict) else None
    if not isinstance(addr, dict):
        return None
    country = addr.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    parts = (addr.get("addressLocality"), addr.get("addressRegion"), country)
    return ", ".join(p for p in parts if p) or None


def _rss_date(value: str | None):
    try:
        return parsedate_to_datetime(value).date() if value else None
    except (TypeError, ValueError):
        return None


class RssAdapter(AtsAdapter):
    """Feed RSS de avisos ({extra.url}) + JSON-LD `JobPosting` del detalle.

    El RSS suele traer solo un resumen (sin requisitos ni idiomas): el detalle se pide para los
    avisos nuevos con título relevante, hasta max_details_per_company; si falla o se pasa del tope, sale sin
    descripción (no_description) para reintentarlo cuando reaparezca.
    `extra.title_strip` (regex) limpia sufijos del título, ej. "(#26332) - CONTRACT".
    """

    ats = "rss"

    def fetch(self):
        x = self.company.extra
        strip = re.compile(x["title_strip"]) if x.get("title_strip") else None
        resp = self.ctx.http.get(x["url"])
        resp.raise_for_status()
        details = 0
        for item in ET.fromstring(resp.content).iter("item"):
            link = (item.findtext("link") or "").strip()
            title = html.unescape(item.findtext("title") or "").strip()
            if strip:
                title = strip.sub("", title).strip()
            guid = (item.findtext("guid") or "").strip() or link.rstrip("/").rsplit("/", 1)[-1]
            job_id = f"{self.company.token}:{guid}"
            summary = html_to_text(item.findtext("description"))
            posted = _rss_date(item.findtext("pubDate"))
            posting = None
            wanted = not self.ctx.is_known(f"{self.ats}:{job_id}") and self.ctx.is_relevant(title)
            if wanted and details < self.s.max_details_per_company:
                details += 1
                page = self.ctx.http.get(link)
                if page.status_code == 200:
                    posting = parse_job_posting(page.text)
            if wanted and posting is None:
                # Sin detalle (falló o tope): sin descripción, queda no_description y se reintenta
                # al reaparecer. Con el resumen de un renglón se evaluaba a ciegas y no volvía.
                summary = ""
            job = self.make(
                job_id=job_id,
                url=link,
                title=title,
                location=posting_location(posting) if posting else None,
                description=html_to_text((posting or {}).get("description")) or summary,
                posted_at=posted or parse_date((posting or {}).get("datePosted")),
            )
            if job:
                yield job
