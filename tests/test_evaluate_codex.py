import json
from pathlib import Path

import pytest

from job_radar import db
from job_radar.evaluate import build_evaluator
from job_radar.evaluate_codex import CodexEvaluator, _classify, strict_schema
from job_radar.evaluate_common import (
    AuthenticationFailed,
    ConfigurationError,
    RetryableError,
)
from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())


class Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def conn(tmp_path, cfg):
    connection = db.connect(tmp_path / "t.db")
    prefilter = Prefilterer(cfg.prefilter)
    for index, case in enumerate(CASES[:3]):
        data = case["job"]
        job = RawJob(
            source="test",
            job_id=str(index),
            url=f"https://x/{index}",
            title=data["title"],
            company=data["company"],
            location=data["location"],
            country=data["country"],
            description=data["description"],
        )
        db.upsert_job(connection, job, prefilter.run(job))
    connection.commit()
    return connection


class FakeRunner:
    """Simula `codex exec`: escribe el JSON en --output-last-message y devuelve eventos."""

    def __init__(self, payloads):
        self.payloads = payloads if callable(payloads) else list(payloads)
        self.calls = []
        self.snapshots = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        home = Path(kwargs.get("env", {}).get("CODEX_HOME", ""))
        snapshot = {}
        if (home / "config.toml").exists():
            snapshot["config"] = (home / "config.toml").read_text()
            snapshot["auth"] = json.loads((home / "auth.json").read_text())
            snapshot["mode"] = (home / "auth.json").stat().st_mode & 0o777
        self.snapshots.append(snapshot)
        payload = self.payloads(cmd) if callable(self.payloads) else self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, Proc):
            return payload
        if payload is None:
            return Proc(
                stdout=json.dumps({"type": "error", "message": "rate limit exceeded"}),
                returncode=1,
            )
        out = Path(cmd[cmd.index("--output-last-message") + 1])
        out.write_text(json.dumps(payload), encoding="utf-8")
        return Proc(
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 13733, "output_tokens": 331},
                }
            )
        )


def by_title(cmd):
    prompt = cmd[-1]
    index = next(i for i, case in enumerate(CASES) if f"Title: {case['job']['title']}\n" in prompt)
    return CASES[index]["features"]


def test_factory_uses_codex_provider(cfg, conn):
    cfg = cfg.model_copy(
        update={"llm": cfg.llm.model_copy(update={"provider": "agentrouter_codex"})}
    )
    assert isinstance(build_evaluator(cfg, conn), CodexEvaluator)


def test_run_evaluates_scores_and_logs_usage(cfg, conn, monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")
    runner = FakeRunner(by_title)
    summary = CodexEvaluator(cfg, conn, runner=runner).run()

    assert summary == {"candidates": 3, "evaluated": 3, "failed": 0, "stopped": None}
    rows = list(conn.execute("SELECT evaluator, model FROM evaluations"))
    assert {row["evaluator"] for row in rows} == {"agentrouter"}
    assert {row["model"] for row in rows} == {cfg.llm.agentrouter.models[0].name}

    usage = db.llm_usage(conn)
    assert usage[0]["provider"] == "agentrouter"
    assert usage[0]["requests"] == 3
    assert usage[0]["tokens_in"] == 3 * 13733
    assert usage[0]["tokens_out"] == 3 * 331

    cmd, kwargs = runner.calls[0]
    assert Path(cmd[0]).name == cfg.llm.agentrouter.codex_bin
    assert cmd[1] == "exec"
    assert cmd[cmd.index("--model") + 1] == cfg.llm.agentrouter.models[0].name
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in cmd
    assert "--output-schema" in cmd
    assert kwargs["env"]["OPENAI_API_KEY"] == "test-key"
    assert kwargs["env"]["CODEX_HOME"] != str(Path.home() / ".codex")
    snapshot = runner.snapshots[0]
    assert 'wire_api = "responses"' in snapshot["config"]
    assert snapshot["auth"] == {"OPENAI_API_KEY": "test-key"}
    assert snapshot["mode"] == 0o600


def test_missing_api_key_stops_without_calling_codex(cfg, conn, monkeypatch):
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    runner = FakeRunner([])
    summary = CodexEvaluator(cfg, conn, runner=runner).run()

    assert summary["evaluated"] == 0 and summary["failed"] == 0
    assert summary["stopped"].startswith("authentication: falta AGENTROUTER_API_KEY")
    assert runner.calls == []


def test_retryable_error_retries_once(cfg, conn, monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")
    runner = FakeRunner([None, CASES[0]["features"]])
    sleeps = []
    evaluator = CodexEvaluator(cfg, conn, runner=runner, sleep=sleeps.append)
    job = conn.execute("SELECT * FROM jobs LIMIT 1").fetchone()

    features, model = evaluator.evaluate_job(job)

    assert features.seniority.value == "senior"
    assert model == cfg.llm.agentrouter.models[0].name
    assert len(runner.calls) == 2
    assert len(sleeps) == 1
    usage = db.llm_usage(conn)
    assert usage[0]["requests"] == 2 and usage[0]["ok"] == 1
    evaluator.close()


def test_retries_exhausted_stops_run(cfg, conn, monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")
    retries = cfg.llm.agentrouter.codex_max_retries
    runner = FakeRunner([None] * (retries + 1))
    summary = CodexEvaluator(cfg, conn, runner=runner, sleep=lambda _: None).run()

    assert summary["evaluated"] == 0
    assert summary["stopped"].startswith("models unavailable:")
    assert len(runner.calls) == retries + 1


def test_authentication_error_from_codex_stops_run(cfg, conn, monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")
    error = Proc(
        stdout=json.dumps({"type": "error", "message": "401 unauthorized client detected"}),
        returncode=1,
    )
    summary = CodexEvaluator(cfg, conn, runner=FakeRunner([error])).run()

    assert summary["evaluated"] == 0
    assert summary["stopped"].startswith("authentication:")


def test_invalid_features_are_counted_and_pipeline_continues(cfg, conn, monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-key")
    bad = dict(CASES[0]["features"])
    bad["seniority"] = "boss"
    runner = FakeRunner([bad, CASES[1]["features"], CASES[2]["features"]])
    summary = CodexEvaluator(cfg, conn, runner=runner).run()

    assert summary["evaluated"] == 2 and summary["failed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 2


def test_strict_schema_sets_additional_properties_and_required():
    from job_radar.models import EvaluationFeatures

    schema = strict_schema(EvaluationFeatures.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["LangReq"]["additionalProperties"] is False
    assert set(schema["$defs"]["LangReq"]["required"]) == {"lang", "level", "cefr"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("unauthorized client detected", AuthenticationFailed),
        (
            "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
            ConfigurationError,
        ),
        ("Missing environment variable: `OPENAI_API_KEY`.", ConfigurationError),
        ("Invalid schema for response_format", ConfigurationError),
        ("429 rate limit exceeded", "retry"),
        ("Reconnecting... 1/5 (rate limit exceeded)", "retry"),
        ("HTTP 503 no available channel", "retry"),
    ],
)
def test_error_classification(message, expected):
    error = _classify(message)
    if expected == "retry":
        assert isinstance(error, RetryableError)
    else:
        assert isinstance(error, expected)
