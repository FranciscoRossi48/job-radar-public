from __future__ import annotations

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date


class LeverAdapter(AtsAdapter):
    """api.lever.co/v0/postings/{token}?mode=json (EU: extra.region = eu -> api.eu.lever.co)"""

    ats = "lever"

    def fetch(self):
        host = "api.eu.lever.co" if self.company.extra.get("region") == "eu" else "api.lever.co"
        for j in self.ctx.http.get_json(
            f"https://{host}/v0/postings/{self.company.token}?mode=json"
        ):
            cats = j.get("categories") or {}
            location = ", ".join([cats.get("location") or "", *(cats.get("allLocations") or [])])
            parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
            for lst in j.get("lists") or []:
                parts.append(f"## {lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
            parts.append(j.get("additionalPlain") or "")
            job = self.make(
                job_id=j["id"],
                url=j.get("hostedUrl"),
                title=j.get("text", ""),
                location=location.strip(", "),
                country=j.get("country"),
                description="\n\n".join(p for p in parts if p),
                posted_at=parse_date(j.get("createdAt")),
            )
            if job:
                yield job
