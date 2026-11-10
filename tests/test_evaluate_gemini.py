import json
from pathlib import Path

import pytest
from google.genai import errors

from job_radar import db
from job_radar.evaluate_gemini import GeminiEvaluator, RateLimiter, build_prompt
from job_radar.export import export_csv, rescore
from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeModels:
    def __init__(self, responses):
        self.responses = responses if callable(responses) else list(responses)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        r = self.responses.pop(0) if isinstance(self.responses, list) else self.responses(contents)
        if isinstance(r, Exception):
            raise r
        return type("Resp", (), {"text": r})()


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def _quota_error(per_day=False, delay="20s"):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]
    if per_day:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
            }
        )
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "quota",
                "details": details,
            }
        },
    )


@pytest.fixture
def conn(tmp_path, cfg):
    c = db.connect(tmp_path / "t.db")
    pre = Prefilterer(cfg.prefilter)
    for i, case in enumerate(CASES[:3]):
        j = case["job"]
        job = RawJob(
            source="test",
            job_id=str(i),
            url=f"https://x/{i}",
            title=j["title"],
            company=j["company"],
            location=j["location"],
            country=j["country"],
            description=j["description"],
        )
        db.upsert_job(c, job, pre.run(job))
    c.commit()
    return c


def _features(i):
    return json.dumps(CASES[i]["features"])


def by_title(contents):
    """Responde con las features del caso cuyo título aparece en el prompt (orden-independiente)."""
    case = next(c for c in CASES if f"Title: {c['job']['title']}\n" in contents)
    return json.dumps(case["features"])


def test_prompt_contains_only_condensed_and_snippets(conn, cfg):
    job = conn.execute("SELECT * FROM jobs WHERE id='test:1'").fetchone()
    p = build_prompt(job, cfg.profile)
    assert "must currently reside in Switzerland" in p  # snippet de evidencia
    assert "Stack list:" in p and "de B1" in p
    assert job["description_raw"] not in p or len(job["description_raw"]) < 2000


def test_run_evaluates_and_scores(conn, cfg):
    # pending_for_evaluation ordena por first_seen DESC, id -> test:0, test:1, test:2
    clock = FakeClock()
    client = FakeClient(by_title)
    ev = GeminiEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock)
    summary = ev.run()
    assert summary["evaluated"] == 3 and summary["failed"] == 0
    rows = {r["job_id"]: r for r in conn.execute("SELECT * FROM evaluations")}
    assert rows["test:0"]["score_computed"] >= 90
    assert rows["test:1"]["score_computed"] == 0  # must reside
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status='evaluated'").fetchone()[0] == 3

    path, n = export_csv(conn, cfg.model_copy(update={"root": conn_root(conn)}))
    assert n == 1 and "Limmat Private Bank" in path.read_text()


def conn_root(conn):
    return Path(conn.execute("PRAGMA database_list").fetchone()["file"]).parent


def test_retry_on_resource_exhausted_uses_retry_delay(conn, cfg):
    clock = FakeClock()
    client = FakeClient([_quota_error(), _features(0)])
    ev = GeminiEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock)
    job = conn.execute("SELECT * FROM jobs WHERE id='test:0'").fetchone()
    f, model = ev.evaluate_job(job)
    assert f.seniority.value == "senior" and model == cfg.llm.gemini.models[0].name
    assert 21 in clock.sleeps


def _with_models(cfg, *models):
    from job_radar.config import GeminiModel

    g = cfg.llm.gemini.model_copy(update={"models": [GeminiModel(**m) for m in models]})
    return cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"gemini": g})})


def test_daily_quota_falls_back_to_next_model_then_stops(conn, cfg):
    cfg2 = _with_models(cfg, {"name": "big", "rpd": 100}, {"name": "lite", "rpd": 100})
    clock = FakeClock()
    client = FakeClient(
        [_features(0), _quota_error(per_day=True), _features(1), _quota_error(per_day=True)]
    )
    ev = GeminiEvaluator(cfg2, conn, client=client, sleep=clock.sleep, clock=clock)
    summary = ev.run()
    assert summary["evaluated"] == 2 and summary["stopped"].startswith("daily quota")
    models = [r[0] for r in conn.execute("SELECT model FROM evaluations ORDER BY id")]
    assert models == ["big", "lite"]


def test_local_rpd_cap_per_model(conn, cfg):
    cfg2 = _with_models(cfg, {"name": "big", "rpd": 1}, {"name": "lite", "rpd": 1})
    clock = FakeClock()
    ev = GeminiEvaluator(
        cfg2, conn, client=FakeClient([_features(0)] * 3), sleep=clock.sleep, clock=clock
    )
    summary = ev.run()
    assert summary["evaluated"] == 2 and summary["stopped"]


def test_invalid_response_does_not_break_run(conn, cfg):
    clock = FakeClock()
    long = json.loads(_features(2))
    long["rationale"] = "word " * 40  # se recorta a 25 palabras
    client = FakeClient(["not json", '{"foo": 1}', json.dumps(long)])
    ev = GeminiEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock)
    summary = ev.run()
    assert summary == {"candidates": 3, "evaluated": 1, "failed": 2, "stopped": None}


def test_rate_limiter_rpm():
    clock = FakeClock()
    rl = RateLimiter(rpm=10, clock=clock, sleep=clock.sleep)
    for _ in range(25):
        rl.acquire()
    assert clock.t >= 120  # 25 llamadas a 10 RPM -> al menos 2 ventanas de espera


def test_rescore_applies_new_weights(conn, cfg):
    clock = FakeClock()
    GeminiEvaluator(
        cfg,
        conn,
        client=FakeClient(by_title),
        sleep=clock.sleep,
        clock=clock,
    ).run()
    # Sin bonus de país, para que ningún score quede recortado en 100 y la resta sea exacta.
    q = "SELECT score_computed FROM evaluations WHERE job_id='test:0'"
    bonus = cfg.scoring.bonus.model_copy(update={"country": {}})
    cfg0 = cfg.model_copy(update={"scoring": cfg.scoring.model_copy(update={"bonus": bonus})})
    rescore(conn, cfg0)
    before = conn.execute(q).fetchone()[0]
    cfg2 = cfg0.model_copy(update={"scoring": cfg0.scoring.model_copy(update={"base": 40})})
    assert rescore(conn, cfg2) == 3
    assert conn.execute(q).fetchone()[0] == before - 20


def _unavailable():
    return errors.ServerError(
        503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}}
    )


def test_persistent_503_falls_back_to_next_model(conn, cfg):
    cfg2 = _with_models(cfg, {"name": "big", "rpd": 100}, {"name": "lite", "rpd": 100})
    clock = FakeClock()
    client = FakeClient([_unavailable(), _unavailable(), _features(0), _features(1), _features(2)])
    ev = GeminiEvaluator(cfg2, conn, client=client, sleep=clock.sleep, clock=clock)
    summary = ev.run()
    assert summary["evaluated"] == 3 and summary["failed"] == 0
    models = {r[0] for r in conn.execute("SELECT model FROM evaluations")}
    assert models == {"lite"}  # big se salteó tras 2 errores 503


def test_all_models_down_stops_run_without_failing_jobs(conn, cfg):
    cfg2 = _with_models(cfg, {"name": "big", "rpd": 100}, {"name": "lite", "rpd": 100})
    clock = FakeClock()
    ev = GeminiEvaluator(
        cfg2,
        conn,
        client=FakeClient([_unavailable()] * (2 + cfg.llm.gemini.last_model_retries)),
        sleep=clock.sleep,
        clock=clock,
    )
    summary = ev.run()
    assert summary["evaluated"] == 0 and summary["failed"] == 0
    assert summary["stopped"].startswith("models unavailable")


def test_last_model_gets_more_patience(conn, cfg):
    cfg2 = _with_models(cfg, {"name": "big", "rpd": 100}, {"name": "lite", "rpd": 100})
    clock = FakeClock()
    # big cae 2 veces (se saltea); lite falla 4 veces de a ratos pero se recupera
    client = FakeClient(
        [
            _unavailable(),
            _unavailable(),
            *[_unavailable()] * 4,
            _features(0),
            _features(1),
            _features(2),
        ]
    )
    ev = GeminiEvaluator(cfg2, conn, client=client, sleep=clock.sleep, clock=clock)
    summary = ev.run()
    assert summary["evaluated"] == 3 and summary["stopped"] is None
    assert "lite" not in ev.unavailable


def test_billing_exhausted_stops_run(conn, cfg):
    no_credit = errors.ClientError(
        402,
        {
            "error": {
                "code": 402,
                "status": "PAYMENT_REQUIRED",
                "message": "prepayment credits depleted",
            }
        },
    )
    clock = FakeClock()
    ev = GeminiEvaluator(
        cfg, conn, client=FakeClient([_features(0), no_credit]), sleep=clock.sleep, clock=clock
    )
    summary = ev.run()
    assert summary["evaluated"] == 1 and summary["failed"] == 0
    assert summary["stopped"].startswith("billing")


def test_token_usage_is_logged(conn, cfg):
    usage = type(
        "U",
        (),
        {"prompt_token_count": 1200, "candidates_token_count": 150, "thoughts_token_count": 50},
    )()

    class WithUsage(FakeModels):
        def generate_content(self, model, contents, config):
            r = super().generate_content(model, contents, config)
            r.usage_metadata = usage
            return r

    client = FakeClient([])
    client.models = WithUsage(by_title)
    clock = FakeClock()
    GeminiEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock).run()
    rows = db.llm_usage(conn)
    assert (
        rows[0]["requests"] == 3 and rows[0]["tokens_in"] == 3600 and rows[0]["tokens_out"] == 600
    )


def test_client_has_request_timeout(cfg, conn, monkeypatch):
    from google import genai

    seen = {}
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(genai, "Client", lambda **kw: seen.update(kw) or object())
    GeminiEvaluator(cfg, conn).client  # noqa: B018 — la property crea el cliente
    assert seen["http_options"].timeout == cfg.llm.gemini.request_timeout_s * 1000
