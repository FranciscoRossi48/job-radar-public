import pytest

from job_radar import db
from job_radar.config import DedupSettings
from job_radar.dedup import dedup_key, hamming, normalize_company, normalize_title, simhash
from job_radar.models import PrefilterResult, RawJob

DESC = (
    "We are looking for a Senior Data Engineer to join our Zurich team. You will design and "
    "operate batch and streaming pipelines on Databricks and Snowflake, model data with dbt, "
    "and work closely with risk and finance stakeholders in a regulated banking environment. "
    "Requirements: 5+ years of experience with Python and SQL, strong data modeling skills, "
    "experience with Airflow orchestration, CI/CD and cloud infrastructure on Azure. Fluent "
    "English is required; German is a plus. You bring ownership, curiosity and the ability to "
    "explain complex topics to non-technical audiences. Hybrid work, two days in the office."
)


@pytest.mark.parametrize(
    "a,b",
    [
        ("Senior Data Engineer (m/w/d)", "Senior Data Engineer"),
        ("Senior Data Engineer 80-100%", "Senior Data Engineer"),
        ("Senior Data Engineer (f/m/d)", "senior data engineer"),
        ("Data Engineer:in", "Data Engineer"),
        ("Dateningenieur*in", "Dateningenieur"),
        ("Data Engineer (H/F)", "Data Engineer"),
        (
            "Medical AI Data Engineer @ Tune Insight, Lausanne, Switzerland (On-site/Hybrid)",
            "Medical AI Data Engineer",
        ),
    ],
)
def test_normalize_title(a, b):
    assert normalize_title(a) == normalize_title(b)


def test_normalize_company():
    assert normalize_company("UBS Switzerland AG") == normalize_company("UBS")
    assert normalize_company("Adyen N.V.") == "adyen"
    assert normalize_company("Zürcher Kantonalbank") == "zurcher kantonalbank"


def test_dedup_key_requires_company():
    assert dedup_key(None, "Data Engineer", "CH") is None
    assert dedup_key("Acme AG", "Data Engineer (m/w/d)", "ch") == "acme|data engineer|CH"


def test_simhash_near_duplicates():
    a = simhash(DESC)
    b = simhash(DESC.replace("Hybrid work, two days in the office.", "Apply now!"))
    c = simhash("Wir suchen eine Pflegefachperson für unsere Station in Basel. " * 5)
    assert hamming(a, b) <= 8
    assert hamming(a, c) > 20


def _job(
    source, jid, company="Acme Bank AG", title="Senior Data Engineer", country="CH", desc=DESC
):
    return RawJob(
        source=source,
        job_id=jid,
        url="u",
        title=title,
        company=company,
        country=country,
        description=desc,
    )


DD = DedupSettings(simhash_max_distance=10, simhash_min_chars=400, window_days=45)


def _status(conn, jid):
    return tuple(
        conn.execute("SELECT status, duplicate_of FROM jobs WHERE id=?", (jid,)).fetchone()
    )


def test_level1_same_id_is_not_reinserted(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.upsert_job(conn, _job("linkedin", "1"), PrefilterResult(excluded=False), DD)
    assert not db.upsert_job(conn, _job("linkedin", "1"), PrefilterResult(excluded=False), DD)


def test_level2_company_title_country(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job("linkedin", "1"), PrefilterResult(excluded=False), DD)
    db.upsert_job(
        conn,
        _job("indeed", "x", title="Senior Data Engineer (m/w/d)", desc="short"),
        PrefilterResult(excluded=False),
        DD,
    )
    assert _status(conn, "indeed:x") == ("duplicate", "linkedin:1")
    # mismo título en otro país no es duplicado
    db.upsert_job(
        conn, _job("indeed", "y", country="NL", desc="short"), PrefilterResult(excluded=False), DD
    )
    assert _status(conn, "indeed:y") == ("pending", None)


def test_level3_simhash(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job("greenhouse", "1"), PrefilterResult(excluded=False), DD)
    # empresa con otro nombre y título distinto, misma descripción con un cambio menor
    other = _job(
        "linkedin",
        "9",
        company="ACME Private Banking",
        title="Data Engineer – Lakehouse",
        desc=DESC.replace("Apply", "Send your CV"),
    )
    db.upsert_job(conn, other, PrefilterResult(excluded=False), DD)
    assert _status(conn, "linkedin:9") == ("duplicate", "greenhouse:1")


def test_level3_aggregator_same_title_relaxed_simhash(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    strict_dd = DedupSettings(
        simhash_max_distance=3,
        simhash_same_title_max_distance=10,
        simhash_min_chars=400,
        window_days=45,
    )
    db.upsert_job(
        conn,
        _job(
            "indeed", "1", company="Amazon.com", title="Data Engineer I, EU AMXL BIT", country="LU"
        ),
        PrefilterResult(excluded=False),
        strict_dd,
    )
    # Agregador con otro nombre de empresa y encabezado extra (distancia simhash > 3 pero <= 10)
    repost = _job(
        "linkedin",
        "2",
        company="Silicon Luxembourg",
        title="Data Engineer I, EU AMXL BIT",
        country="LU",
        desc="Amazon is Earth's most customer-centric company, a global leader in e-commerce.\n\n"
        + DESC,
    )
    db.upsert_job(conn, repost, PrefilterResult(excluded=False), strict_dd)
    assert _status(conn, "linkedin:2") == ("duplicate", "indeed:1")


def test_excluded_original_does_not_capture_duplicates(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(
        conn,
        _job("linkedin", "1", desc=""),
        PrefilterResult(excluded=True, reason="no_description"),
        DD,
    )
    db.upsert_job(conn, _job("greenhouse", "2"), PrefilterResult(excluded=False), DD)
    assert _status(conn, "greenhouse:2") == ("pending", None)


def test_short_descriptions_skip_simhash(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(
        conn,
        _job("a", "1", company="X", desc="Data engineer wanted."),
        PrefilterResult(excluded=False),
        DD,
    )
    db.upsert_job(
        conn,
        _job("b", "2", company="Y", desc="Data engineer wanted."),
        PrefilterResult(excluded=False),
        DD,
    )
    assert _status(conn, "b:2") == ("pending", None)


def test_backfill_dedup_marks_later_copy(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    # Simula avisos de antes de la dedup: insertados sin dd (sin dedup_key ni simhash).
    db.upsert_job(
        conn,
        _job("indeed", "1", company="UNION BANCAIRE PRIVÉE, UBP SA"),
        PrefilterResult(excluded=False),
    )
    conn.execute(
        "UPDATE jobs SET dedup_key = NULL, simhash = NULL, first_seen = '2026-10-14T22:00:00+00:00'"
    )
    db.upsert_job(
        conn,
        _job("linkedin", "2", company="UBP - Union Bancaire Privée"),
        PrefilterResult(excluded=False),
    )
    conn.execute("UPDATE jobs SET first_seen = '2026-10-15T11:00:00+00:00' WHERE id = 'linkedin:2'")
    out = db.backfill_dedup(conn, DD)
    assert out["filled"] >= 1
    assert [d["id"] for d in out["duplicates"]] == ["linkedin:2"]
    assert _status(conn, "linkedin:2") == ("duplicate", "indeed:1")
    assert _status(conn, "indeed:1") == ("pending", None)
    assert db.backfill_dedup(conn, DD)["duplicates"] == []  # idempotente
