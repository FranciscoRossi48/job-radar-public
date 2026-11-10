from __future__ import annotations

import json
import re

from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.common import html_to_text, parse_date

_MULTI_LOCATION = re.compile(r"^\s*\d+\s+(?:locations|standorte|lieux|locaties)\s*$", re.I)


def find_country_facet(facets: list[dict], resolve) -> dict[str, list[str]]:
    """Busca (recursivamente) un facet cuyos valores sean países objetivo y arma el filtro.

    Workday anida el país (ej. locationMainGroup -> locationCountry) y los ids de país son
    globales, pero el nombre del facet varía según la empresa: por eso se detecta por los
    valores. `resolve(descriptor)` devuelve el ISO-2 objetivo o None.
    """
    for f in facets or []:
        values = f.get("values") or []
        ids = [v["id"] for v in values if v.get("id") and resolve(v.get("descriptor") or "")]
        # Un facet de país tiene muchos valores; evita confundirlo con, por ej., una ciudad.
        if ids and f.get("facetParameter") and len(values) > len(ids):
            return {f["facetParameter"]: ids}
        for v in values:
            if v.get("values"):
                found = find_country_facet([v], resolve)
                if found:
                    return found
    return {}


class WorkdayAdapter(AtsAdapter):
    """Endpoint `cxs` público: {tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

    token = tenant; extra: {host: wd3, site: roche-ext}. Para no recorrer los avisos de todo el
    mundo: (1) si el sitio tiene un filtro de país, se aplica solo con CH/NL/LU; (2) si no, se
    saltean los avisos cuya ubicación del listado es un solo lugar fuera de los países objetivo.
    El país definitivo sale del detalle (con tope de detalles por empresa).
    Opcional: extra.facets como JSON de appliedFacets (reemplaza la detección automática).
    """

    ats = "workday"
    PAGE = 20

    def _resolve_target(self, text: str) -> str | None:
        code = self.ctx.geo.resolve(text)
        return code if code in self.ctx.geo.targets else None

    def _worth_detail(self, locations_text: str | None) -> bool:
        """¿Hay chance de que el aviso sea de un país objetivo?"""
        text = (locations_text or "").strip()
        return not text or bool(_MULTI_LOCATION.match(text)) or bool(self._resolve_target(text))

    def fetch(self):
        tenant, x = self.company.token, self.company.extra
        base = f"https://{tenant}.{x['host']}.myworkdayjobs.com"
        api = f"{base}/wday/cxs/{tenant}/{x['site']}"
        if x.get("facets"):
            facets = json.loads(x["facets"])
        else:
            probe = self.ctx.http.post_json(
                f"{api}/jobs", {"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}}
            )
            facets = find_country_facet(probe.get("facets", []), self._resolve_target)
        seen: set[str] = set()
        details = 0
        for term in self.s.workday_search_terms:
            offset = 0
            while True:
                page = self.ctx.http.post_json(
                    f"{api}/jobs",
                    {
                        "limit": self.PAGE,
                        "offset": offset,
                        "searchText": term,
                        "appliedFacets": facets,
                    },
                )
                postings = page.get("jobPostings", [])
                for p in postings:
                    path = p.get("externalPath")
                    if not path or path in seen or not self.ctx.is_relevant(p.get("title", "")):
                        continue
                    seen.add(path)
                    if not facets and not self._worth_detail(p.get("locationsText")):
                        continue
                    job_id = f"{tenant}:{path.rsplit('/', 1)[-1]}"
                    if self.ctx.is_known(f"{self.ats}:{job_id}"):
                        # Ya está en la DB: solo refrescamos last_seen, sin pedir el detalle.
                        known = self.make(
                            job_id=job_id,
                            url=f"{base}/{x['site']}{path}",
                            title=p["title"],
                            country=self.company.country,
                        )
                        if known:
                            yield known
                        continue
                    if details >= self.s.max_details_per_company:
                        continue
                    details += 1
                    info = self.ctx.http.get_json(f"{api}{path}").get("jobPostingInfo", {})
                    country = (info.get("country") or {}).get("descriptor")
                    job = self.make(
                        job_id=job_id,
                        url=info.get("externalUrl") or f"{base}/{x['site']}{path}",
                        title=info.get("title") or p["title"],
                        location=info.get("location") or p.get("locationsText"),
                        country=country,
                        description=html_to_text(info.get("jobDescription")),
                        posted_at=parse_date(info.get("startDate")),
                    )
                    if job:
                        yield job
                offset += len(postings)
                if not postings or offset >= page.get("total", 0):
                    break
