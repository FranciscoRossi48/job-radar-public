from __future__ import annotations

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

API = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersAdapter(AtsAdapter):
    """Listado paginado + detalle por aviso (N+1). Filtra país y título antes del detalle."""

    ats = "smartrecruiters"

    def _listing(self):
        offset = 0
        while True:
            page = self.ctx.http.get_json(
                f"{API}/{self.company.token}/postings", params={"limit": 100, "offset": offset}
            )
            content = page.get("content", [])
            yield from content
            offset += len(content)
            if not content or offset >= page.get("totalFound", 0):
                return

    def fetch(self):
        details = 0
        for p in self._listing():
            loc = p.get("location") or {}
            job = self.make(
                job_id=p["id"],
                url=f"https://jobs.smartrecruiters.com/{self.company.token}/{p['id']}",
                title=p.get("name", ""),
                location=loc.get("fullLocation") or loc.get("city"),
                country=loc.get("country"),
                posted_at=parse_date(p.get("releasedDate")),
            )
            if job is None or not self.ctx.is_relevant(job.title):
                continue
            if self.ctx.is_known(job.id):
                yield job
                continue
            if details >= self.s.max_details_per_company:
                continue
            details += 1
            d = self.ctx.http.get_json(f"{API}/{self.company.token}/postings/{p['id']}")
            sections = (d.get("jobAd") or {}).get("sections") or {}
            parts = []
            for key in (
                "jobDescription",
                "qualifications",
                "additionalInformation",
                "companyDescription",
            ):
                sec = sections.get(key) or {}
                if sec.get("text"):
                    parts.append(f"## {sec.get('title') or key}\n{html_to_text(sec['text'])}")
            job.description = "\n\n".join(parts)
            job.url = d.get("postingUrl") or job.url
            yield job
