"""Rescate de LinkedIn: retoma las búsquedas de hoy cortadas por 429."""

from datetime import UTC, datetime

from job_radar import daily, db
from job_radar.ingest.pipeline import RETRY_STEP, linkedin_retry_adapter
from job_radar.logs import append_run_summary, full_run_today
from job_radar.models import PrefilterResult, RawJob


def _cfg(cfg, tmp_path):
    app = cfg.app.model_copy(
        update={
            "db_path": str(tmp_path / "t.db"),
            "logs_dir": str(tmp_path / "logs"),
            "exports_dir": str(tmp_path / "exports"),
        }
    )
    searches = cfg.searches.model_copy(update={"active_countries": ["CH"]})
    return cfg.model_copy(update={"app": app, "searches": searches})


def _morning(cfg, searches):
    src = {"fetched": 80, "warnings": ["cortada"], "searches": searches}
    append_run_summary(
        cfg,
        {
            "started": datetime.now(UTC).isoformat(timespec="seconds"),
            "steps": {"ingest": {"network_errors": 0, "sources": {"jobspy:linkedin": src}}},
        },
    )


def _no_description(conn, cfg):
    job = RawJob(source="linkedin", job_id="li-9", url="u", title="Data Engineer", country="CH")
    pre = PrefilterResult(excluded=True, reason="no_description")
    db.upsert_job(conn, job, pre, cfg.app.dedup)
    conn.commit()


def test_retry_plan_resumes_cut_and_missing_searches(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    conn = db.connect(cfg.db_path)
    now = datetime.now().astimezone()
    fixed, *rotating = cfg.searches.queries_for(now.date())
    _morning(
        cfg,
        [
            {"query": fixed, "country": "CH", "hours": 29, "results": 50, "cut": True},
            {"query": rotating[0], "country": "CH", "hours": 72, "results": 30, "cut": False},
            # rotating[1] no llegó a correr (la fuente abortó por 429)
        ],
    )
    _no_description(conn, cfg)
    adapter = linkedin_retry_adapter(cfg, conn, now)
    want = cfg.searches.jobspy.results_wanted
    plan = [(s.query, s.hours, s.offset, s.wanted) for s in adapter.plan()]
    assert plan == [
        (fixed, 29, 50, want - 50),
        (rotating[1], cfg.searches.jobspy.hours_old, 0, want),
    ]
    assert [j.id for j in adapter._retry_details] == ["linkedin:li-9"]


def test_no_retry_when_clean_or_already_retried(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    conn = db.connect(cfg.db_path)
    assert linkedin_retry_adapter(cfg, conn) is None  # hoy no corrió LinkedIn
    queries = cfg.searches.queries_for(datetime.now().date())
    _morning(
        cfg,
        [{"query": q, "country": "CH", "hours": 72, "results": 5, "cut": False} for q in queries],
    )
    assert linkedin_retry_adapter(cfg, conn) is None  # todo completo
    _no_description(conn, cfg)
    assert linkedin_retry_adapter(cfg, conn) is not None
    append_run_summary(
        cfg, {"started": datetime.now(UTC).isoformat(), "steps": {RETRY_STEP: {"new": 0}}}
    )
    assert linkedin_retry_adapter(cfg, conn) is None  # una vez por día


class _Retry:
    name = "jobspy:linkedin"
    warnings: list = []
    searches: list = []

    def fetch(self):
        yield RawJob(
            source="linkedin",
            job_id="li-7",
            url="u",
            title="Senior Data Engineer",
            company="Acme",
            country="CH",
            description="We are looking for a Senior Data Engineer with SQL and Python. " * 5,
        )


def test_rescue_runs_retry_step(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    conn = db.connect(cfg.db_path)
    s = daily.run_daily(
        cfg,
        conn,
        skip_ingest=True,
        skip_evaluate=True,
        retry_factory=lambda cfg, conn: _Retry(),
        network_ok=lambda net: True,
    )
    assert s["steps"][RETRY_STEP]["new"] == 1 and s["totals"]["new"] == 1
    assert "ingest" not in s["steps"]
    append_run_summary(cfg, s)
    assert not full_run_today(cfg)  # el reintento no cuenta como corrida completa

    s = daily.run_daily(
        cfg,
        conn,
        skip_ingest=True,
        skip_evaluate=True,
        retry_factory=lambda cfg, conn: _Retry(),
        network_ok=lambda net: False,
    )
    assert RETRY_STEP not in s["steps"]
