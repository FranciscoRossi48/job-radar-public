import json
from pathlib import Path

import httpx
import pytest
import respx

from job_radar.config import AtsSettings, Company, IamExpatSettings, SwissDevJobsSettings
from job_radar.geo import CountryResolver
from job_radar.http import Http
from job_radar.ingest.ats import ADAPTERS
from job_radar.ingest.ats.successfactors import location_country
from job_radar.ingest.base import RateLimited
from job_radar.ingest.common import FetchContext
from job_radar.ingest.iamexpat import IamExpatAdapter
from job_radar.ingest.swissdevjobs import SwissDevJobsAdapter
from job_radar.prefilter.title import TitleFilter

FX = Path(__file__).parent / "fixtures" / "http"


def fx(name):
    return (FX / name).read_text()


def fxj(name):
    return json.loads(fx(name))


@pytest.fixture
def ctx(cfg):
    title = TitleFilter(cfg.prefilter.title)
    known: set[str] = set()
    c = FetchContext(
        http=Http(delay_s=(0, 0), sleep=lambda s: None, max_retries=2),
        geo=CountryResolver(cfg.searches.geo),
        is_relevant=lambda t: title.check(t) is None,
        is_known=known.__contains__,
    )
    c.known = known  # para que los tests marquen ids conocidos
    return c


def ats(kind, token, ctx, country="CH", **extra):
    company = Company(name="Acme", ats=kind, token=token, country=country, extra=extra)
    return ADAPTERS[kind](
        company, ctx, AtsSettings(max_details_per_company=5, workday_search_terms=["data"])
    )


@respx.mock
def test_greenhouse(ctx):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(
        json=fxj("greenhouse.json")
    )
    jobs = list(ats("greenhouse", "acme", ctx).fetch())
    assert [j.id for j in jobs] == ["greenhouse:101", "greenhouse:103"]  # Varsovia afuera
    assert jobs[0].country == "CH"
    assert "5+ years of SQL" in jobs[0].description and "<li>" not in jobs[0].description
    assert jobs[1].country == "CH"  # sin ubicación -> país de la empresa


@respx.mock
def test_matador(ctx):
    route = respx.get("https://www.rmgroup.ch/wp-json/wp/v2/matador-job-listings").respond(
        json=fxj("matador.json")
    )
    jobs = list(ats("matador", "rmgroup", ctx, host="www.rmgroup.ch").fetch())
    assert route.call_count == 1  # menos de una página: no pide la siguiente
    assert [(j.id, j.country) for j in jobs] == [
        ("matador:rmgroup:7465678", "CH"),
        ("matador:rmgroup:7465001", "LU"),
        ("matador:rmgroup:7464000", "CH"),  # sin ubicación -> país de la empresa
    ]
    assert jobs[0].title == "Data Engineer – Snowflake"
    assert jobs[0].location == "Zurich, Zürich (de)"
    assert "Snowflake and dbt" in jobs[0].description and "<li>" not in jobs[0].description
    assert jobs[0].posted_at is not None


@respx.mock
def test_rss_with_json_ld_detail(ctx):
    respx.get("https://jobs.example.ch/de/rss").respond(text=fx("rss_list.xml"))
    detail = respx.get("https://jobs.example.ch/de/jobs/26332").respond(text=fx("rss_detail.html"))
    ctx.known.add("rss:acme:1700")
    adapter = ats(
        "rss",
        "acme",
        ctx,
        url="https://jobs.example.ch/de/rss",
        title_strip=r"\s*\(#\d+\)\s*(?:-\s*[A-Z]+)?\s*$",
    )
    jobs = {j.id: j for j in adapter.fetch()}
    assert list(jobs) == ["rss:acme:1967", "rss:acme:1938", "rss:acme:1700"]
    assert detail.call_count == 1  # Java: título irrelevante; BI: ya conocido
    de = jobs["rss:acme:1967"]
    assert de.title == "Data Engineer (Snowflake / Finance Data Platform)"
    assert de.location == "Zürich, Switzerland" and de.country == "CH"
    assert "German B2" in de.description and "<p>" not in de.description
    assert str(de.posted_at) == "2026-11-05"
    assert jobs["rss:acme:1700"].description == "Power BI reporting, summary only."


@respx.mock
def test_rss_without_detail_has_no_description(ctx):
    respx.get("https://jobs.example.ch/de/rss").respond(text=fx("rss_list.xml"))
    respx.get("https://jobs.example.ch/de/jobs/26332").respond(status_code=404)
    ctx.known.add("rss:acme:1700")
    adapter = ats("rss", "acme", ctx, url="https://jobs.example.ch/de/rss")
    jobs = {j.id: j for j in adapter.fetch()}
    # nuevo y relevante sin detalle: vacío para que quede no_description y se reintente
    assert jobs["rss:acme:1967"].description == ""


@respx.mock
def test_lever(ctx):
    respx.get("https://api.lever.co/v0/postings/acme?mode=json").respond(json=fxj("lever.json"))
    jobs = list(ats("lever", "acme", ctx).fetch())
    assert [j.id for j in jobs] == ["lever:a1b2"]
    assert "dbt and Snowflake" in jobs[0].description and jobs[0].posted_at is not None


@respx.mock
def test_ashby(ctx):
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(json=fxj("ashby.json"))
    jobs = list(ats("ashby", "acme", ctx, country="NL").fetch())
    assert [(j.id, j.country) for j in jobs] == [("ashby:u-1", "NL")]
    assert "## What you bring" in jobs[0].description


@respx.mock
def test_recruitee(ctx):
    respx.get("https://acme.recruitee.com/api/offers/").respond(json=fxj("recruitee.json"))
    jobs = list(ats("recruitee", "acme", ctx, country="NL").fetch())
    assert [j.id for j in jobs] == ["recruitee:501"]
    assert "## Requirements" in jobs[0].description


@respx.mock
def test_personio(ctx):
    respx.get("https://acme.jobs.personio.de/xml?language=en").respond(text=fx("personio.xml"))
    jobs = list(ats("personio", "acme", ctx).fetch())
    assert [(j.id, j.country) for j in jobs] == [("personio:acme:9001", "CH")]  # Múnich afuera
    assert "German is a plus" in jobs[0].description


@respx.mock
def test_smartrecruiters_fetches_detail_only_when_needed(ctx):
    respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings").respond(
        json=fxj("smartrecruiters_list.json")
    )
    detail = respx.get("https://api.smartrecruiters.com/v1/companies/Acme/postings/744001").respond(
        json=fxj("smartrecruiters_detail.json")
    )
    jobs = list(ats("smartrecruiters", "Acme", ctx).fetch())
    # 744002 es de Alemania; 744003 no es un título relevante
    assert [j.id for j in jobs] == ["smartrecruiters:744001"]
    assert "Fluent German (C1)" in jobs[0].description and detail.call_count == 1

    ctx.known.add("smartrecruiters:744001")
    jobs = list(ats("smartrecruiters", "Acme", ctx).fetch())
    assert len(jobs) == 1 and jobs[0].description == "" and detail.call_count == 1


@respx.mock
def test_workday(ctx):
    api = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/ext"
    lst = respx.post(f"{api}/jobs").respond(json=fxj("workday_list.json"))
    respx.get(f"{api}/job/Basel/Data-Engineer_R-100").respond(json=fxj("workday_detail.json"))
    jobs = list(ats("workday", "acme", ctx, host="wd3", site="ext").fetch())
    assert [(j.id, j.country) for j in jobs] == [("workday:acme:Data-Engineer_R-100", "CH")]
    # calls[0] es la sonda para detectar el filtro de país; después, las búsquedas
    assert json.loads(lst.calls[1].request.content)["searchText"] == "data"


@respx.mock
def test_swissdevjobs(ctx):
    respx.get("https://swissdevjobs.ch/api/jobsLight").respond(json=fxj("swissdevjobs_list.json"))
    respx.get("https://swissdevjobs.ch/jobs/Acme-Senior-Data-Engineer").respond(
        text=fx("swissdevjobs_detail.html")
    )
    settings = SwissDevJobsSettings(tech_categories=["Data"], skip_exp_levels=["Junior"])
    jobs = list(SwissDevJobsAdapter(settings, ctx).fetch())
    assert [j.id for j in jobs] == ["swissdevjobs:sdj1"]
    d = jobs[0].description
    assert "## Requirements (must have)" in d and "Fluent German (B2)" in d
    assert "Working language (per SwissDevJobs): German" in d


@respx.mock
def test_iamexpat(ctx):
    base = "https://www.iamexpat.nl/career/jobs-netherlands/it-technology"
    respx.get(base).respond(text=fx("iamexpat_list.html"))
    respx.get(f"{base}?page=2").respond(text=fx("iamexpat_list.html"))  # sin links nuevos -> corta
    respx.get(
        "https://www.iamexpat.nl/career/jobs-netherlands/it-technology-positions/"
        "senior-data-engineer-amsterdam/AbC123"
    ).respond(text=fx("iamexpat_detail.html"))
    settings = IamExpatSettings(listing_urls=[base], max_pages=2, max_details=5)
    jobs = list(IamExpatAdapter(settings, ctx).fetch())
    assert [j.id for j in jobs] == ["iamexpat:AbC123"]  # customer care no es relevante
    j = jobs[0]
    assert (j.title, j.company, j.location) == (
        "Senior Data Engineer",
        "Acme BV",
        "Amsterdam, Noord-Holland",
    )
    assert "Dutch is a plus" in j.description


@respx.mock
def test_http_retries_429_and_honors_retry_after():
    sleeps = []
    route = respx.get("https://x.test/a").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    http = Http(delay_s=(0, 0), sleep=sleeps.append, max_retries=2)
    assert http.get_json("https://x.test/a") == {"ok": True}
    assert route.call_count == 2 and 7 in sleeps


@respx.mock
def test_http_persistent_429_raises_rate_limited():
    respx.get("https://x.test/b").respond(429)
    with pytest.raises(RateLimited):
        Http(delay_s=(0, 0), sleep=lambda s: None, max_retries=2).get("https://x.test/b")


@pytest.mark.parametrize(
    "location,explicit,fallback,expected",
    [
        ("Zürich, ZH, CH", None, None, "CH"),
        ("Geneva", None, None, "CH"),
        ("Amsterdam, Noord-Holland", None, None, "NL"),
        ("Esch-sur-Alzette", None, None, "LU"),
        ("Singapore", None, "CH", None),
        ("", None, "CH", "CH"),
        (None, "ch", None, "CH"),
        (None, "SG", "CH", None),
        ("Basel", "Switzerland", None, "CH"),
        ("Remote - Europe", None, "NL", None),
    ],
)
def test_country_resolver(cfg, location, explicit, fallback, expected):
    assert CountryResolver(cfg.searches.geo).keep(location, explicit, fallback) == expected


@pytest.mark.parametrize("sep", [" = ", "=", " =\n  "])
def test_swissdevjobs_parse_detail_spacing(sep):
    from job_radar.ingest.swissdevjobs import parse_detail

    assert parse_detail(f'<script>window.__detailedJob{sep}{{"a": 1}};</script>') == {"a": 1}


def test_workday_country_facet_detection(ctx):
    from job_radar.ingest.ats.workday import WorkdayAdapter, find_country_facet

    facets = [
        {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "IT", "id": "x"}]},
        {
            "facetParameter": "locationMainGroup",
            "values": [
                {
                    "facetParameter": "locationCountry",
                    "descriptor": "Country",
                    "values": [
                        {"descriptor": "Switzerland", "id": "ch-id"},
                        {"descriptor": "Netherlands", "id": "nl-id"},
                        {"descriptor": "United States of America", "id": "us-id"},
                    ],
                },
            ],
        },
    ]
    adapter = WorkdayAdapter(
        Company(name="A", ats="workday", token="a", extra={"host": "wd3", "site": "s"}),
        ctx,
        AtsSettings(),
    )
    assert find_country_facet(facets, adapter._resolve_target) == {
        "locationCountry": ["ch-id", "nl-id"]
    }


@respx.mock
def test_workday_skips_single_foreign_locations_without_facet(ctx):
    api = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/ext"
    listing = {
        "total": 3,
        "jobPostings": [
            {
                "title": "Data Engineer",
                "externalPath": "/job/Indianapolis/DE_R-1",
                "locationsText": "Indianapolis",
            },
            {
                "title": "Data Engineer",
                "externalPath": "/job/Basel/DE_R-2",
                "locationsText": "Basel",
            },
            {
                "title": "Data Analyst",
                "externalPath": "/job/x/DA_R-3",
                "locationsText": "3 Locations",
            },
        ],
    }
    respx.post(f"{api}/jobs").respond(json=listing)
    d1 = respx.get(f"{api}/job/Indianapolis/DE_R-1").respond(json=fxj("workday_detail.json"))
    d2 = respx.get(f"{api}/job/Basel/DE_R-2").respond(json=fxj("workday_detail.json"))
    d3 = respx.get(f"{api}/job/x/DA_R-3").respond(json=fxj("workday_detail.json"))
    list(ats("workday", "acme", ctx, host="wd3", site="ext").fetch())
    assert (d1.call_count, d2.call_count, d3.call_count) == (0, 1, 1)


@respx.mock
def test_successfactors(ctx):
    respx.get("https://careers.acme.com/search/").respond(text=fx("successfactors_list.html"))
    detail = respx.get(
        "https://careers.acme.com/job/Zurich-Lead-Data-Solutions-Engineer/1362412/"
    ).respond(text=fx("successfactors_detail.html"))
    jobs = list(ats("successfactors", "acme", ctx).fetch())
    # 1362413 es de GB; 1362414 no es un título relevante
    assert [(j.id, j.country) for j in jobs] == [("successfactors:acme:1362412", "CH")]
    assert "Spark and Snowflake" in jobs[0].description and detail.call_count == 1
    assert str(jobs[0].posted_at) == "2026-09-25"


@pytest.mark.parametrize(
    "loc, country",
    [
        ("Zürich, CH", "CH"),
        ("Zurich, Zurich, CH", "CH"),
        ("La Tour-de-Peilz, VD, CH, 1814", "CH"),  # código postal al final
        ("Geneva, GE", "CH"),  # GE es el cantón, no Georgia
        ("Lausanne, VD", "CH"),
        ("Bern, BE, CH", "CH"),  # BE es el cantón, no Bélgica
        ("Amsterdam, NL", "NL"),
        ("London, GB", None),
        ("Tbilisi, GE", None),
        ("", "CH"),  # sin ubicación: país por defecto de la empresa
    ],
)
def test_successfactors_location_country(ctx, loc, country):
    a = ats("successfactors", "acme", ctx)
    job = a.make(
        job_id="x",
        url="u",
        title="Data Engineer",
        location=loc or None,
        country=location_country(loc, ctx.geo.resolve),
    )
    assert (job.country if job else None) == country


@respx.mock
def test_successfactors_broken_portal_fails_loudly(ctx):
    respx.get("https://careers.acme.com/search/").respond(status_code=403)
    with pytest.raises(httpx.HTTPStatusError):
        list(ats("successfactors", "acme", ctx).fetch())


@respx.mock
def test_brassring(ctx):
    base = "https://jobs.acme.com/TGnewUI/Search/home/HomeWithPreLoad"
    respx.get(
        f"{base}?partnerid=25008&siteid=5012&PageType=searchResults&SearchType=linkquery"
    ).respond(text=fx("brassring_list.html"))
    detail = respx.get(
        f"{base}?partnerid=25008&siteid=5012&PageType=JobDetails&jobid=350001"
    ).respond(text=fx("brassring_detail.html"))
    jobs = list(ats("brassring", "acme", ctx, partnerid="25008", siteid="5012").fetch())
    # 350002 es de India
    assert [(j.id, j.country) for j in jobs] == [("brassring:acme:350001", "CH")]
    assert jobs[0].title == "Senior Data Engineer & Architect"
    assert "## Your skills and experience" in jobs[0].description and detail.call_count == 1
    assert str(jobs[0].posted_at) == "2026-10-23"


@respx.mock
def test_brassring_without_preload_fails_loudly(ctx):
    respx.get("https://jobs.acme.com/TGnewUI/Search/home/HomeWithPreLoad").respond(
        text="<html><body>mantenimiento</body></html>"
    )
    with pytest.raises(ValueError, match="preLoadJSON"):
        list(ats("brassring", "acme", ctx, partnerid="25008", siteid="5012").fetch())
