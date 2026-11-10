import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from job_radar import daily, db
from job_radar.logs import append_run_summary, daily_run_done, last_clean_ingest, setup_logging
from job_radar.models import RawJob


class FakeAdapter:
    name = "fake"

    def fetch(self):
        yield RawJob(
            source="fake",
            job_id="1",
            url="u",
            title="Senior Data Engineer",
            company="Acme",
            country="CH",
            description="We are looking for a Senior Data Engineer with SQL and Python. " * 5,
        )
        yield RawJob(
            source="fake",
            job_id="2",
            url="u",
            title="Junior Data Engineer",
            company="Acme",
            country="CH",
            description="Junior role. " * 20,
        )


class BrokenAdapter:
    name = "broken"

    def fetch(self):
        raise RuntimeError("site down")


class FakeEvaluator:
    def __init__(self, cfg, conn):
        pass

    def run(self, limit=None):
        return {"candidates": 1, "evaluated": 1, "failed": 0, "stopped": None}


def _cfg(cfg, tmp_path):
    app = cfg.app.model_copy(
        update={
            "db_path": str(tmp_path / "t.db"),
            "logs_dir": str(tmp_path / "logs"),
            "exports_dir": str(tmp_path / "exports"),
        }
    )
    return cfg.model_copy(update={"app": app})


def test_run_daily_summary_and_isolation(cfg, tmp_path, monkeypatch):
    cfg = _cfg(cfg, tmp_path)
    monkeypatch.setattr(daily, "build_adapters", lambda cfg, ctx: [BrokenAdapter(), FakeAdapter()])
    conn = db.connect(cfg.db_path)
    s = daily.run_daily(cfg, conn, evaluator_factory=FakeEvaluator)
    t = s["totals"]
    assert (t["new"], t["excluded_prefilter"], t["evaluated"]) == (2, 1, 1)
    assert t["failed_sources"] == ["broken"] and t["failed_steps"] == []
    assert s["steps"]["export"]["path"].endswith(".csv")


def test_failing_step_does_not_stop_the_rest(cfg, tmp_path, monkeypatch):
    cfg = _cfg(cfg, tmp_path)

    def boom(cfg, ctx):
        raise RuntimeError("config rota")

    monkeypatch.setattr(daily, "build_adapters", boom)
    s = daily.run_daily(cfg, db.connect(cfg.db_path), evaluator_factory=FakeEvaluator)
    assert "config rota" in s["steps"]["ingest"]["error"]
    assert s["steps"]["evaluate"]["evaluated"] == 1 and "path" in s["steps"]["export"]
    assert s["totals"]["failed_steps"] == ["ingest"]


def test_notify_on_error(cfg, tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr(daily, "notify_macos", lambda msg, title="Job Radar": alerts.append(msg))
    cfg = _cfg(cfg, tmp_path)
    cfg = cfg.model_copy(update={"app": cfg.app.model_copy(update={"notify_on_error": True})})

    class BillingStoppedEvaluator:
        def __init__(self, cfg, conn):
            pass

        def run(self, limit=None):
            return {
                "candidates": 5,
                "evaluated": 0,
                "failed": 0,
                "stopped": "billing: sin saldo en la cuenta de Google",
            }

    monkeypatch.setattr(daily, "build_adapters", lambda cfg, ctx: [FakeAdapter()])
    daily.run_daily(cfg, db.connect(cfg.db_path), evaluator_factory=BillingStoppedEvaluator)
    assert len(alerts) == 1 and "billing:" in alerts[0]


def test_json_log_file_and_runs_jsonl(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    setup_logging(cfg)
    try:
        logging.getLogger("job_radar.run").info("hola", extra={"run_id": "abc"})
        for h in logging.getLogger().handlers:
            h.flush()
        line = (tmp_path / "logs" / "job-radar.log").read_text().strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["msg"] == "hola" and rec["run_id"] == "abc" and rec["level"] == "INFO"
        append_run_summary(cfg, {"run_id": "abc", "totals": {"new": 1}})
        append_run_summary(cfg, {"run_id": "def", "totals": {"new": 2}})
        runs = (tmp_path / "logs" / "runs.jsonl").read_text().splitlines()
        assert [json.loads(r)["run_id"] for r in runs] == ["abc", "def"]
    finally:
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
            h.close()


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ({"ingest": {"sources": {"linkedin": {"new": 3}, "ats": {"error": "x"}}}}, True),
        ({"ingest": {"sources": {"linkedin": {"error": "x"}, "ats": {"error": "y"}}}}, False),
        ({"ingest": {"error": "RuntimeError: config rota"}}, False),
        ({"evaluate": {"evaluated": 0}}, False),  # rescate (--skip-ingest): no cuenta
        ({"ingest": {"network_errors": 4, "sources": {"linkedin": {"new": 1}}}}, False),
        ({"ingest": {"network_errors": 0, "sources": {"linkedin": {"new": 1}}}}, True),
    ],
)
def test_daily_run_done_by_kind(cfg, tmp_path, steps, expected):
    cfg = _cfg(cfg, tmp_path)
    now = datetime.now().astimezone()
    append_run_summary(cfg, {"run_id": "a", "started": now.isoformat(), "steps": steps})
    assert daily_run_done(cfg, now) is expected


def test_daily_run_done_uses_local_date(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    assert daily_run_done(cfg) is False  # sin runs.jsonl
    ok = {"ingest": {"sources": {"linkedin": {"new": 1}}}}
    now = datetime.now().astimezone()
    append_run_summary(cfg, {"run_id": "x", "started": "basura", "steps": ok})
    yesterday = (now - timedelta(days=1)).astimezone(UTC).isoformat()
    append_run_summary(cfg, {"run_id": "a", "started": yesterday, "steps": ok})
    assert daily_run_done(cfg, now) is False
    append_run_summary(
        cfg, {"run_id": "b", "started": now.astimezone(UTC).isoformat(), "steps": ok}
    )
    assert daily_run_done(cfg, now) is True


def test_daily_run_done_gives_up_after_max_attempts(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    net = cfg.app.network.model_copy(update={"max_daily_attempts": 2})
    cfg = cfg.model_copy(update={"app": cfg.app.model_copy(update={"network": net})})
    now = datetime.now().astimezone()
    bad = {"ingest": {"network_errors": 3, "sources": {"linkedin": {"new": 0}}}}
    append_run_summary(cfg, {"run_id": "a", "started": now.isoformat(), "steps": bad})
    assert daily_run_done(cfg, now) is False
    append_run_summary(cfg, {"run_id": "b", "started": now.isoformat(), "steps": bad})
    assert daily_run_done(cfg, now) is True


def test_wait_for_network(cfg):
    net = cfg.app.network.model_copy(update={"check_hosts": ["a", "b"], "wait_s": 30, "poll_s": 10})
    t = [0.0]
    down = {"b"}

    def resolve(host):
        if host in down:
            raise OSError("nodename nor servname provided")

    def sleep(s):
        t[0] += s
        if t[0] >= 20:
            down.clear()  # la red vuelve a los 20 s

    assert daily.wait_for_network(net, resolve=resolve, sleep=sleep, clock=lambda: t[0]) is True
    down.add("b")
    t[0] = 0.0
    never = lambda h: (_ for _ in ()).throw(OSError("sin red"))  # noqa: E731
    assert daily.wait_for_network(net, resolve=never, sleep=sleep, clock=lambda: t[0]) is False
    assert t[0] >= 30


def test_ingest_counts_network_errors(cfg, tmp_path, monkeypatch):
    cfg = _cfg(cfg, tmp_path)

    class SwallowingAdapter:  # como JobSpy: loguea el error de red y sigue sin resultados
        name = "jobspy:linkedin"

        def fetch(self):
            logging.getLogger("JobSpy:LinkedIn").error(
                "LinkedIn: HTTPSConnectionPool(host='www.linkedin.com', port=443): "
                "Max retries exceeded with url"
            )
            return iter(())

    class DnsBrokenAdapter:
        name = "swissdevjobs"

        def fetch(self):
            raise OSError("[Errno 8] nodename nor servname provided, or not known")

    monkeypatch.setattr(
        daily,
        "build_adapters",
        lambda cfg, ctx: [SwallowingAdapter(), DnsBrokenAdapter(), FakeAdapter(), BrokenAdapter()],
    )
    s = daily.run_daily(cfg, db.connect(cfg.db_path), evaluator_factory=FakeEvaluator)
    # El "site down" de BrokenAdapter no es de red: no cuenta.
    assert s["steps"]["ingest"]["network_errors"] == 2
    assert all(not isinstance(h, daily.NetworkErrorCounter) for h in logging.getLogger().handlers)


def _summary(steps, **totals):
    base = {"new": 0, "shortlist": 0, "failed_sources": [], "failed_steps": []}
    return {"steps": steps, "totals": base | totals}


TOP = [{"score": 99.0, "company": "Keyrock", "title": "Senior Data Engineer"}]


@pytest.mark.parametrize(
    ("summary", "title", "priority"),
    [
        (
            _summary(
                {"ingest": {"network_errors": 0}, "export": {"top": TOP}}, new=24, shortlist=2
            ),
            "Job Radar: CSV listo",
            3,
        ),
        (
            _summary(
                {"ingest": {"network_errors": 5}, "export": {"top": []}},
                failed_sources=["swissdevjobs"],
            ),
            "Job Radar: CSV con problemas",
            3,
        ),
        (
            _summary({"ingest": {"error": "x"}, "export": {"top": []}}, failed_steps=["ingest"]),
            "Job Radar: falló la corrida",
            4,
        ),
        # Rescate (--skip-ingest) sin novedades: silencio.
        (_summary({"evaluate": {"stopped": None}, "export": {"top": []}}), None, None),
    ],
)
def test_ntfy_message(summary, title, priority):
    msg = daily.ntfy_message(summary)
    assert (msg and msg["title"], msg and msg["priority"]) == (title, priority)


def test_ntfy_message_content():
    s = _summary({"ingest": {"network_errors": 0}, "export": {"top": TOP}}, new=24, shortlist=2)
    msg = daily.ntfy_message(s)
    assert "2 ofertas nuevas en el CSV (de 24 avisos nuevos)" in msg["message"]
    assert "99 · Keyrock · Senior Data Engineer" in msg["message"]
    assert daily.ntfy_message(s, notify_success=False) is None
    bad = _summary({"ingest": {"network_errors": 2}, "export": {"top": []}})
    assert "2 errores de red" in daily.ntfy_message(bad, notify_success=False)["message"]


def test_send_ntfy(cfg, monkeypatch):
    sent = []

    class Resp:
        def raise_for_status(self):
            pass

    def post(url, json, timeout):
        sent.append((url, json))
        return Resp()

    msg = {"title": "t", "message": "m", "priority": 3, "tags": []}
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert daily.send_ntfy(cfg, msg, post=post) is False and not sent
    monkeypatch.setenv("NTFY_TOPIC", "job-radar-test")
    assert daily.send_ntfy(cfg, msg, post=post) is True
    assert sent == [("https://ntfy.sh", {"topic": "job-radar-test", **msg})]


def test_send_ntfy_retries(cfg, monkeypatch):
    calls, waits = [], []

    class Resp:
        def raise_for_status(self):
            pass

    def post(url, json, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("sin red")
        return Resp()

    monkeypatch.setenv("NTFY_TOPIC", "job-radar-test")
    msg = {"title": "t", "message": "m", "priority": 3, "tags": []}
    assert daily.send_ntfy(cfg, msg, post=post, sleep=waits.append) is True
    assert len(calls) == 3 and len(waits) == 2
    calls.clear()

    def down(url, json, timeout):
        calls.append(url)
        raise OSError("sin red")

    assert daily.send_ntfy(cfg, msg, post=down, sleep=waits.append) is False
    assert len(calls) == cfg.app.ntfy.retries


def test_ntfy_reports_partial_source():
    s = {
        "steps": {"ingest": {"network_errors": 0}, "export": {"top": []}},
        "totals": {
            "new": 5,
            "shortlist": 1,
            "partial_sources": {"jobspy:linkedin": ["'q' cortada en 40 avisos por 429"]},
        },
    }
    msg = daily.ntfy_message(s)
    assert msg["title"] == "Job Radar: CSV con problemas"
    assert "jobspy:linkedin: 'q' cortada en 40 avisos por 429" in msg["message"]


def _ingest_summary(when):
    source = {"a": {"fetched": 1}}
    return {"started": when, "steps": {"ingest": {"network_errors": 0, "sources": source}}}


def test_rescue_warns_when_no_search_today(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    conn = db.connect(cfg.db_path)
    s = daily.run_daily(cfg, conn, skip_ingest=True, skip_evaluate=True)
    assert s["no_search_today"] is True
    msg = daily.ntfy_message(s, notify_success=False)
    assert msg["title"] == "Job Radar: hoy no hubo búsqueda" and msg["priority"] == 4

    append_run_summary(cfg, _ingest_summary(datetime.now(UTC).isoformat()))
    s = daily.run_daily(cfg, conn, skip_ingest=True, skip_evaluate=True)
    assert s["no_search_today"] is False
    assert daily.ntfy_message(s) is None

    def broken(url, json, timeout):
        raise OSError("sin red")

    assert daily.send_ntfy(cfg, msg, post=broken) is False


def test_run_daily_sends_ntfy_with_top(cfg, tmp_path, monkeypatch):
    cfg = _cfg(cfg, tmp_path)
    nt = cfg.app.ntfy.model_copy(update={"enabled": True})
    cfg = cfg.model_copy(update={"app": cfg.app.model_copy(update={"ntfy": nt})})
    sent = []
    monkeypatch.setattr(daily, "send_ntfy", lambda cfg, msg: sent.append(msg))
    monkeypatch.setattr(daily, "build_adapters", lambda cfg, ctx: [FakeAdapter()])
    s = daily.run_daily(cfg, db.connect(cfg.db_path), evaluator_factory=FakeEvaluator)
    assert len(sent) == 1 and sent[0]["title"] == "Job Radar: CSV listo"
    assert s["steps"]["export"]["top"] == []  # FakeEvaluator no guarda evaluaciones


def test_log_rotation(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    lc = cfg.app.logging.model_copy(update={"max_bytes": 2000, "backup_count": 2})
    cfg = cfg.model_copy(update={"app": cfg.app.model_copy(update={"logging": lc})})
    setup_logging(cfg)
    try:
        for i in range(200):
            logging.getLogger("job_radar.test").info("línea %d %s", i, "x" * 50)
        files = sorted(p.name for p in (tmp_path / "logs").iterdir())
        assert files == ["job-radar.log", "job-radar.log.1", "job-radar.log.2"]
    finally:
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
            h.close()


def test_last_clean_ingest_skips_cut_and_failed_runs(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path)
    assert last_clean_ingest(cfg, "jobspy:linkedin") is None
    t0 = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)

    def run(hours, source):
        steps = {"ingest": {"sources": {"jobspy:linkedin": source}}}
        started = (t0 + timedelta(hours=hours)).isoformat()
        append_run_summary(cfg, {"run_id": str(hours), "started": started, "steps": steps})

    run(0, {"new": 3})
    run(24, {"new": 3, "warnings": ["'data engineer' cortada en 10 avisos por 429"]})
    run(48, {"error": "RateLimited"})
    run(49, {"new": 1, "warnings": ["7 avisos sin descripción, se reintentan"]})  # cuenta
    assert last_clean_ingest(cfg, "jobspy:linkedin") == t0 + timedelta(hours=49)
    assert last_clean_ingest(cfg, "jobspy:indeed") is None
