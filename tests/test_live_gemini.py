"""Valida el rubric contra los 10 casos sintéticos con Gemini real.

Gasta ~10 requests de la cuota. Correr con: JOB_RADAR_LIVE=1 uv run pytest -m live -s
"""

import json
import os
from pathlib import Path

import pytest

from job_radar.evaluate_gemini import GeminiEvaluator
from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer
from job_radar.scoring import JobContext, score

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("JOB_RADAR_LIVE") != "1", reason="JOB_RADAR_LIVE=1"),
]


@pytest.fixture(scope="module")
def evaluator(cfg, tmp_path_factory):
    """JOB_RADAR_MODEL=nombre fuerza un solo modelo (para comparar calidad entre modelos)."""
    from job_radar import db
    from job_radar.config import GeminiModel

    conn = db.connect(tmp_path_factory.mktemp("live") / "t.db")
    if name := os.environ.get("JOB_RADAR_MODEL"):
        base = next((m for m in cfg.llm.gemini.models if m.name == name), GeminiModel(name=name))
        g = cfg.llm.gemini.model_copy(update={"models": [base]})
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"gemini": g})})
    return GeminiEvaluator(cfg, conn)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_rubric_on_synthetic_case(case, cfg, evaluator):
    j = case["job"]
    raw = RawJob(
        source="live",
        job_id=case["name"],
        url="u",
        title=j["title"],
        company=j["company"],
        location=j["location"],
        country=j["country"],
        description=j["description"],
    )
    pre = Prefilterer(cfg.prefilter).run(raw)
    row = {
        "title": j["title"],
        "company": j["company"],
        "location": j["location"],
        "country": j["country"],
        "desc_lang": pre.desc_lang,
        "desc_condensed": pre.desc_condensed,
        "lang_snippets": json.dumps([s.model_dump() for s in pre.snippets]),
    }
    f, model = evaluator.evaluate_job(row)
    exp = case["features"]
    print(f"\n{case['name']} [{model}]: {f.model_dump_json()}")
    assert f.residency_hard_req == exp["residency_hard_req"]
    assert f.seniority.value == exp["seniority"] or {f.seniority.value, exp["seniority"]} <= {
        "mid",
        "senior",
    }
    res = score(f, JobContext(j["location"], j["country"], pre.desc_lang), cfg.profile, cfg.scoring)
    assert res.excluded == case["expect"]["excluded"], res.breakdown
