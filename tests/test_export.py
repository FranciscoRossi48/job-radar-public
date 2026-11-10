import csv
import json
from pathlib import Path

from job_radar import db
from job_radar.export import export_csv
from job_radar.models import EvaluationFeatures, PrefilterResult, RawJob
from job_radar.scoring import ScoreResult

CASES = json.loads((Path(__file__).parent / "fixtures" / "jobs" / "cases.json").read_text())
F = EvaluationFeatures(**CASES[0]["features"])


def _setup(cfg, tmp_path):
    app = cfg.app.model_copy(
        update={"db_path": str(tmp_path / "t.db"), "exports_dir": str(tmp_path / "exports")}
    )
    cfg = cfg.model_copy(update={"app": app})
    conn = db.connect(cfg.db_path)

    def add(jid, score):
        job = RawJob(
            source="t", job_id=jid, url="u", title=f"DE {jid}", company="Acme", country="CH"
        )
        db.upsert_job(conn, job, PrefilterResult(excluded=False))
        bd = [{"rule": "stack: BigQuery, dbt", "points": 8}]
        db.insert_evaluation(
            conn,
            job_id=f"t:{jid}",
            evaluator="gemini",
            model="m",
            prompt_version="v1",
            features=F,
            result=ScoreResult(score, False, bd),
        )
        conn.commit()

    return cfg, conn, add


def _ids(path):
    return [r["id"] for r in csv.DictReader(path.open())]


def test_new_mode_never_repeats_and_appends_same_day(cfg, tmp_path):
    cfg, conn, add = _setup(cfg, tmp_path)
    add("a", 90)
    add("b", 80)
    add("low", 40)
    path, n = export_csv(conn, cfg)
    assert n == 2 and _ids(path) == ["t:a", "t:b"]
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["stack_match"] == "BigQuery, dbt"  # stack que sumó (LLM + texto)

    add("c", 85)
    path2, n2 = export_csv(conn, cfg)
    assert n2 == 1 and path2 == path
    assert _ids(path) == ["t:a", "t:b", "t:c"]  # se agregó al archivo del día, sin repetir

    _, n3 = export_csv(conn, cfg)
    assert n3 == 0


def test_all_mode_is_full_ranking_and_marks_nothing(cfg, tmp_path):
    cfg, conn, add = _setup(cfg, tmp_path)
    add("a", 90)
    add("b", 80)
    export_csv(conn, cfg)  # marca a y b
    add("c", 99)
    path, n = export_csv(conn, cfg, mode="all")
    assert path.name == "ranking_all.csv" and _ids(path) == ["t:c", "t:a", "t:b"]
    _, n_new = export_csv(conn, cfg)
    assert n_new == 1  # c sigue siendo novedad: --all no marca


def test_csv_is_copied_to_mirror_dirs(cfg, tmp_path):
    cfg, conn, add = _setup(cfg, tmp_path)
    drive = tmp_path / "drive" / "Job Radar"
    drive.mkdir(parents=True)
    pattern = str(tmp_path / "dr*" / "Job Radar")  # comodín, como GoogleDrive-<mail>
    missing = str(tmp_path / "no-existe")
    app = cfg.app.model_copy(update={"exports_mirror_dirs": [pattern, missing]})
    cfg = cfg.model_copy(update={"app": app})
    add("a", 90)
    path, _ = export_csv(conn, cfg)
    assert (drive / path.name).read_text() == path.read_text()
    add("b", 85)
    export_csv(conn, cfg)  # segunda corrida del día: la copia se actualiza con el archivo completo
    assert _ids(drive / path.name) == ["t:a", "t:b"]


def test_tests_never_touch_real_drive(cfg):
    assert cfg.app.exports_mirror_dirs == []


def test_decided_jobs_skip_daily_csv_but_show_in_history(cfg, tmp_path):
    cfg, conn, add = _setup(cfg, tmp_path)
    add("a", 90)
    add("b", 85)
    db.set_decision(conn, "t:a", "discarded")
    db.set_decision(conn, "t:b", "applied")
    conn.commit()
    _, n = export_csv(conn, cfg)
    assert n == 0
    path, _ = export_csv(conn, cfg, mode="all")
    rows = {r["id"]: r["decision"] for r in csv.DictReader(path.open())}
    assert rows == {"t:a": "discarded", "t:b": "applied"}


def test_paused_country_skips_daily_csv_but_shows_in_history(cfg, tmp_path):
    cfg, conn, add = _setup(cfg, tmp_path)
    add("nl", 90)
    add("ch", 90)
    conn.execute("UPDATE jobs SET country = 'NL' WHERE id = 't:nl'")
    cfg = cfg.model_copy(
        update={"searches": cfg.searches.model_copy(update={"active_countries": ["CH"]})}
    )
    assert _ids(export_csv(conn, cfg, mode="new")[0]) == ["t:ch"]
    assert "t:nl" in _ids(export_csv(conn, cfg, mode="all")[0])
