from __future__ import annotations

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date


class AshbyAdapter(AtsAdapter):
    """api.ashbyhq.com/posting-api/job-board/{token}"""

    ats = "ashby"

    def fetch(self):
        url = f"https://api.ashbyhq.com/posting-api/job-board/{self.company.token}"
        for j in self.ctx.http.get_json(url).get("jobs", []):
            if j.get("isListed") is False:
                continue
            postal = ((j.get("address") or {}).get("postalAddress")) or {}
            secondary = [s.get("location", "") for s in j.get("secondaryLocations") or []]
            location = ", ".join(x for x in [j.get("location"), *secondary] if x)
            job = self.make(
                job_id=j["id"],
                url=j.get("jobUrl"),
                title=j.get("title", ""),
                location=" | ".join(x for x in (location, postal.get("addressCountry")) if x),
                description=html_to_text(j.get("descriptionHtml")) or j.get("descriptionPlain", ""),
                posted_at=parse_date(j.get("publishedAt")),
            )
            if job:
                yield job
