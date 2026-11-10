import json
from pathlib import Path

import pytest

from job_radar.models import EvaluationFeatures
from job_radar.scoring import JobContext, score

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())


def _run(case, cfg, profile=None):
    f = EvaluationFeatures(**case["features"])
    j = case["job"]
    ctx = JobContext(location=j["location"], country=j["country"], desc_lang=j["desc_lang"])
    return score(f, ctx, profile or cfg.profile, cfg.scoring)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_synthetic_cases(case, cfg):
    res = _run(case, cfg)
    exp = case["expect"]
    assert res.excluded is exp["excluded"], res.breakdown
    if exp.get("reason"):
        assert any(exp["reason"] in r for r in res.exclusion_reasons), res.exclusion_reasons
    if "min_score" in exp:
        assert res.score >= exp["min_score"], res.breakdown
    if "max_score" in exp:
        assert res.score <= exp["max_score"], res.breakdown


def _case(name):
    return next(c for c in CASES if c["name"] == name)


def test_work_permit_vs_must_reside(cfg):
    assert not _run(_case("work_permit_is_not_residency"), cfg).excluded
    assert _run(_case("must_currently_reside_in_switzerland"), cfg).excluded


def test_residency_ok_after_moving(cfg):
    moved = cfg.profile.model_copy(update={"residency_countries": ["CH"]})
    assert not _run(_case("must_currently_reside_in_switzerland"), cfg, moved).excluded
    # vivir en CH no habilita "must live in NL"
    assert _run(_case("local_candidates_only_nl"), cfg, moved).excluded


def test_residency_without_country_excludes(cfg):
    case = json.loads(json.dumps(_case("work_permit_is_not_residency")))
    case["features"] |= {"residency_hard_req": True, "residency_country": None}
    assert _run(case, cfg).excluded


@pytest.mark.parametrize(
    "lang,cefr,excluded",
    [
        ("de", "A2", False),
        ("de", "B1", False),
        ("de", "B2", True),
        ("de", None, False),
        ("fr", "A1", True),
        ("nl", None, True),
        ("it", "B1", True),
    ],
)
def test_language_thresholds(cfg, lang, cefr, excluded):
    case = json.loads(json.dumps(_case("work_permit_is_not_residency")))
    case["features"]["lang_required"] = [{"lang": lang, "level": "required", "cefr": cefr}]
    assert _run(case, cfg).excluded is excluded


def test_junior_excluded(cfg):
    case = json.loads(json.dumps(_case("german_nice_to_have")))
    case["features"]["seniority"] = "junior"
    assert _run(case, cfg).exclusion_reasons == ["junior"]


def test_security_clearance_excluded(cfg):
    case = json.loads(json.dumps(_case("dutch_is_a_plus_onsite")))
    case["features"]["red_flags"] = ["security clearance required"]
    res = _run(case, cfg)
    assert res.excluded and res.exclusion_reasons == ["security clearance required"]

    # También excluye de forma determinística si aparece en el texto aunque el LLM no lo ponga en red_flags
    f = EvaluationFeatures(**_case("dutch_is_a_plus_onsite")["features"])
    ctx = JobContext(
        location="The Hague",
        country="NL",
        text="Data Engineer for NCIA. Valid National or NATO Secret personal security clearance.",
    )
    res2 = score(f, ctx, cfg.profile, cfg.scoring)
    assert res2.excluded and res2.exclusion_reasons == ["security clearance required"]

    # Un background check común ("security screening") no es security clearance
    ctx_ok = JobContext(
        location="Vlissingen",
        country="NL",
        text="A pre-assignment and security screening form part of the recruitment process.",
    )
    assert not score(f, ctx_ok, cfg.profile, cfg.scoring).excluded


@pytest.mark.parametrize(
    "text, excluded",
    [
        ("No security clearance required.", False),
        ("Security clearance is not required for this role.", False),
        ("You will not need a security clearance.", False),
        ("Keine Sicherheitsprüfung PSP notwendig.", False),
        ("Must hold an active security clearance.", True),
        ("Eligible for NATO Secret clearance.", True),
        # La negación de otra oración no cuenta
        ("Security clearance needed. No relocation package.", True),
        ("Must hold security clearance; no exceptions.", True),
        # Un match negado y otro no: excluye
        ("No security clearance needed now, but NATO Secret within 6 months.", True),
    ],
)
def test_security_clearance_negation(cfg, text, excluded):
    f = EvaluationFeatures(**_case("dutch_is_a_plus_onsite")["features"])
    ctx = JobContext(location="The Hague", country="NL", text=text)
    assert score(f, ctx, cfg.profile, cfg.scoring).excluded is excluded


def test_security_clearance_negated_red_flag(cfg):
    case = json.loads(json.dumps(_case("dutch_is_a_plus_onsite")))
    case["features"]["red_flags"] = ["no security clearance required"]
    assert not _run(case, cfg).excluded


def test_geneva_prior_penalizes_without_excluding(cfg):
    case = json.loads(json.dumps(_case("work_permit_is_not_residency")))
    base = _run(case, cfg).score
    case["job"]["location"] = "Lausanne, VD"
    res = _run(case, cfg)
    assert not res.excluded and res.score < base
    assert any("Romandie" in b.get("rule", "") for b in res.breakdown)


def test_stack_match_only_counts_profile_tech(cfg):
    case = json.loads(json.dumps(_case("german_nice_to_have")))
    case["features"]["stack_match"] = ["dbt", "Fivetran", "Qlik"]
    rules = [b["rule"] for b in _run(case, cfg).breakdown]
    assert "stack: dbt" in rules


def test_score_is_clamped(cfg):
    case = json.loads(json.dumps(_case("work_permit_is_not_residency")))
    cfg2 = cfg.scoring.model_copy(update={"base": 200})
    f = EvaluationFeatures(**case["features"])
    assert score(f, JobContext(), cfg.profile, cfg2).score == 100


def test_rationale_limit():
    data = json.loads(json.dumps(_case("german_nice_to_have")))["features"]
    data["rationale"] = "word " * 26
    with pytest.raises(ValueError):
        EvaluationFeatures(**data)


def test_nice_language_penalties(cfg):
    rules = [b["rule"] for b in _run(_case("junior_senior_combined_title"), cfg).breakdown]
    assert "lang nice: de" not in rules  # "German B1 helpful" con alemán B1
    rules = [b["rule"] for b in _run(_case("german_nice_to_have"), cfg).breakdown]
    assert "lang nice: de" not in rules  # alemán "a plus" no penaliza (lang_nice.de = 0)
    rules = [b["rule"] for b in _run(_case("dutch_is_a_plus_onsite"), cfg).breakdown]
    assert "lang nice: nl" in rules  # neerlandés "a plus" sí


def test_red_flags_duplicating_other_fields_are_ignored(cfg):
    case = json.loads(json.dumps(_case("dutch_is_a_plus_onsite")))
    base = _run(case, cfg).score
    case["features"]["red_flags"] = ["100% on-site only", "Dutch language preferred"]
    assert _run(case, cfg).score == base
    case["features"]["red_flags"] = ["Fixed-term contract of 4 months"]
    assert _run(case, cfg).score == base - cfg.scoring.penalties.red_flag


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Experience with Google Cloud Platform and BigQuery.", {"GCP", "BigQuery"}),
        (
            "Our stack: GCP, Dataform, Cloud Composer and Looker.",
            {"GCP", "Dataform", "Cloud Composer", "Looker"},
        ),
        (
            "Power BI (DAX), SSIS packages and Microsoft Purview.",
            {"Power BI", "DAX", "SSIS", "Azure Purview"},
        ),
        ("PostgreSQL and MySQL administration.", set()),  # SQL no matchea dentro de PostgreSQL
        ("We love PySpark and dimensional modelling.", {"Spark", "Data Modeling"}),
        ("T-SQL and Python.", {"SQL", "Python"}),
        ("Spark-based pipelines with dbt-core.", {"Spark", "dbt"}),
    ],
)
def test_stack_in_text(cfg, text, expected):
    from job_radar.scoring import stack_in_text

    assert stack_in_text(text, cfg.profile) == expected


def test_stack_union_llm_and_text_counts_each_tech_once(cfg):
    case = json.loads(json.dumps(_case("german_nice_to_have")))
    case["features"]["stack_match"] = ["bigquery", "dbt"]  # el LLM puede variar mayúsculas
    f = EvaluationFeatures(**case["features"])
    ctx = JobContext(location="Zug", text="We use BigQuery, dbt and Looker on GCP.")
    rules = [b["rule"] for b in score(f, ctx, cfg.profile, cfg.scoring).breakdown]
    assert "stack: BigQuery, dbt, GCP, Looker" in rules


def test_role_points_for_data_roles(cfg):
    roles = cfg.scoring.bonus.role_family
    assert roles["DE"] == roles["AE"] == roles["BI"] == 8 and roles["DA"] == roles["DS"] == 6


@pytest.mark.parametrize(
    "title, text, rule",
    [
        ("AI Engineer", "Python and AWS", "role adjacent"),
        ("AI Engineer", "Python only", "role adjacent, stack < 2"),
        ("Data Scientist / ML Engineer", "Python only", "role DS"),  # nombra datos: rol normal
        ("Senior SQL/DWH Engineer", "Python only", "role DS"),
    ],
)
def test_adjacent_role_needs_stack(cfg, title, text, rule):
    case = json.loads(json.dumps(_case("dutch_is_a_plus_onsite")))
    case["features"].update(role_family="DS", stack_match=[])
    f = EvaluationFeatures(**case["features"])
    ctx = JobContext(location="Zug", country="CH", text=f"{title} {text}", title=title)
    rules = [b["rule"] for b in score(f, ctx, cfg.profile, cfg.scoring).breakdown]
    assert rule in rules


def test_country_bonus_for_switzerland(cfg):
    case = json.loads(json.dumps(_case("dutch_is_a_plus_onsite")))  # NL
    nl = _run(case, cfg)
    case["job"]["country"] = "CH"
    ch = _run(case, cfg)
    assert ch.score == nl.score + cfg.scoring.bonus.country["CH"]
    assert {"rule": "country CH", "points": 5} in ch.breakdown


@pytest.mark.parametrize(
    "level, cefr, rule, points",
    [
        ("required", "C1", "lang required: es", 8),  # FIA: "Fluent English and Spanish"
        ("required", None, "lang required: es", 8),
        ("nice", None, "lang nice: es", 4),
    ],
)
def test_spanish_adds_points(cfg, level, cefr, rule, points):
    case = json.loads(json.dumps(_case("work_permit_is_not_residency")))
    base = _run(case, cfg).score
    case["features"]["lang_required"] = [{"lang": "es", "level": level, "cefr": cefr}]
    res = _run(case, cfg)
    assert not res.excluded
    assert {"rule": rule, "points": points} in res.breakdown
    # sin penalización de "within level" ni de "nice"
    assert not any("es" in b.get("rule", "") and b["points"] < 0 for b in res.breakdown)
    assert res.score == min(100, base + points)
