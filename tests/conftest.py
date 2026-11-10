from pathlib import Path

import pytest

from job_radar.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg():
    """Config real de config/*.yaml, con dos excepciones para que los tests sean estables:
    sin carpetas espejo (nunca escriben en Drive), sin push de ntfy, sin respaldo, sin países de residencia (los casos de
    "must reside" se prueban explícitamente en test_scoring.py) y con todos los países activos
    (los casos de NL/LU siguen cubiertos aunque estén en pausa)."""
    c = load_config(ROOT)
    return c.model_copy(
        update={
            "app": c.app.model_copy(
                update={
                    "exports_mirror_dirs": [],
                    "notify_on_error": False,
                    "ntfy": c.app.ntfy.model_copy(update={"enabled": False}),
                    "backup": c.app.backup.model_copy(update={"enabled": False}),
                }
            ),
            "profile": c.profile.model_copy(update={"residency_countries": []}),
            "searches": c.searches.model_copy(update={"active_countries": []}),
        }
    )
