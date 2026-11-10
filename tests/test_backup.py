import gzip
import sqlite3
from datetime import date

import pytest

from job_radar import daily, db
from job_radar.backup import backup_db


def _cfg(cfg, tmp_path, mirrors):
    bk = cfg.app.backup.model_copy(update={"enabled": True, "keep": 2})
    app = cfg.app.model_copy(
        update={
            "db_path": str(tmp_path / "t.db"),
            "logs_dir": str(tmp_path / "logs"),
            "exports_dir": str(tmp_path / "exports"),
            "exports_mirror_dirs": mirrors,
            "backup": bk,
        }
    )
    return cfg.model_copy(update={"app": app})


def test_backup_restorable_and_rotates(cfg, tmp_path):
    mirror = tmp_path / "drive"
    mirror.mkdir()
    cfg = _cfg(cfg, tmp_path, [str(mirror)])
    conn = db.connect(cfg.db_path)
    conn.execute("CREATE TABLE marca (x)")
    conn.execute("INSERT INTO marca VALUES (7)")
    conn.commit()
    for d in (1, 2, 3):
        out = backup_db(cfg, conn, date(2026, 11, d))
    folder = mirror / "backups"
    assert sorted(p.name for p in folder.iterdir()) == [
        "jobs_2026-11-02.db.gz",
        "jobs_2026-11-03.db.gz",
    ]
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress((folder / "jobs_2026-11-03.db.gz").read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT x FROM marca").fetchone() == (7,)
    assert out["paths"] == [str(folder / "jobs_2026-11-03.db.gz")]


def test_backup_without_folder_fails(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path, [])
    with pytest.raises(RuntimeError):
        backup_db(cfg, db.connect(cfg.db_path))


def test_failed_backup_is_a_failed_step(cfg, tmp_path):
    cfg = _cfg(cfg, tmp_path, [str(tmp_path / "no-existe")])
    s = daily.run_daily(cfg, db.connect(cfg.db_path), skip_ingest=True, skip_evaluate=True)
    assert s["totals"]["failed_steps"] == ["backup"]
    assert daily.error_alert(s) == "Falló el paso: backup"
