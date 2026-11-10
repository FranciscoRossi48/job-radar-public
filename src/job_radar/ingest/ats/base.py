"""Base común de adapters ATS: una instancia por empresa de companies.yaml."""

from __future__ import annotations

from collections.abc import Iterator

from job_radar.config import AtsSettings, Company
from job_radar.ingest.common import FetchContext
from job_radar.models import RawJob


class AtsAdapter:
    ats: str = ""

    def __init__(self, company: Company, ctx: FetchContext, settings: AtsSettings):
        self.company = company
        self.ctx = ctx
        self.s = settings
        self.name = f"{self.ats}:{company.token}"

    def make(self, *, job_id, url, title, location=None, country=None, **kw) -> RawJob | None:
        """RawJob si el aviso es de un país objetivo; None si hay que descartarlo."""
        code = self.ctx.geo.keep(location, country, self.company.country)
        if code is None:
            return None
        return RawJob(
            source=self.ats,
            job_id=str(job_id),
            url=url,
            title=title,
            company=self.company.name,
            location=location,
            country=code,
            **kw,
        )

    def fetch(self) -> Iterator[RawJob]:
        raise NotImplementedError
