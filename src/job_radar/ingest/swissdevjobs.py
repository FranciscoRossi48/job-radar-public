"""SwissDevJobs: listado JSON público (/api/jobsLight) + detalle embebido en la página del aviso.

El listado no trae descripción; el detalle viene en `window.__detailedJob = {...}`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

from job_radar.config import SwissDevJobsSettings
from job_radar.ingest.common import FetchContext, parse_date
from job_radar.models import RawJob

log = logging.getLogger(__name__)

BASE = "https://swissdevjobs.ch"
_MARKER = re.compile(r"window\.__detailedJob\s*=\s*")


def parse_detail(page: str) -> dict | None:
    m = _MARKER.search(page)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(page[m.end() :])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def build_description(d: dict) -> str:
    parts = [d.get("description") or ""]
    for heading, key in (
        ("Requirements (must have)", "requirementsMustTextArea"),
        ("Nice to have", "requirementsNiceTextArea"),
        ("Responsibilities", "responsibilitiesTextArea"),
    ):
        if d.get(key):
            parts.append(f"## {heading}\n{d[key]}")
    if d.get("language"):
        parts.append(f"Working language (per SwissDevJobs): {d['language']}")
    return "\n\n".join(p.strip() for p in parts if p.strip())


class SwissDevJobsAdapter:
    name = "swissdevjobs"

    def __init__(self, settings: SwissDevJobsSettings, ctx: FetchContext):
        self.s = settings
        self.ctx = ctx

    def fetch(self) -> Iterator[RawJob]:
        listing = self.ctx.http.get_json(f"{BASE}/api/jobsLight")
        cats = {c.lower() for c in self.s.tech_categories}
        skip = {e.lower() for e in self.s.skip_exp_levels}
        details = 0
        for item in listing:
            if item.get("isPaused"):
                continue
            if cats and (item.get("techCategory") or "").lower() not in cats:
                continue
            if (item.get("expLevel") or "").lower() in skip:
                continue
            title = item.get("name") or ""
            if not self.ctx.is_relevant(title):
                continue
            job = RawJob(
                source=self.name,
                job_id=item["_id"],
                url=f"{BASE}/jobs/{item['jobUrl']}",
                title=title,
                company=item.get("company"),
                location=item.get("actualCity") or item.get("cityCategory"),
                country="CH",
                posted_at=parse_date(item.get("activeFrom")),
            )
            if self.ctx.is_known(job.id):
                yield job  # solo refresca last_seen
                continue
            if details >= self.s.max_details:
                continue
            details += 1
            page = self.ctx.http.get(job.url)
            detail = parse_detail(page.text) if page.status_code == 200 else None
            if detail is None:
                log.warning("swissdevjobs: sin detalle para %s", job.url)
            else:
                job.description = build_description(detail)
            yield job
