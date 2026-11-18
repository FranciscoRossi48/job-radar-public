"""Evaluación desatendida a través de Codex CLI (integración oficial de AgentRouter).

AgentRouter rechaza clientes HTTP no aprobados ("unauthorized client detected"). Codex CLI sí
está aprobado, así que este backend usa su salida estructurada (`--output-schema`) con un
`CODEX_HOME` temporal. No toca la configuración real de Codex del usuario.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from job_radar import db
from job_radar.config import AgentRouterSettings, Config
from job_radar.evaluate_common import (
    AuthenticationFailed,
    ConfigurationError,
    ModelsUnavailable,
    RetryableError,
    build_prompt,
    sanitize,
)
from job_radar.ingest.base import backoff_delay
from job_radar.models import EvaluationFeatures
from job_radar.scoring import JobContext, score

log = logging.getLogger(__name__)
PROVIDER = "agentrouter"
StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def strict_schema(node):
    """OpenAI structured output exige additionalProperties=false y todas las claves required."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            if node.get("properties"):
                node["required"] = list(node["properties"])
        for value in node.values():
            strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            strict_schema(value)
    return node


def _parse_events(stdout: str) -> tuple[dict | None, list[str]]:
    usage = None
    errors: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
        elif event.get("type") == "error":
            errors.append(str(event.get("message", "")))
    return usage, errors


def _classify(message: str) -> Exception:
    lower = message.lower()
    if "unauthorized client" in lower or "401" in lower or "invalid api key" in lower:
        return AuthenticationFailed(message[:300])
    if (
        "chatgpt account" in lower
        or "missing environment variable" in lower
        or "invalid schema" in lower
        or ("not supported" in lower and "model" in lower)
    ):
        return ConfigurationError(message[:300])
    if (
        "rate limit" in lower
        or "429" in lower
        or "timeout" in lower
        or "timed out" in lower
        or "reconnecting" in lower
        or "no available channel" in lower
        or any(code in lower for code in ("500", "502", "503", "504"))
    ):
        return RetryableError(message[:300])
    return ConfigurationError(message[:300])


class CodexEvaluator:
    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        runner: Callable = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg, self.conn = cfg, conn
        self.a: AgentRouterSettings = cfg.llm.agentrouter
        self.model = self.a.models[0].name
        self.bin = shutil.which(self.a.codex_bin) or self.a.codex_bin
        self.runner = runner
        self.sleep = sleep
        self.rubric = cfg.rubric()
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._env: dict[str, str] | None = None
        self._workdir: Path | None = None
        self._schema: Path | None = None
        self._out: Path | None = None

    def _prepare(self) -> None:
        if self._tmp is not None:
            return
        key = os.environ.get("AGENTROUTER_API_KEY")
        if not key:
            raise AuthenticationFailed("falta AGENTROUTER_API_KEY en .env")

        self._tmp = tempfile.TemporaryDirectory(prefix="job-radar-codex-")
        root = Path(self._tmp.name)
        home = root / "home"
        home.mkdir()
        self._workdir = root / "work"
        self._workdir.mkdir()
        (home / "config.toml").write_text(
            f'model = "{self.model}"\n'
            'model_provider = "AgentRouter"\n\n'
            "[model_providers.AgentRouter]\n"
            f'base_url = "{self.a.base_url}"\n'
            'env_key = "OPENAI_API_KEY"\n'
            'name = "AgentRouter"\n'
            'wire_api = "responses"\n',
            encoding="utf-8",
        )
        auth = home / "auth.json"
        auth.write_text(json.dumps({"OPENAI_API_KEY": key}), encoding="utf-8")
        auth.chmod(0o600)

        self._schema = root / "schema.json"
        self._out = root / "out.json"
        self._env = os.environ.copy()
        self._env["CODEX_HOME"] = str(home)
        self._env["OPENAI_API_KEY"] = key

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self):
        self._prepare()
        return self

    def __exit__(self, *exc):
        self.close()

    def _command(self, prompt: str) -> list[str]:
        assert self._workdir and self._schema and self._out
        return [
            self.bin,
            "exec",
            "--model",
            self.model,
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--cd",
            str(self._workdir),
            "--output-schema",
            str(self._schema),
            "--output-last-message",
            str(self._out),
            "--json",
            prompt,
        ]

    def _execute(
        self, prompt: str, schema_model: type[BaseModel] = EvaluationFeatures
    ) -> tuple[dict, dict | None]:
        self._prepare()
        assert self._env and self._workdir and self._out and self._schema
        self._schema.write_text(
            json.dumps(strict_schema(schema_model.model_json_schema()), ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            proc = self.runner(
                self._command(prompt),
                cwd=str(self._workdir),
                env=self._env,
                capture_output=True,
                text=True,
                timeout=self.a.codex_timeout_s,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            raise RetryableError(f"codex excedió {self.a.codex_timeout_s:.0f}s") from error
        except OSError as error:
            raise ConfigurationError(f"no pude ejecutar {self.bin}: {error}") from error

        usage, errors = _parse_events(proc.stdout or "")
        if self._out.exists():
            text = self._out.read_text(encoding="utf-8")
            self._out.unlink(missing_ok=True)
            try:
                return json.loads(text), usage
            except json.JSONDecodeError:
                pass
        message = "; ".join(errors) or (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise _classify(message)

    def _prompt(self, job: sqlite3.Row | dict) -> str:
        return (
            f"{self.rubric}\n\n"
            f"{build_prompt(job, self.cfg.profile)}\n\n"
            "Return only one JSON object matching the provided JSON Schema. "
            "Do not use tools, do not read files and do not add markdown fences."
        )

    def _call(
        self, prompt: str, schema_model: type[BaseModel] = EvaluationFeatures
    ) -> tuple[dict, str]:
        attempt = 0
        while True:
            try:
                data, usage = self._execute(prompt, schema_model)
            except RetryableError as error:
                db.log_llm_call(self.conn, PROVIDER, self.model, False)
                if attempt >= self.a.codex_max_retries:
                    raise ModelsUnavailable(str(error)) from error
                delay = min(
                    backoff_delay(attempt, self.a.backoff_base_s, self.a.backoff_max_s) + 1,
                    self.a.backoff_max_s,
                )
                attempt += 1
                log.warning(
                    "codex %s; reintento %d/%d en %.0fs",
                    error,
                    attempt,
                    self.a.codex_max_retries,
                    delay,
                )
                self.sleep(delay)
                continue
            except Exception:
                db.log_llm_call(self.conn, PROVIDER, self.model, False)
                raise
            usage = usage or {}
            db.log_llm_call(
                self.conn,
                PROVIDER,
                self.model,
                True,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
            return data, self.model

    def structured(
        self,
        prompt: str,
        schema_model: type[StructuredModel],
        instruction: str | None = None,
    ) -> tuple[StructuredModel, str]:
        full_prompt = f"{instruction}\n\n{prompt}" if instruction else prompt
        data, model = self._call(full_prompt, schema_model)
        return schema_model.model_validate(data), model

    def evaluate_job(self, job: sqlite3.Row | dict) -> tuple[EvaluationFeatures, str]:
        data, model = self._call(self._prompt(job), EvaluationFeatures)
        return EvaluationFeatures.model_validate(sanitize(data)), model

    def run(self, limit: int | None = None) -> dict:
        limit = min(limit or self.a.codex_max_jobs_per_run, self.a.codex_max_jobs_per_run)
        jobs = db.pending_for_evaluation(self.conn, limit, self.cfg.searches.active_codes)
        summary = {"candidates": len(jobs), "evaluated": 0, "failed": 0, "stopped": None}
        try:
            for job in jobs:
                try:
                    features, model = self.evaluate_job(job)
                except AuthenticationFailed as error:
                    summary["stopped"] = f"authentication: {error}"
                    log.error("codex/agentrouter: %s", error)
                    break
                except ConfigurationError as error:
                    summary["stopped"] = f"configuration: {error}"
                    log.error("codex/agentrouter: %s", error)
                    break
                except ModelsUnavailable as error:
                    summary["stopped"] = f"models unavailable: {error}"
                    log.warning("codex/agentrouter: %s; corto la corrida", error)
                    break
                except (ValidationError, ValueError, json.JSONDecodeError) as error:
                    summary["failed"] += 1
                    log.warning(
                        "codex/agentrouter: respuesta inválida para %s: %s",
                        job["id"],
                        str(error)[:300],
                    )
                    continue
                except Exception as error:  # noqa: BLE001 — un job roto no corta la corrida
                    summary["failed"] += 1
                    log.exception("codex/agentrouter: error evaluando %s: %s", job["id"], error)
                    continue

                context = JobContext.from_row(job)
                result = score(features, context, self.cfg.profile, self.cfg.scoring)
                db.insert_evaluation(
                    self.conn,
                    job_id=job["id"],
                    evaluator=PROVIDER,
                    model=model,
                    prompt_version=self.cfg.llm.prompt_version,
                    features=features,
                    result=result,
                )
                self.conn.commit()
                summary["evaluated"] += 1
                log.info(
                    "codex/agentrouter: %s -> %.0f %s",
                    job["id"],
                    result.score,
                    "(excluido: " + "; ".join(result.exclusion_reasons) + ")"
                    if result.excluded
                    else "",
                )
        finally:
            self.close()
        return summary
