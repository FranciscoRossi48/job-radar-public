import json
from pathlib import Path

import pytest

from job_radar import db
from job_radar.config import AgentRouterModel
from job_radar.evaluate import build_evaluator
from job_radar.evaluate_agentrouter import AgentRouterEvaluator
from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


class FakeResponse:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self.data = data or {}
        self.headers = headers or {}
        self.text = json.dumps(self.data)

    def json(self):
        return self.data


class FakeClient:
    def __init__(self, responses):
        self.responses = responses if callable(responses) else list(responses)
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        response = self.responses(json) if callable(self.responses) else self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def conn(tmp_path, cfg):
    connection = db.connect(tmp_path / "t.db")
    prefilter = Prefilterer(cfg.prefilter)
    for index, case in enumerate(CASES[:3]):
        job_data = case["job"]
        job = RawJob(
            source="test",
            job_id=str(index),
            url=f"https://x/{index}",
            title=job_data["title"],
            company=job_data["company"],
            location=job_data["location"],
            country=job_data["country"],
            description=job_data["description"],
        )
        db.upsert_job(connection, job, prefilter.run(job))
    connection.commit()
    return connection


def response_for_case(index, *, fenced=False):
    content = json.dumps(CASES[index]["features"])
    if fenced:
        content = f"```json\n{content}\n```"
    return FakeResponse(
        data={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 25},
        }
    )


def by_title(payload):
    prompt = payload["messages"][1]["content"]
    index = next(i for i, case in enumerate(CASES) if f"Title: {case['job']['title']}\n" in prompt)
    return response_for_case(index)


def test_factory_uses_configured_provider(cfg, conn):
    from job_radar.evaluate_codex import CodexEvaluator

    codex_cfg = cfg.model_copy(
        update={"llm": cfg.llm.model_copy(update={"provider": "agentrouter_codex"})}
    )
    assert isinstance(build_evaluator(codex_cfg, conn), CodexEvaluator)

    http_cfg = cfg.model_copy(
        update={"llm": cfg.llm.model_copy(update={"provider": "agentrouter"})}
    )
    assert isinstance(build_evaluator(http_cfg, conn), AgentRouterEvaluator)


def test_run_evaluates_scores_and_logs_usage(cfg, conn):
    clock = FakeClock()
    client = FakeClient(by_title)
    evaluator = AgentRouterEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock)

    summary = evaluator.run()

    assert summary == {"candidates": 3, "evaluated": 3, "failed": 0, "stopped": None}
    rows = list(conn.execute("SELECT evaluator, model FROM evaluations"))
    assert {row["evaluator"] for row in rows} == {"agentrouter"}
    assert {row["model"] for row in rows} == {cfg.llm.agentrouter.models[0].name}
    usage = db.llm_usage(conn)
    assert usage[0]["provider"] == "agentrouter"
    assert usage[0]["tokens_in"] == 300 and usage[0]["tokens_out"] == 75

    url, payload = client.calls[0]
    assert url == "https://agentrouter.org/v1/chat/completions"
    assert payload["model"] == cfg.llm.agentrouter.models[0].name
    assert payload["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in payload["messages"][0]["content"]


def test_fenced_json_is_tolerated(cfg, conn):
    evaluator = AgentRouterEvaluator(
        cfg, conn, client=FakeClient([response_for_case(0, fenced=True)])
    )
    job = conn.execute("SELECT * FROM jobs WHERE id='test:0'").fetchone()
    features, model = evaluator.evaluate_job(job)
    assert features.seniority.value == "senior"
    assert model == cfg.llm.agentrouter.models[0].name


def test_503_falls_back_to_next_model(cfg, conn):
    settings = cfg.llm.agentrouter.model_copy(
        update={
            "models": [
                AgentRouterModel(name="big", rpd=100),
                AgentRouterModel(name="small", rpd=100),
            ]
        }
    )
    cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"agentrouter": settings})})
    unavailable = FakeResponse(503, {"error": {"message": "no available channel"}})
    client = FakeClient(
        [unavailable, unavailable, response_for_case(0), response_for_case(1), response_for_case(2)]
    )
    clock = FakeClock()

    summary = AgentRouterEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock).run()

    assert summary["evaluated"] == 3 and summary["failed"] == 0
    assert {row[0] for row in conn.execute("SELECT model FROM evaluations")} == {"small"}


def test_rate_limit_uses_retry_after(cfg, conn):
    limited = FakeResponse(429, {"error": {"message": "slow down"}}, {"retry-after": "20"})
    client = FakeClient([limited, response_for_case(0)])
    clock = FakeClock()
    evaluator = AgentRouterEvaluator(cfg, conn, client=client, sleep=clock.sleep, clock=clock)
    job = conn.execute("SELECT * FROM jobs WHERE id='test:0'").fetchone()

    features, _ = evaluator.evaluate_job(job)

    assert features.seniority.value == "senior"
    assert 21 in clock.sleeps


def test_authentication_error_stops_without_failing_jobs(cfg, conn):
    response = FakeResponse(401, {"error": {"message": "invalid token"}})
    summary = AgentRouterEvaluator(cfg, conn, client=FakeClient([response])).run()
    assert summary["evaluated"] == 0 and summary["failed"] == 0
    assert summary["stopped"] == "authentication: invalid token"


def test_client_uses_key_and_timeout(cfg, conn, monkeypatch):
    seen = {}
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")

    def fake_client(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("job_radar.evaluate_agentrouter.httpx.Client", fake_client)
    AgentRouterEvaluator(cfg, conn).client  # noqa: B018 — la property crea el cliente
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["headers"]["Originator"] == "job-radar"
    assert seen["headers"]["Version"] == "0.1.0"
    assert seen["timeout"] == cfg.llm.agentrouter.request_timeout_s
