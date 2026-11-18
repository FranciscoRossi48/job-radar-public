from job_radar import db
from job_radar.cv_profile import (
    CvDocument,
    CvLanguage,
    CvSessionStore,
    ProfileDraft,
    ProfileUpdate,
    cv_scoring_yaml,
    extract_cv_text,
    generate_profile_draft,
    profile_yaml,
)


def test_text_cv_is_extracted_and_contact_details_are_redacted():
    payload = b"Sample Candidate\nsample@example.test\n+1 202 555 0187\n" + (
        b"Data Engineer with six years building Python, SQL and dbt pipelines.\n" * 3
    )
    text, kind, pages = extract_cv_text("resume.txt", "text/plain", payload)
    assert kind == "txt" and pages is None
    assert "[name removed]" in text and "Sample Candidate" not in text
    assert "[email removed]" in text and "[phone removed]" in text
    assert "sample@example.test" not in text


def test_cv_store_is_memory_only_and_single_use():
    store = CvSessionStore()
    document = store.add(
        "resume.md",
        "text/markdown",
        ("Data engineer using Python, SQL and Azure.\n" * 5).encode(),
    )
    assert store.take(document.token).filename == "resume.md"


def test_profile_generation_preserves_existing_residency_when_cv_is_silent(
    cfg, tmp_path, monkeypatch
):
    cfg = cfg.model_copy(
        update={"profile": cfg.profile.model_copy(update={"residency_countries": ["CH"]})}
    )

    class FakeEvaluator:
        def structured(self, prompt, schema, instruction):
            assert "<resume_text>" in prompt
            return (
                ProfileDraft(
                    summary="Data engineer with six years building cloud data platforms and analytics systems.",
                    languages=[],
                    residency_countries=[],
                    stack=["Python", "SQL", "Python"],
                    current_title="Data Engineer",
                    years_experience=6,
                    domains=["banking"],
                    warnings=[],
                ),
                "fake-model",
            )

    monkeypatch.setattr("job_radar.cv_profile.build_evaluator", lambda cfg, conn: FakeEvaluator())
    conn = db.connect(tmp_path / "jobs.db")
    result = generate_profile_draft(
        cfg,
        conn,
        CvDocument("token", "cv.txt", "txt", "Data Engineer " * 30, None, 0),
    )
    assert result["draft"]["residency_countries"] == cfg.profile.residency_countries
    assert {item["code"] for item in result["draft"]["languages"]} == set(cfg.profile.languages)
    assert result["draft"]["stack"] == ["Python", "SQL"]
    assert any("kept the existing setting" in warning for warning in result["draft"]["warnings"])


def test_profile_yaml_keeps_stack_aliases(cfg, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    source = cfg.root / "config" / "profile.yaml"
    (config / "profile.yaml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    update = ProfileUpdate(
        summary="Analytics engineer with five years of Python and SQL delivery experience.",
        languages=[CvLanguage(code="en", level="C1", evidence="CV")],
        residency_countries=["CH"],
        stack=["Python", "SQL"],
    )
    content = profile_yaml(tmp_path, update)
    assert "stack_aliases:" in content
    assert "Analytics engineer" in content
    assert "- Python" in content


def test_cv_scoring_yaml_enables_cv_hard_exclusions(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "scoring.yaml").write_text(
        "base: 60\nhard_exclusions:\n  seniority_junior: false\n", encoding="utf-8"
    )
    content = cv_scoring_yaml(tmp_path)
    assert "seniority_above_profile: true" in content
    assert "no_cv_stack_match: true" in content
