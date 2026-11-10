from datetime import UTC, datetime, timedelta

from job_radar import db
from job_radar.models import PrefilterResult, RawJob


def _job(i="1"):
    return RawJob(source="linkedin", job_id=i, url="u", title="Data Engineer", country="CH")


def test_upsert_new_then_seen(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    pre = PrefilterResult(excluded=False)
    assert db.upsert_job(conn, _job(), pre) is True
    assert db.upsert_job(conn, _job(), pre) is False
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["id"] == "linkedin:1" and row["status"] == "pending"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_excluded_status(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job(), PrefilterResult(excluded=True, reason="title_excluded:junior"))
    row = conn.execute("SELECT status, exclude_reason FROM jobs").fetchone()
    assert tuple(row) == ("excluded_prefilter", "title_excluded:junior")


def test_leases_and_stale(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job("1"), PrefilterResult(excluded=False))
    db.upsert_job(conn, _job("2"), PrefilterResult(excluded=False))
    old = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    conn.execute(
        "UPDATE jobs SET claimed_at = ?, claimed_by = 'claude' WHERE id = 'linkedin:1'", (old,)
    )
    conn.execute(
        "UPDATE jobs SET claimed_at = ?, claimed_by = 'claude' WHERE id = 'linkedin:2'",
        (db.now_iso(),),
    )
    assert db.release_expired_leases(conn, 30) == 1

    very_old = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    conn.execute("UPDATE jobs SET last_seen = ? WHERE id = 'linkedin:1'", (very_old,))
    assert db.mark_stale(conn, 14) == 1
    # si reaparece, vuelve a pending
    db.upsert_job(conn, _job("1"), PrefilterResult(excluded=False))
    assert conn.execute("SELECT status FROM jobs WHERE id='linkedin:1'").fetchone()[0] == "pending"


def test_job_without_description_is_reprocessed_when_it_arrives(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job(), PrefilterResult(excluded=True, reason="no_description"))
    full = _job().model_copy(update={"description": "We need a senior data engineer."})
    db.upsert_job(conn, full, PrefilterResult(excluded=False, desc_lang="en"))
    row = conn.execute("SELECT status, exclude_reason, desc_lang FROM jobs").fetchone()
    assert tuple(row) == ("pending", None, "en")


def test_known_ids_excludes_jobs_without_description(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job("1"), PrefilterResult(excluded=False))
    db.upsert_job(conn, _job("2"), PrefilterResult(excluded=True, reason="no_description"))
    assert db.known_ids(conn) == {"linkedin:1"}


def test_reprefilter_only_changes_status_of_pending(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.upsert_job(conn, _job("1"), PrefilterResult(excluded=False))
    db.upsert_job(conn, _job("2"), PrefilterResult(excluded=False))
    conn.execute("UPDATE jobs SET status = 'evaluated' WHERE id = 'linkedin:2'")
    out = db.reprefilter(conn, lambda job: PrefilterResult(excluded=True, reason="x"))
    assert out == {"refreshed": 2, "newly_excluded": 1, "reopened": 0}
    st = dict(conn.execute("SELECT id, status FROM jobs").fetchall())
    assert st == {"linkedin:1": "excluded_prefilter", "linkedin:2": "evaluated"}


def test_reprefilter_reopens_title_that_became_relevant(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    excluded = PrefilterResult(excluded=True, reason="title_not_relevant")
    for i in "123":
        db.upsert_job(conn, _job(i), excluded)
    conn.execute("UPDATE jobs SET last_seen = '2020-01-01' WHERE id = 'linkedin:3'")

    def pre_run(job):
        if job.job_id == "2":
            return PrefilterResult(excluded=True, reason="no_description")
        return PrefilterResult(excluded=False)

    assert db.reprefilter(conn, pre_run)["reopened"] == 1
    rows = conn.execute("SELECT id, status, exclude_reason FROM jobs ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("linkedin:1", "pending", None),
        ("linkedin:2", "excluded_prefilter", "no_description"),
        ("linkedin:3", "excluded_prefilter", "title_not_relevant"),  # demasiado viejo
    ]


def test_pending_for_evaluation_skips_paused_countries(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    for i in "123":
        db.upsert_job(conn, _job(i), PrefilterResult(excluded=False))
    conn.execute("UPDATE jobs SET country = 'NL' WHERE id = 'linkedin:1'")
    conn.execute("UPDATE jobs SET country = 'CH' WHERE id = 'linkedin:2'")
    conn.execute("UPDATE jobs SET country = NULL WHERE id = 'linkedin:3'")
    ids = {r["id"] for r in db.pending_for_evaluation(conn, 10, ["CH"])}
    assert ids == {"linkedin:2", "linkedin:3"}
    assert len(db.pending_for_evaluation(conn, 10)) == 3
