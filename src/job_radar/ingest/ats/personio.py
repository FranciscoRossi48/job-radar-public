from __future__ import annotations

import xml.etree.ElementTree as ET

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date


class PersonioAdapter(AtsAdapter):
    """{token}.jobs.personio.de/xml?language=en (extra.domain = com para .jobs.personio.com)"""

    ats = "personio"

    def fetch(self):
        domain = self.company.extra.get("domain", "de")
        base = f"https://{self.company.token}.jobs.personio.{domain}"
        resp = self.ctx.http.get(f"{base}/xml?language=en")
        resp.raise_for_status()
        for pos in ET.fromstring(resp.content).iter("position"):
            offices = [pos.findtext("office") or ""]
            offices += [o.text or "" for o in pos.findall("additionalOffices/office")]
            parts = []
            for jd in pos.findall("jobDescriptions/jobDescription"):
                parts.append(
                    f"## {jd.findtext('name', '').strip()}\n{html_to_text(jd.findtext('value'))}"
                )
            job_id = pos.findtext("id")
            job = self.make(
                job_id=f"{self.company.token}:{job_id}",
                url=f"{base}/job/{job_id}",
                title=(pos.findtext("name") or "").strip(),
                location=", ".join(o for o in offices if o),
                description="\n\n".join(parts),
                posted_at=parse_date(pos.findtext("createdAt")),
            )
            if job:
                yield job
