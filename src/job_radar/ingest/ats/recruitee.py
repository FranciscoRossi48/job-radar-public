from __future__ import annotations

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date


class RecruiteeAdapter(AtsAdapter):
    """{token}.recruitee.com/api/offers/"""

    ats = "recruitee"

    def fetch(self):
        url = f"https://{self.company.token}.recruitee.com/api/offers/"
        for j in self.ctx.http.get_json(url).get("offers", []):
            if j.get("status", "published") != "published":
                continue
            description = html_to_text(j.get("description"))
            if j.get("requirements"):
                description += "\n\n## Requirements\n" + html_to_text(j["requirements"])
            job = self.make(
                job_id=j["id"],
                url=j.get("careers_url"),
                title=j.get("title", ""),
                location=j.get("location") or j.get("city"),
                country=j.get("country_code"),
                description=description,
                posted_at=parse_date(j.get("published_at")),
            )
            if job:
                yield job
