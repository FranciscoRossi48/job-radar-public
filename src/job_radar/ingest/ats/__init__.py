"""Registro de adapters ATS por plataforma."""

from job_radar.ingest.ats.ashby import AshbyAdapter
from job_radar.ingest.ats.base import AtsAdapter
from job_radar.ingest.ats.brassring import BrassRingAdapter
from job_radar.ingest.ats.greenhouse import GreenhouseAdapter
from job_radar.ingest.ats.lever import LeverAdapter
from job_radar.ingest.ats.matador import MatadorAdapter
from job_radar.ingest.ats.personio import PersonioAdapter
from job_radar.ingest.ats.recruitee import RecruiteeAdapter
from job_radar.ingest.ats.rss import RssAdapter
from job_radar.ingest.ats.smartrecruiters import SmartRecruitersAdapter
from job_radar.ingest.ats.successfactors import SuccessFactorsAdapter
from job_radar.ingest.ats.workday import WorkdayAdapter

ADAPTERS: dict[str, type[AtsAdapter]] = {
    a.ats: a
    for a in (
        GreenhouseAdapter,
        LeverAdapter,
        AshbyAdapter,
        SmartRecruitersAdapter,
        WorkdayAdapter,
        RecruiteeAdapter,
        PersonioAdapter,
        SuccessFactorsAdapter,
        BrassRingAdapter,
        MatadorAdapter,
        RssAdapter,
    )
}
