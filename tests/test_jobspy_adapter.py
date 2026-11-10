import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from job_radar.config import Backoff, CountrySearch, JobSpySettings
from job_radar.ingest.base import RateLimited, run_adapters, with_backoff
from job_radar.ingest.jobspy_adapter import JobSpyAdapter, window_hours

CH = CountrySearch(
    code="CH",
    linkedin_location="Switzerland",
    indeed_country="switzerland",
    google_location="Zurich, Switzerland",
)
SETTINGS = JobSpySettings(
    backoff=Backoff(max_retries=2, base_s=1, max_s=2), delay_between_calls_s=(0, 0)
)
ROW = {
    "id": "li-123",
    "job_url": "https://www.linkedin.com/jobs/view/123",
    "job_url_direct": None,
    "title": "Senior Data Engineer",
    "company": "Acme Bank",
    "location": "Zurich, ZH, CH",
    "description": "Build pipelines.",
    "date_posted": None,
}
jobspy_log = logging.getLogger("JobSpy:LinkedIn")


def test_maps_rows_to_rawjob():
    adapter = JobSpyAdapter(
        "linkedin",
        SETTINGS,
        ["data engineer"],
        [CH],
        scrape=lambda **kw: pd.DataFrame([ROW]),
        sleep=lambda s: None,
    )
    jobs = list(adapter.fetch())
    assert len(jobs) == 1
    j = jobs[0]
    assert (j.id, j.country, j.company) == ("linkedin:li-123", "CH", "Acme Bank")


def test_passes_site_specific_kwargs():
    seen = {}

    def scrape(**kw):
        seen.update(kw)
        return pd.DataFrame()

    list(
        JobSpyAdapter("indeed", SETTINGS, ["q"], [CH], scrape=scrape, sleep=lambda s: None).fetch()
    )
    assert seen["country_indeed"] == "switzerland" and seen["hours_old"] == 24
    list(
        JobSpyAdapter(
            "google", SETTINGS, ["data engineer"], [CH], scrape=scrape, sleep=lambda s: None
        ).fetch()
    )
    assert (
        seen["google_search_term"] == "data engineer jobs near Zurich, Switzerland since yesterday"
    )


def test_retries_on_429_then_succeeds():
    calls, sleeps = [], []

    def scrape(**kw):
        calls.append(1)
        if len(calls) == 1:
            jobspy_log.error("LinkedIn response status code 429")
            return pd.DataFrame()
        return pd.DataFrame([ROW])

    adapter = JobSpyAdapter("linkedin", SETTINGS, ["q"], [CH], scrape=scrape, sleep=sleeps.append)
    assert len(list(adapter.fetch())) == 1
    assert len(calls) == 2 and len(sleeps) == 1


def test_persistent_429_aborts_only_that_source():
    def blocked(**kw):
        jobspy_log.error("LinkedIn response status code 429")
        return pd.DataFrame()

    bad = JobSpyAdapter("linkedin", SETTINGS, ["q"], [CH], scrape=blocked, sleep=lambda s: None)
    good = JobSpyAdapter(
        "indeed",
        SETTINGS,
        ["q"],
        [CH],
        scrape=lambda **kw: pd.DataFrame([ROW]),
        sleep=lambda s: None,
    )
    res = run_adapters([bad, good])
    assert res[0].aborted and res[0].jobs == []
    assert not res[1].error and len(res[1].jobs) == 1


def test_broken_source_does_not_break_run():
    class Boom:
        name = "boom"

        def fetch(self):
            raise RuntimeError("API changed")

    good = JobSpyAdapter(
        "indeed",
        SETTINGS,
        ["q"],
        [CH],
        scrape=lambda **kw: pd.DataFrame([ROW]),
        sleep=lambda s: None,
    )
    res = run_adapters([Boom(), good])
    assert "API changed" in res[0].error and len(res[1].jobs) == 1


def test_with_backoff_raises_after_max_retries():
    with pytest.raises(RateLimited):
        with_backoff(
            lambda: [],
            is_rate_limited=lambda: True,
            max_retries=2,
            base_s=1,
            max_s=2,
            label="x",
            sleep=lambda s: None,
        )


def test_jobspy_loggers_are_adopted_into_root():
    from job_radar.ingest.jobspy_adapter import _adopt_jobspy_loggers

    lg = logging.getLogger("JobSpy:Test")
    lg.propagate = False
    lg.addHandler(logging.StreamHandler())
    _adopt_jobspy_loggers()
    assert lg.propagate and not any(type(h) is logging.StreamHandler for h in lg.handlers)


def _row(n, title="Senior Data Engineer"):
    return ROW | {"id": f"li-{n}", "job_url": f"https://x/{n}", "title": title, "description": None}


def _lazy(rows, details, **kw):
    settings = SETTINGS.model_copy(update={"linkedin_detail_delay_s": (0, 0)} | kw)
    calls = []

    def scrape(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(rows)

    adapter = JobSpyAdapter(
        "linkedin",
        settings,
        ["q"],
        [CH],
        scrape=scrape,
        sleep=lambda s: None,
        is_known={"linkedin:li-1"}.__contains__,
        is_relevant=lambda title: "Intern" not in title,
        details=details,
    )
    return adapter, calls


def test_linkedin_details_only_for_new_relevant_jobs():
    asked = []

    def details(job_id):
        asked.append(job_id)
        return f"desc {job_id}"

    rows = [_row(1), _row(2), _row(3, "Data Intern"), _row(2)]
    adapter, calls = _lazy(rows, details)
    jobs = {j.job_id: j.description for j in adapter.fetch()}
    assert calls[0]["linkedin_fetch_description"] is False
    assert asked == ["2"]  # 1 conocido, 3 con título irrelevante, 2 repetido en el listado
    assert jobs == {"li-1": "", "li-3": "", "li-2": "desc 2"}
    assert adapter.warnings == []


def test_linkedin_stops_asking_after_consecutive_failures():
    asked = []

    def details(job_id):
        asked.append(job_id)
        return None

    adapter, _ = _lazy([_row(n) for n in range(2, 9)], details, linkedin_detail_max_failures=2)
    jobs = list(adapter.fetch())
    assert len(jobs) == 7 and all(not j.description for j in jobs)
    assert len(asked) == 2
    assert adapter.warnings == ["7 avisos sin descripción, se reintentan"]


def test_few_missing_descriptions_are_not_reported():
    details = {"2": "desc 2"}.get  # 3 queda sin descripción
    adapter, _ = _lazy([_row(2), _row(3)], details)
    assert {j.job_id: j.description for j in adapter.fetch()} == {"li-2": "desc 2", "li-3": ""}
    assert adapter.warnings == []


def test_truncated_listing_is_reported():
    def scrape(**kw):
        jobspy_log.error("LinkedIn response status code 429")
        return pd.DataFrame([_row(2)])

    adapter = JobSpyAdapter(
        "linkedin",
        SETTINGS,
        ["data engineer"],
        [CH],
        scrape=scrape,
        sleep=lambda s: None,
        is_known=lambda job_id: False,
        details=lambda job_id: "desc",
    )
    res = run_adapters([adapter])[0]
    assert res.error is None and len(res.jobs) == 1
    assert res.warnings == ["'data engineer' cortada en 1 avisos por 429"]


def test_cooldown_after_cut_search():
    def scrape(**kw):
        if kw["search_term"] == "data engineer":
            jobspy_log.error("LinkedIn response status code 429")
        return pd.DataFrame([_row(2 if kw["search_term"] == "data engineer" else 3)])

    pauses = []
    adapter = JobSpyAdapter(
        "linkedin",
        SETTINGS.model_copy(
            update={"cooldown_after_cut_s": (90, 90), "linkedin_detail_delay_s": (1, 1)}
        ),
        ["data engineer", "data analyst", "BI engineer"],
        [CH],
        scrape=scrape,
        sleep=pauses.append,
        is_known=lambda job_id: False,
        details=lambda job_id: "desc",
    )
    list(adapter.fetch())
    # tras la cortada, 90 s; tras una limpia, la pausa normal (0); antes de las descripciones, 0
    # porque la última búsqueda no salió cortada
    assert pauses == [90, 0, 1]


@pytest.mark.parametrize(
    ("hours_ago", "expected"),
    [
        (None, None),  # sin corrida previa: hours_old
        (3, 24),  # mínimo
        (24, 30),  # un día + margen
        (49, 55),
        (200, 72),  # Mac apagada varios días: tope hours_old
    ],
)
def test_window_hours(hours_ago, expected):
    s = SETTINGS.model_copy(
        update={
            "hours_old": 72,
            "since_last_run_min_h": 24,
            "since_last_run_margin_h": 6,
            "since_last_run": True,
        }
    )
    now = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    last = None if hours_ago is None else now - timedelta(hours=hours_ago)
    assert window_hours(s, last, now) == expected


def test_fixed_queries_use_short_window():
    calls = []

    def scrape(**kwargs):
        calls.append((kwargs["search_term"], kwargs["hours_old"]))
        return pd.DataFrame([])

    adapter = JobSpyAdapter(
        "indeed",
        SETTINGS.model_copy(update={"hours_old": 72}),
        ["data engineer", "data analyst"],
        [CH],
        scrape=scrape,
        sleep=lambda s: None,
        fixed_queries=["data engineer"],
        fixed_hours_old=30,
    )
    list(adapter.fetch())
    assert calls == [("data engineer", 30), ("data analyst", 72)]


def test_resumes_cut_search_and_retries_missing_descriptions():
    from job_radar.ingest.jobspy_adapter import Search
    from job_radar.models import RawJob

    calls, asked = [], []

    def scrape(**kw):
        calls.append(kw)
        return pd.DataFrame([_row(5), _row(6)])

    def details(job_id):
        asked.append(job_id)
        return f"desc {job_id}"

    missing = RawJob(source="linkedin", job_id="li-9", url="u", title="Data Engineer")
    adapter = JobSpyAdapter(
        "linkedin",
        SETTINGS.model_copy(update={"linkedin_detail_delay_s": (0, 0)}),
        [],
        [CH],
        scrape=scrape,
        sleep=lambda s: None,
        is_known=lambda job_id: False,
        details=details,
        plan=[Search("data engineer", CH, 29, offset=50, wanted=10)],
        retry_details=[missing],
    )
    jobs = {j.job_id: j.description for j in adapter.fetch()}
    assert calls[0]["offset"] == 50 and calls[0]["results_wanted"] == 10
    assert calls[0]["hours_old"] == 29
    assert asked == ["9", "5", "6"]
    assert jobs == {"li-9": "desc 9", "li-5": "desc 5", "li-6": "desc 6"}
    assert adapter.searches == [
        {"query": "data engineer", "country": "CH", "hours": 29, "results": 52, "cut": False}
    ]


def test_records_each_search_result():
    def scrape(**kw):
        if kw["search_term"] == "b":
            jobspy_log.error("LinkedIn response status code 429")
        return pd.DataFrame([_row(2)])

    adapter = JobSpyAdapter(
        "indeed", SETTINGS, ["a", "b"], [CH], scrape=scrape, sleep=lambda s: None
    )
    res = run_adapters([adapter])[0]
    assert [(s["query"], s["results"], s["cut"]) for s in res.searches] == [
        ("a", 1, False),
        ("b", 1, True),
    ]
