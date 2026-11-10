"""Respaldo de jobs.db en las carpetas espejo (Drive): la base no está en Git y sin esto solo
existe en esta Mac."""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from job_radar.config import Config
from job_radar.export import mirror_dirs

log = logging.getLogger(__name__)


def backup_db(cfg: Config, conn: sqlite3.Connection, day: date | None = None) -> dict:
    """Copia consistente de la base, comprimida, una por día. Sin carpeta destino lanza error:
    un respaldo que no se hizo tiene que verse como paso fallido, no pasar de largo."""
    dirs = mirror_dirs(cfg)
    if not dirs:
        raise RuntimeError("no hay carpeta espejo donde guardar el respaldo")
    bk = cfg.app.backup
    name = f"jobs_{(day or date.today()).isoformat()}.db.gz"
    paths = []
    with TemporaryDirectory() as tmp:
        raw, packed = Path(tmp) / "jobs.db", Path(tmp) / name
        dest = sqlite3.connect(raw)
        try:
            conn.backup(dest)  # a diferencia de copiar el archivo, respeta el WAL
        finally:
            dest.close()
        with raw.open("rb") as src, gzip.open(packed, "wb") as out:
            shutil.copyfileobj(src, out)
        for d in dirs:
            folder = d / bk.subdir
            folder.mkdir(exist_ok=True)
            # Nombre temporal y rename: Drive nunca sube un archivo a medio copiar.
            partial = folder / f".{name}.tmp"
            shutil.copy2(packed, partial)
            os.replace(partial, folder / name)
            for old in sorted(folder.glob("jobs_*.db.gz"))[: -bk.keep]:
                old.unlink()
            paths.append(str(folder / name))
    log.info("respaldo de la base: %s", ", ".join(paths))
    return {"paths": paths}
