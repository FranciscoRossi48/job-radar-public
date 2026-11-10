import pytest

from job_radar.models import RawJob
from job_radar.prefilter import Prefilterer
from job_radar.prefilter.condense import Condenser
from job_radar.prefilter.language import EvidenceExtractor, detect_lang
from job_radar.prefilter.title import TitleFilter

EN = (
    "We are looking for a Senior Data Engineer to build our lakehouse on Databricks. "
    "You will design pipelines in Python and SQL and work with analysts across the bank."
)
DE = (
    "Wir suchen eine erfahrene Data Engineer Persönlichkeit für unser Team in Zürich. "
    "Sie entwickeln Datenpipelines und arbeiten eng mit den Fachbereichen zusammen."
)
FR = (
    "Nous recherchons un ingénieur de données expérimenté pour notre équipe à Genève. "
    "Vous concevrez des pipelines de données et travaillerez avec les équipes métier."
)


def test_detect_lang(cfg):
    langs = cfg.prefilter.description.detect_langs
    assert detect_lang(EN, langs) == "en"
    assert detect_lang(DE, langs) == "de"
    assert detect_lang(FR, langs) == "fr"
    assert detect_lang("too short", langs) is None


def test_snippets_capture_context(cfg):
    ex = EvidenceExtractor(cfg.prefilter.evidence, 150)
    text = (
        EN
        + " Fluent German (C1) is required. "
        + "x " * 200
        + "You must currently reside in Switzerland."
    )
    snips = ex.extract(text)
    lang = [s for s in snips if s.kind == "lang"]
    res = [s for s in snips if s.kind == "residency"]
    assert len(lang) == 1  # fluent, German y C1 se fusionan en una ventana
    assert "Fluent" in lang[0].match and "German" in lang[0].match and "C1" in lang[0].match
    assert "Databricks" in lang[0].text  # contexto previo
    assert res and "reside" in res[0].match


def test_condense_drops_boilerplate_and_prioritizes_requirements(cfg):
    desc = """## About us
We are a leading bank with 10,000 employees worldwide.

## Your tasks
- Build pipelines

## Your profile
- 5+ years of SQL and Python
- Fluent English, German is a plus

## What we offer
- 30 days of vacation

We are an equal opportunity employer and value diversity."""
    out = Condenser(cfg.prefilter.condense, 2000).condense(desc)
    assert "leading bank" not in out
    assert "vacation" not in out
    assert "equal opportunity" not in out
    assert out.index("5+ years") < out.index("Build pipelines")


def test_condense_truncates(cfg):
    out = Condenser(cfg.prefilter.condense, 100).condense("word " * 500)
    assert len(out) <= 102


def test_prefilterer_excludes_french_description(cfg):
    job = RawJob(source="t", job_id="1", url="u", title="Data Engineer", description=FR)
    res = Prefilterer(cfg.prefilter).run(job)
    assert res.excluded and res.reason == "desc_lang:fr"


def test_prefilterer_keeps_german_description(cfg):
    job = RawJob(source="t", job_id="1", url="u", title="Data Engineer", description=DE)
    res = Prefilterer(cfg.prefilter).run(job)
    assert not res.excluded and res.desc_lang == "de"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Experience with Cloud Native architectures on AWS.", False),
        ("Architect for an AI-native future.", False),
        ("Leverage data-native services and Kubernetes-native pipelines.", False),
        ("Built with React Native and Expo.", False),
        ("Tech native for almost 30 years.", False),
        ("For this position we only consider Dutch native speakers.", True),
        ("Native German speaker or C1 level.", True),
        ("Languages: German (native), English (fluent).", True),
        ("Near-native English is required.", True),
    ],
)
def test_native_only_in_language_context(cfg, text, expected):
    ex = EvidenceExtractor(cfg.prefilter.evidence, 150)
    hits = [s for s in ex.extract(text) if s.kind == "lang" and "native" in s.match.lower()]
    assert bool(hits) is expected, [s.match for s in ex.extract(text)]


DE_BASE = (
    "Wir suchen eine erfahrene Persönlichkeit für den Aufbau unserer Datenplattform in Zürich. "
    "Sie entwickeln Datenpipelines mit Python und SQL und arbeiten eng mit den Fachbereichen. "
)


@pytest.mark.parametrize(
    "extra,excluded",
    [
        ("Sehr gute Deutschkenntnisse in Wort und Schrift.", True),
        ("Fliessende Deutsch- und Englischkenntnisse.", True),
        ("Verhandlungssicheres Deutsch ist Voraussetzung.", True),
        ("Deutschkenntnisse mindestens C1.", True),
        ("Deutsch auf C1-Niveau.", True),
        ("Schweizerdeutsch ist ein Muss.", True),
        ("Fliessend Englisch, Deutsch von Vorteil.", False),
        ("Schweizerdeutsch wünschenswert.", False),
        ("Du verstehst Schweizerdeutsch (sprechen musst du es nicht).", False),
        ("Schweizerdeutsch ist nicht zwingend.", False),
        ("Gute Englischkenntnisse; Deutschkenntnisse (B1) sind ein Plus.", False),
        ("Arbeitsort in der deutschsprachigen Schweiz.", False),
        ("Gute Deutschkenntnisse.", False),
    ],
)
def test_german_ad_high_german_requirement(cfg, extra, excluded):
    job = RawJob(
        source="t", job_id="1", url="u", title="Data Engineer", description=DE_BASE + extra
    )
    res = Prefilterer(cfg.prefilter).run(job)
    assert res.desc_lang == "de"
    assert (res.reason or "").startswith("desc_lang:de_high") is excluded, res.reason


def test_high_german_rule_only_for_german_ads(cfg):
    desc = EN + " Fluent German (C1) is required."
    job = RawJob(source="t", job_id="1", url="u", title="Data Engineer", description=desc)
    assert not Prefilterer(cfg.prefilter).run(job).excluded  # aviso en inglés: decide el LLM


@pytest.mark.parametrize(
    "title",
    [
        "AI Engineer",
        "Senior Machine Learning Engineer",
        "ML Ops Engineer",
        "Senior Business Analyst",
        "Solutions Architect",
        "Senior Cloud Engineer",
        "Senior ICT Database Engineer:in (80-100%)",
        "PL/SQL Developer",
        "Senior Python Data Engineer",  # entra por "data"
    ],
)
def test_adjacent_titles_pass(cfg, title):
    assert TitleFilter(cfg.prefilter.title).check(title) is None


@pytest.mark.parametrize(
    "title",
    [
        "FP&A Analyst (100%)",
        "Logistics Analyst",
        "AI Adoption Specialist",
        "Senior Backend Developer",
        "Senior Python Engineer",  # backend/Python puro: se descartaban todos
        "Senior Backend Engineer (Python)",
        "Network Engineer",
        "Principal Customer Marketing Manager",
    ],
)
def test_unrelated_titles_stay_out(cfg, title):
    assert TitleFilter(cfg.prefilter.title).check(title) == "title_not_relevant"
