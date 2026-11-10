from __future__ import annotations

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date


class GreenhouseAdapter(AtsAdapter):
    """boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"""

    ats = "greenhouse"

    def fetch(self):
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.company.token}/jobs?content=true"
        for j in self.ctx.http.get_json(url).get("jobs", []):
            offices = ", ".join(
                o.get("location") or o.get("name") or "" for o in j.get("offices", [])
            )
            location = " | ".join(x for x in ((j.get("location") or {}).get("name"), offices) if x)
            job = self.make(
                job_id=j["id"],
                url=j.get("absolute_url"),
                title=j.get("title", ""),
                location=location,
                description=html_to_text(j.get("content")),
                posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
            )
            if job:
                yield job
