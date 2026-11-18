"""Safe, validated access to the YAML configuration used by the dashboard."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from job_radar.config import (
    AppSettings,
    Company,
    LlmConfig,
    Prefilter,
    Profile,
    ScoringConfig,
    Searches,
    load_config,
)

CONFIG_MODELS = {
    "app.yaml": AppSettings,
    "profile.yaml": Profile,
    "searches.yaml": Searches,
    "prefilter.yaml": Prefilter,
    "scoring.yaml": ScoringConfig,
    "llm.yaml": LlmConfig,
}
CONFIG_FILES = [
    "profile.yaml",
    "searches.yaml",
    "scoring.yaml",
    "prefilter.yaml",
    "llm.yaml",
    "app.yaml",
    "companies.yaml",
]


class ConfigSaveError(ValueError):
    pass


def read_config_file(root: Path, name: str) -> dict[str, Any]:
    if name not in CONFIG_FILES:
        raise FileNotFoundError(name)
    path = root / "config" / name
    stat = path.stat()
    return {
        "name": name,
        "content": path.read_text(encoding="utf-8"),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
    }


def _validate(name: str, content: str) -> None:
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        raise ConfigSaveError(f"Invalid YAML: {exc}") from exc
    try:
        if name == "companies.yaml":
            if not isinstance(data, dict):
                raise ConfigSaveError("companies.yaml must contain a mapping")
            for company in data.get("companies") or []:
                Company.model_validate(company)
        else:
            CONFIG_MODELS[name].model_validate(data)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:8]
        )
        raise ConfigSaveError(errors) from exc


def validate_config_file(name: str, content: str) -> dict[str, bool | str]:
    if name not in CONFIG_FILES:
        raise FileNotFoundError(name)
    _validate(name, content)
    return {"valid": True, "message": f"{name} is valid"}


def save_config_file(root: Path, name: str, content: str) -> dict[str, Any]:
    if name not in CONFIG_FILES:
        raise FileNotFoundError(name)
    _validate(name, content)
    config_dir = root / "config"
    path = config_dir / name
    original = path.read_text(encoding="utf-8")
    normalized = content.rstrip() + "\n"
    if normalized == original:
        return {"saved": False, "backup": None, **read_config_file(root, name)}

    backup_dir = config_dir / ".backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"{stamp}-{name}"
    shutil.copy2(path, backup)
    temporary = config_dir / f".{name}.tmp"
    temporary.write_text(normalized, encoding="utf-8")
    temporary.replace(path)
    try:
        load_config(root)
    except Exception as exc:
        path.write_text(original, encoding="utf-8")
        raise ConfigSaveError(f"Configuration would not load: {exc}") from exc
    return {"saved": True, "backup": str(backup), **read_config_file(root, name)}


def settings_summary(cfg) -> dict[str, Any]:
    provider = cfg.llm.provider
    if provider.startswith("agentrouter"):
        models = [model.name for model in cfg.llm.agentrouter.models]
        key_configured = bool(os.environ.get("AGENTROUTER_API_KEY"))
        key_name = "AGENTROUTER_API_KEY"
    else:
        models = [model.name for model in cfg.llm.gemini.models]
        key_configured = bool(os.environ.get("GEMINI_API_KEY"))
        key_name = "GEMINI_API_KEY"
    return {
        "profile": {
            "summary": cfg.profile.summary,
            "current_title": cfg.profile.current_title,
            "years_experience": cfg.profile.years_experience,
            "domains": cfg.profile.domains,
            "languages": cfg.profile.languages,
            "residency_countries": cfg.profile.residency_countries,
            "stack": cfg.profile.stack,
        },
        "search": {
            "active_countries": cfg.searches.active_codes,
            "available_countries": [country.code for country in cfg.searches.countries],
            "queries": cfg.searches.queries,
            "rotating_queries": cfg.searches.rotating_queries,
            "sources": cfg.searches.jobspy.sites,
            "hours_old": cfg.searches.jobspy.hours_old,
        },
        "scoring": {
            "threshold": cfg.scoring.threshold,
            "base": cfg.scoring.base,
            "evaluator_priority": cfg.scoring.evaluator_priority,
            "no_cv_stack_match": cfg.scoring.hard_exclusions.no_cv_stack_match,
        },
        "llm": {
            "provider": provider,
            "models": models,
            "key_name": key_name,
            "key_configured": key_configured,
        },
        "paths": {
            "root": str(cfg.root),
            "database": str(cfg.db_path),
            "exports": str(cfg.path(cfg.app.exports_dir)),
            "logs": str(cfg.path(cfg.app.logs_dir)),
        },
        "config_files": CONFIG_FILES,
    }
