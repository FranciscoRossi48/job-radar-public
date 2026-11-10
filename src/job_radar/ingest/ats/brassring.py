"""BrassRing / Talent Gateway (TGnewUI): UBS (`jobs.ubs.com`), etc.

Carga inicial vía `HomeWithPreLoad` (JSON en `<input id="preLoadJSON">`), paginación AJAX en
`ProcessSortAndShowMoreJobs` y detalle en `JobDetails` solo para avisos relevantes nuevos de
países objetivo.
"""

from __future__ import annotations

import html
import json
import logging
import re

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

log = logging.getLogger(__name__)

# No greedy y con \s antes de `value`: el input real trae después
# `capture-escaped-parsed-value="preloadResponse"`, y un `[^>]*` greedy terminaba capturando ese.
_PRELOAD = re.compile(r'<input\b[^>]*?\bid="preLoadJSON"[^>]*?\svalue="([^"]*)"', re.I)
_DETAIL_SECTIONS = (
    "Key responsibilities",
    "Your skills and experience",
    "The team",
    "About us",
)


def parse_preload(page: str) -> dict | None:
    m = _PRELOAD.search(page)
    if not m:
        return None
    try:
        data = json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_detail_questions(data: dict) -> str:
    jd = data.get("Jobdetails") or {}
    by_name = {
        (q.get("QuestionName") or "").strip(): q.get("AnswerValue")
        for q in jd.get("JobDetailQuestions") or []
        if q.get("QuestionName") and q.get("AnswerValue")
    }
    parts = [
        f"## {sec}\n{html_to_text(by_name[sec])}" for sec in _DETAIL_SECTIONS if by_name.get(sec)
    ]
    return "\n\n".join(p for p in parts if p.strip())


class BrassRingAdapter(AtsAdapter):
    """Portal BrassRing TGnewUI (`token`: ej. `ubs`, `extra`: `{host, partnerid, siteid}`)."""

    ats = "brassring"
    MAX_PAGES = 15  # UBS publica ~500 avisos (50 por página); 8 dejaba afuera unos 100

    def _iter_jobs(self, base: str, partnerid: str, siteid: str):
        url = f"{base}/TGnewUI/Search/home/HomeWithPreLoad"
        resp = self.ctx.http.get(
            url,
            params={
                "partnerid": partnerid,
                "siteid": siteid,
                "PageType": "searchResults",
                "SearchType": "linkquery",
            },
        )
        resp.raise_for_status()
        preload = parse_preload(resp.text)
        if preload is None:
            # Sin esto, un cambio en el HTML se ve como "0 avisos" y no como fuente rota.
            raise ValueError(f"brassring: no encontré preLoadJSON en {url}")
        sr = preload.get("searchResultsResponse") or {}
        first_page = (sr.get("Jobs") or {}).get("Job") or []
        yield from first_page

        raw_ss = preload.get("SmartSearchJSONValue")
        if not raw_ss or not first_page:
            return
        try:
            ss = json.loads(raw_ss) if isinstance(raw_ss, str) else raw_ss
        except json.JSONDecodeError:
            return
        session_val = ss.get("EncryptedSessionValue")
        if not session_val:
            return

        seen_count = len(first_page)
        for page_num in range(2, self.MAX_PAGES + 1):
            payload = {
                "partnerId": str(partnerid),
                "siteId": str(siteid),
                "keyword": "",
                "location": "",
                "keywordCustomSolrFields": ss.get("KeywordCustomSolrFields", ""),
                "locationCustomSolrFields": ss.get("LocationCustomSolrFields", ""),
                "encryptedSessionValue": session_val,
                "pageNumber": page_num,
            }
            page = self.ctx.http.post_json(
                f"{base}/TGnewUI/Search/Ajax/ProcessSortAndShowMoreJobs", payload
            )
            jobs = ((page.get("Jobs") or {}).get("Job")) or []
            if not jobs:
                break
            yield from jobs
            seen_count += len(jobs)
            total = page.get("JobsCount") or sr.get("JobsCount") or 0
            if total and seen_count >= int(total):
                break

    def fetch(self):
        x = self.company.extra
        host = x.get("host") or f"jobs.{self.company.token}.com"
        partnerid, siteid = x["partnerid"], x["siteid"]
        base = f"https://{host}"
        seen: set[str] = set()
        details = 0
        for item in self._iter_jobs(base, partnerid, siteid):
            qs = {
                q.get("QuestionName"): q.get("Value")
                for q in item.get("Questions") or []
                if q.get("QuestionName")
            }
            req_id = str(qs.get("reqid") or "").strip()
            if not req_id or req_id in seen:
                continue
            seen.add(req_id)
            title = html.unescape(str(qs.get("jobtitle") or "")).strip()
            loc = html.unescape(str(qs.get("formtext23") or qs.get("formtext2") or "")).strip()
            detail_url = (
                item.get("Link")
                or f"{base}/TGnewUI/Search/home/HomeWithPreLoad"
                f"?partnerid={partnerid}&siteid={siteid}&PageType=JobDetails&jobid={req_id}"
            )
            job = self.make(
                job_id=f"{self.company.token}:{req_id}",
                url=detail_url,
                title=title,
                location=loc or None,
                posted_at=parse_date(qs.get("lastupdated")),
            )
            if job is None or not self.ctx.is_relevant(job.title):
                continue
            if self.ctx.is_known(job.id):
                yield job
                continue
            fallback_desc = html_to_text(qs.get("jobdescription"))
            if details >= self.s.max_details_per_company:
                job.description = fallback_desc
                yield job
                continue
            details += 1
            d_resp = self.ctx.http.get(detail_url)
            d_preload = parse_preload(d_resp.text) if d_resp.status_code == 200 else None
            job.description = (
                parse_detail_questions(d_preload) if d_preload else ""
            ) or fallback_desc
            yield job
