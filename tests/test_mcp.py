import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_radar import db
from job_radar.mcp_server import (
    build_server,
    desktop_config,
    tool_get_applications,
    tool_get_batch,
    tool_get_job_detail,
    tool_get_shortlist,
    tool_get_stats,
    tool_save_evaluations,
    tool_set_decisions,
)
from job_radar.models import EvaluationFeatures, PrefilterResult, RawJob
from job_radar.scoring import ScoreResult

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())
FEATURES = EvaluationFeatures(**CASES[0]["features"])


@pytest.fixture
def env(tmp_path, cfg):
    cfg = cfg.model_copy(
        update={"app": cfg.app.model_copy(update={"db_path": str(tmp_path / "t.db")})}
    )
    conn = db.connect(cfg.db_path)

    def add(jid, score=None, evaluator="gemini", status_pending=True):
        job = RawJob(
            source="t",
            job_id=jid,
            url=f"https://x/{jid}",
            title=f"Data Engineer {jid}",
            company="Acme",
            location="Zürich",
            country="CH",
            description=f"desc {jid}",
        )
        db.upsert_job(conn, job, PrefilterResult(excluded=False, desc_condensed=f"cond {jid}"))
        if score is not None:
            db.insert_evaluation(
                conn,
                job_id=f"t:{jid}",
                evaluator=evaluator,
                model="m",
                prompt_version="v1",
                features=FEATURES,
                result=ScoreResult(score, False, []),
            )
        conn.commit()

    for jid, s in [("a", 95), ("b", 78), ("c", 60), ("d", 40), ("e", 70)]:
        add(jid, s)
    add("e", None)  # ya existe; solo refresca
    db.insert_evaluation(
        conn,
        job_id="t:e",
        evaluator="claude",
        model="m",
        prompt_version="v1",
        features=FEATURES,
        result=ScoreResult(72, False, []),
    )
    add("p1")
    add("p2")
    conn.commit()
    return cfg, conn


def test_shortlist_returns_doubtful_closest_to_threshold_first(env):
    cfg, conn = env
    out = tool_get_batch(cfg, conn, n=10, mode="shortlist")
    ids = [j["id"] for j in out["jobs"]]
    # 95 y 40 fuera de rango; e ya revisado por Claude; b (78) más cerca de 75 que c (60)
    assert ids == ["t:b", "t:c"]
    assert out["jobs"][0]["previous_evaluation"]["score"] == 78
    assert out["jobs"][0]["desc_condensed"] == "cond b"
    guide = out["evaluation_guide"]
    assert "residency_hard_req" in guide and "de B1" in guide
    # quedaron reclamados: un segundo pedido no los repite
    assert tool_get_batch(cfg, conn, n=10, mode="shortlist")["count"] == 0


def test_pending_mode_and_lease_expiry(env):
    cfg, conn = env
    out = tool_get_batch(cfg, conn, n=1, mode="pending")
    assert out["count"] == 1 and out["jobs"][0]["previous_evaluation"] is None
    first = out["jobs"][0]["id"]
    second = tool_get_batch(cfg, conn, n=5, mode="pending")["jobs"]
    assert [j["id"] for j in second] != [first] and len(second) == 1
    old = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    conn.execute("UPDATE jobs SET claimed_at = ? WHERE id = ?", (old, first))
    conn.commit()
    assert [j["id"] for j in tool_get_batch(cfg, conn, n=5, mode="pending")["jobs"]] == [first]


def test_get_batch_rejects_unknown_mode(env):
    cfg, conn = env
    with pytest.raises(ValueError):
        tool_get_batch(cfg, conn, mode="all")


def test_save_evaluations_partial_failure(env):
    cfg, conn = env
    tool_get_batch(cfg, conn, n=10, mode="shortlist")
    good = {**CASES[1]["features"], "job_id": "t:b"}  # must reside -> excluido
    bad_enum = {**CASES[0]["features"], "job_id": "t:c", "seniority": "principal"}
    too_long = {**CASES[0]["features"], "job_id": "t:c", "rationale": "word " * 30}
    unknown = {**CASES[0]["features"], "job_id": "t:zzz"}
    out = tool_save_evaluations(cfg, conn, [good, bad_enum, too_long, unknown], model="claude-x")
    assert out["saved"] == 1
    assert [f["index"] for f in out["failed"]] == [1, 2, 3]
    assert any("seniority" in e for e in out["failed"][0]["errors"])
    assert any("25 words" in e for e in out["failed"][1]["errors"])
    assert out["failed"][2]["errors"] == ["job_id: unknown job"]
    assert out["results"][0]["excluded"] is True
    row = conn.execute("SELECT claimed_at FROM jobs WHERE id='t:b'").fetchone()
    assert row["claimed_at"] is None  # lease liberado
    ev = conn.execute(
        "SELECT evaluator, model FROM evaluations WHERE job_id='t:b' ORDER BY id DESC"
    ).fetchone()
    assert tuple(ev) == ("claude", "claude-x")
    # la evaluación de Claude pasa a ser la vigente (prioridad claude > gemini)
    detail = tool_get_job_detail(cfg, conn, "t:b")
    assert detail["description"] == "desc b" and len(detail["evaluations"]) == 2
    sl = tool_get_shortlist(cfg, conn, min_score=0, days=1)
    b = next(j for j in sl["jobs"] if j["id"] == "t:b")
    assert b["evaluator"] == "claude" and b["score"] == 0


def test_shortlist_and_stats(env):
    cfg, conn = env
    sl = tool_get_shortlist(cfg, conn, min_score=75, days=1)
    assert [j["id"] for j in sl["jobs"]] == ["t:a", "t:b"]
    st = tool_get_stats(cfg, conn)
    assert st["by_status"] == {"evaluated": 5, "pending": 2}
    assert st["evaluations_by_evaluator"] == {"gemini": 5, "claude": 1}


def test_job_detail_unknown(env):
    cfg, conn = env
    with pytest.raises(ValueError):
        tool_get_job_detail(cfg, conn, "nope")


def test_protocol_level(env):
    cfg, _ = env
    server = build_server(cfg)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        prompts = {p.name for p in await server.list_prompts()}
        stats = await server.call_tool("get_stats", {})
        prompt = await server.get_prompt("rubric")
        batch = await server.call_tool("get_batch", {"n": 2, "mode": "shortlist"})
        return tools, prompts, stats, prompt, batch

    tools, prompts, stats, prompt, batch = asyncio.run(go())
    assert tools == {
        "get_batch",
        "save_evaluations",
        "get_job_detail",
        "get_shortlist",
        "get_stats",
        "set_decisions",
        "get_applications",
    }
    assert prompts == {"rubric"}
    assert not stats.is_error and json.loads(stats.content[0].text)["by_status"]["pending"] == 2
    text = prompt.messages[0].content.text
    assert "residency_hard_req" in text and "Stack list:" in text and "save_evaluations" in text
    assert json.loads(batch.content[0].text)["count"] == 2


def test_desktop_config_uses_absolute_paths(cfg):
    entry = desktop_config(cfg)["mcpServers"]["job-radar"]
    assert entry["command"].startswith("/") and entry["command"].endswith("uv")
    assert entry["args"][1].startswith("/") and entry["args"][-1] == "job-radar-mcp"


def test_shortlist_only_new_and_history(env):
    cfg, conn = env
    db.mark_exported(conn, ["t:a"])
    conn.commit()
    new = tool_get_shortlist(cfg, conn, min_score=75, days=None, only_new=True)
    assert [j["id"] for j in new["jobs"]] == ["t:b"]
    hist = tool_get_shortlist(cfg, conn, min_score=75, days=None)
    assert {j["id"]: j["already_reported"] for j in hist["jobs"]} == {"t:a": True, "t:b": False}


def test_get_batch_custom_range_best_first(env):
    cfg, conn = env
    out = tool_get_batch(cfg, conn, n=10, mode="shortlist", min_score=60, max_score=100)
    # a (95), b (78), c (60); e ya revisado por Claude; d (40) fuera de rango
    assert [j["id"] for j in out["jobs"]] == ["t:a", "t:b", "t:c"]


def test_decisions_hide_jobs_and_track_applications(env):
    cfg, conn = env
    out = tool_set_decisions(
        cfg,
        conn,
        [
            {"job_id": "t:a", "decision": "applied", "note": "CV + carta"},
            {"job_id": "t:b", "decision": "discarded"},
            {"job_id": "t:c", "decision": "maybe"},
            {"job_id": "t:zzz", "decision": "applied"},
            {"decision": "applied"},
        ],
    )
    assert out["saved"] == 2 and [f["index"] for f in out["failed"]] == [2, 3, 4]
    # ya no aparecen en lotes ni en el shortlist por defecto
    batch = tool_get_batch(cfg, conn, n=10, mode="shortlist", min_score=0, max_score=100)
    assert {j["id"] for j in batch["jobs"]} == {"t:c", "t:d"}
    sl = tool_get_shortlist(cfg, conn, min_score=0, days=None)
    assert "t:a" not in {j["id"] for j in sl["jobs"]} and "t:b" not in {j["id"] for j in sl["jobs"]}
    full = tool_get_shortlist(cfg, conn, min_score=0, days=None, include_decided=True)
    assert {j["id"]: j["decision"] for j in full["jobs"]}["t:a"] == "applied"
    apps = tool_get_applications(cfg, conn)
    assert apps["count"] == 1 and apps["applications"][0]["note"] == "CV + carta"
    # deshacer
    tool_set_decisions(cfg, conn, [{"job_id": "t:b", "decision": None}])
    sl = tool_get_shortlist(cfg, conn, min_score=0, days=None)
    assert "t:b" in {j["id"] for j in sl["jobs"]}


def test_get_batch_days_looks_back_further(env):
    cfg, conn = env
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds")
    conn.execute("UPDATE evaluations SET created_at = ? WHERE job_id = 't:a'", (old,))
    conn.commit()
    today = tool_get_batch(cfg, conn, n=10, mode="shortlist", min_score=60, max_score=100)
    assert "t:a" not in {j["id"] for j in today["jobs"]}
    conn.execute("UPDATE jobs SET claimed_at = NULL, claimed_by = NULL")
    conn.commit()
    week = tool_get_batch(cfg, conn, n=10, mode="shortlist", min_score=60, max_score=100, days=7)
    assert [j["id"] for j in week["jobs"]][0] == "t:a"


@pytest.mark.parametrize("mode", ["shortlist", "pending"])
def test_paused_country_not_offered(env, mode):
    cfg, conn, *_ = env
    conn.execute("UPDATE jobs SET country = 'NL' WHERE id IN ('t:a', 't:e')")
    conn.commit()
    active = cfg.searches.model_copy(update={"active_countries": ["CH"]})
    cfg = cfg.model_copy(update={"searches": active})
    sl = tool_get_shortlist(cfg, conn, min_score=0, days=None)
    assert sl["count"] == 3 and "t:a" not in [j["id"] for j in sl["jobs"]]
    batch = tool_get_batch(cfg, conn, n=10, mode=mode, min_score=0, max_score=100)
    assert batch["jobs"] and not {"t:a", "t:e"} & {j["id"] for j in batch["jobs"]}
