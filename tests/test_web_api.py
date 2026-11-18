import shutil

import pytest
from fastapi.testclient import TestClient

from job_radar import db
from job_radar.models import PrefilterResult, RawJob
from job_radar.web.app import create_app


@pytest.fixture
def web_root(cfg, tmp_path):
    shutil.copytree(cfg.root / "config", tmp_path / "config")
    conn = db.connect(tmp_path / "jobs.db")
    db.upsert_job(
        conn,
        RawJob(
            source="test",
            job_id="1",
            url="https://example.test/job/1",
            title="Data Engineering Intern",
            company="Acme",
            location="Paris",
            country="FR",
            description="Build reliable data pipelines with Python and SQL. " * 12,
        ),
        PrefilterResult(excluded=False, desc_lang="en", desc_condensed="Build pipelines."),
    )
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def web_client(web_root):
    with TestClient(create_app(web_root, web_root / "missing-dist")) as client:
        yield client


def test_health_and_dashboard(web_client):
    health = web_client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"]["exists"] is True

    dashboard = web_client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["metrics"]["total"] == 1
    assert dashboard.json()["metrics"]["pending"] == 1


def test_jobs_filter_detail_and_decision(web_client):
    response = web_client.get("/api/jobs", params={"kind": "internship", "country": "FR"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Data Engineering Intern"

    detail = web_client.get("/api/jobs/test:1")
    assert detail.status_code == 200
    assert detail.json()["description_raw"].startswith("Build reliable")

    decided = web_client.put(
        "/api/jobs/test:1/decision",
        json={"decision": "applied", "note": "Applied from the web UI"},
    )
    assert decided.status_code == 200
    assert decided.json()["decision"] == "applied"


def test_config_editor_validates_before_saving(web_client, web_root):
    path = web_root / "config" / "profile.yaml"
    original = path.read_text(encoding="utf-8")
    response = web_client.put(
        "/api/settings/config/profile.yaml", json={"content": "languages: []\n"}
    )
    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == original

    valid = web_client.post(
        "/api/settings/config/profile.yaml/validate", json={"content": original}
    )
    assert valid.status_code == 200
    assert valid.json()["valid"] is True


def test_config_editor_rejects_unknown_files(web_client):
    assert web_client.get("/api/settings/config/.env").status_code == 404
    assert (
        web_client.put("/api/settings/config/.env", json={"content": "SECRET=x"}).status_code == 404
    )


def test_cv_preview_is_local_and_profile_can_be_applied(web_client, web_root):
    preview = web_client.post(
        "/api/profile/cv/preview",
        files={
            "file": (
                "resume.txt",
                ("Data engineer with Python, SQL, dbt and Azure experience.\n" * 5).encode(),
                "text/plain",
            )
        },
    )
    assert preview.status_code == 200
    assert preview.json()["token"] and preview.json()["characters"] > 100

    applied = web_client.post(
        "/api/profile/cv/apply",
        json={
            "summary": "Data engineer with five years building Python and SQL data platforms.",
            "languages": [{"code": "en", "level": "C1", "evidence": "CV"}],
            "residency_countries": ["CH"],
            "stack": ["Python", "SQL", "dbt"],
        },
    )
    assert applied.status_code == 200
    assert applied.json()["rescored"] == 0
    assert "Data engineer with five years" in (web_root / "config" / "profile.yaml").read_text(
        encoding="utf-8"
    )


def test_spa_reports_build_instruction_when_bundle_is_missing(web_client):
    response = web_client.get("/")
    assert response.status_code == 503
    assert "npm --prefix frontend" in response.json()["build"]
