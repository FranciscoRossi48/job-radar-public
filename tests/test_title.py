import pytest

from job_radar.prefilter.title import TitleFilter


@pytest.fixture(scope="module")
def tf(cfg):
    return TitleFilter(cfg.prefilter.title)


@pytest.mark.parametrize(
    "title",
    [
        "Data Engineering Intern",
        "Internship - Business Intelligence",
        "Praktikum Data Analytics (80-100%)",
        "Praktikantin BI Reporting",
        "Werkstudent Data Engineering (m/w/d)",
        "Werkstudent*in Data Analytics",
        "Data Analytics Trainee Program",
        "Stagiair Data Engineering",
        "Junior Data Engineer",
        "Data Engineer (Junior)",
        "Stage Data Analyst",
        "Portfolio Strategy and Insights Interns (Basel)",
        "Summer Internships 2027 - Data",
        "Data Juniors Program",
        "Werkstudenten Data Analytics",
        "Praktikanten BI",
        "Stagiaires Data",
        "Data Apprentice",
        "Lernende/r Informatik Data",
    ],
)
def test_excluded(tf, title):
    assert tf.check(title) is not None


@pytest.mark.parametrize(
    "title",
    [
        "Senior Data Engineer",
        "Junior/Senior Data Engineer",
        "Junior / Senior BI Developer",
        "Medior/Senior Data Engineer",
        "Senior or Junior Analytics Engineer",
        "Data Engineer - Staging & Platform",
        "Backstage Data Platform Engineer",
        "Data Engineer, early-stage startup",
        "Internal Data Analyst",
        "International Business Intelligence Lead",
        "Lead Analytics Engineer (dbt, Snowflake)",
        "Dateningenieur / Data Engineer (80-100%)",
        "Palantir Foundry Engineer (Zurich or Lausanne)",
    ],
)
def test_passes(tf, title):
    assert tf.check(title) is None


def test_reason_names_the_word(tf):
    assert tf.check("Junior Data Engineer") == "title_excluded:junior"


@pytest.mark.parametrize("title", ["Software Engineer Backend", "Sales Manager DACH"])
def test_include_list_filters_irrelevant(tf, title):
    assert tf.check(title) == "title_not_relevant"
