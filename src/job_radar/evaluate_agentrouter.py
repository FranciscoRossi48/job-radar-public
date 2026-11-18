"""Evaluación desatendida mediante la API OpenAI-compatible de AgentRouter."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from job_radar import db
from job_radar.config import AgentRouterModel, AgentRouterSettings, Config
from job_radar.evaluate_common import (
    AuthenticationFailed,
    BillingExhausted,
    DailyQuotaExhausted,
    ModelsUnavailable,
    RateLimiter,
    RequestRejected,
    build_prompt,
    sanitize,
)
from job_radar.ingest.base import backoff_delay
from job_radar.models import EvaluationFeatures
from job_radar.scoring import JobContext, score

log = logging.getLogger(__name__)
PROVIDER = "agentrouter"
StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return response.text.strip()[:300] or f"HTTP {response.status_code}"
    error = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(error, dict):
        error = error.get("message") or error.get("detail") or error
    return str(error)[:300]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _response_text(data: dict) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("AgentRouter respondió sin choices")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if text:
            return text
    raise ValueError("AgentRouter respondió sin contenido de texto")


def _parse_json(text: str) -> dict:
    """Tolera fences aunque JSON mode normalmente no los devuelve."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline >= 0 else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("la respuesta JSON no es un objeto")
    return data


class AgentRouterEvaluator:
    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        client=None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg, self.conn = cfg, conn
        self.a: AgentRouterSettings = cfg.llm.agentrouter
        self.sleep = sleep
        self.limiters = {
            m.name: RateLimiter(m.rpm, clock=clock, sleep=sleep) for m in self.a.models
        }
        self.exhausted: set[str] = set()
        self.unavailable: set[str] = set()
        self._errors_5xx: dict[str, int] = {}
        self.rubric = cfg.rubric()
        self._client = client

    @property
    def endpoint(self) -> str:
        base_url = os.environ.get("AGENTROUTER_BASE_URL") or self.a.base_url
        return f"{base_url.rstrip('/')}/chat/completions"

    @property
    def client(self):
        if self._client is None:
            key = os.environ.get("AGENTROUTER_API_KEY")
            if not key:
                raise AuthenticationFailed("falta AGENTROUTER_API_KEY en .env")
            self._client = httpx.Client(
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Originator": "job-radar",
                    "Version": "0.1.0",
                    "User-Agent": "job-radar/0.1.0",
                },
                timeout=self.a.request_timeout_s,
            )
        return self._client

    def _payload(
        self,
        model: str,
        prompt: str,
        schema_model: type[BaseModel] = EvaluationFeatures,
        instruction: str | None = None,
    ) -> dict:
        schema = json.dumps(
            schema_model.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        system_instruction = (
            f"{instruction or self.rubric}\n\n"
            "Return only one JSON object matching this JSON Schema. Do not use markdown fences.\n"
            f"{schema}"
        )
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

    def _calls_today(self, model: str) -> int:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return db.llm_calls_since(self.conn, PROVIDER, midnight, model)

    def _next_model(self) -> AgentRouterModel:
        for model in self.a.models:
            if model.name in self.exhausted or model.name in self.unavailable:
                continue
            if self._calls_today(model.name) >= model.rpd:
                log.info("agentrouter: %s alcanzó el RPD local (%d)", model.name, model.rpd)
                self.exhausted.add(model.name)
                continue
            return model
        if self.unavailable:
            raise ModelsUnavailable(
                f"sin modelos disponibles (caídos: {sorted(self.unavailable)}, "
                f"sin cuota local: {sorted(self.exhausted)})"
            )
        raise DailyQuotaExhausted("todos los modelos alcanzaron el RPD local")

    def _has_alternative(self, name: str) -> bool:
        return any(
            model.name != name
            and model.name not in self.exhausted
            and model.name not in self.unavailable
            and self._calls_today(model.name) < model.rpd
            for model in self.a.models
        )

    def _retry_unavailable(self, model: AgentRouterModel, reason: str) -> None:
        failures = self._errors_5xx[model.name] = self._errors_5xx.get(model.name, 0) + 1
        alternative = self._has_alternative(model.name)
        limit = self.a.unavailable_after if alternative else self.a.last_model_retries
        if failures >= limit:
            log.warning(
                "agentrouter: %s sigue caído (%s); lo salteo en esta corrida",
                model.name,
                reason,
            )
            self.unavailable.add(model.name)
            return
        delay = min(
            backoff_delay(failures - 1, self.a.backoff_base_s, self.a.backoff_max_s) + 1,
            self.a.backoff_max_s,
        )
        log.warning(
            "agentrouter %s no disponible (%d/%d%s); reintento en %.0fs: %s",
            model.name,
            failures,
            limit,
            "" if alternative else ", último modelo",
            delay,
            reason,
        )
        self.sleep(delay)

    def _call(
        self,
        prompt: str,
        schema_model: type[BaseModel] = EvaluationFeatures,
        instruction: str | None = None,
    ) -> tuple[str, str]:
        rate_attempt = 0
        while True:
            model = self._next_model()
            self.limiters[model.name].acquire()
            try:
                response = self.client.post(
                    self.endpoint,
                    json=self._payload(model.name, prompt, schema_model, instruction),
                )
            except httpx.RequestError as error:
                db.log_llm_call(self.conn, PROVIDER, model.name, False)
                self._retry_unavailable(model, str(error)[:200])
                continue

            if response.status_code >= 400:
                db.log_llm_call(self.conn, PROVIDER, model.name, False)
                message = _error_message(response)
                if response.status_code in {401, 403}:
                    raise AuthenticationFailed(message)
                if response.status_code == 402:
                    raise BillingExhausted(message)
                if response.status_code >= 500:
                    self._retry_unavailable(model, f"HTTP {response.status_code}: {message}")
                    continue
                if response.status_code == 429:
                    if rate_attempt >= self.a.max_retries:
                        self.unavailable.add(model.name)
                        rate_attempt = 0
                        continue
                    delay = _retry_after(response) or backoff_delay(
                        rate_attempt, self.a.backoff_base_s, self.a.backoff_max_s
                    )
                    delay = min(delay + 1, self.a.backoff_max_s)
                    rate_attempt += 1
                    log.warning(
                        "agentrouter %s rate limit; reintento %d en %.0fs",
                        model.name,
                        rate_attempt,
                        delay,
                    )
                    self.sleep(delay)
                    continue
                raise RequestRejected(f"HTTP {response.status_code}: {message}")

            data = response.json()
            usage = data.get("usage") or {}
            db.log_llm_call(
                self.conn,
                PROVIDER,
                model.name,
                True,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            )
            self._errors_5xx[model.name] = 0
            return _response_text(data), model.name

    def structured(
        self,
        prompt: str,
        schema_model: type[StructuredModel],
        instruction: str | None = None,
    ) -> tuple[StructuredModel, str]:
        text, model = self._call(prompt, schema_model, instruction)
        return schema_model.model_validate(_parse_json(text)), model

    def evaluate_job(self, job: sqlite3.Row | dict) -> tuple[EvaluationFeatures, str]:
        text, model = self._call(build_prompt(job, self.cfg.profile), EvaluationFeatures)
        return EvaluationFeatures.model_validate(sanitize(_parse_json(text))), model

    def run(self, limit: int | None = None) -> dict:
        limit = min(limit or self.a.max_jobs_per_run, self.a.max_jobs_per_run)
        jobs = db.pending_for_evaluation(self.conn, limit, self.cfg.searches.active_codes)
        summary = {"candidates": len(jobs), "evaluated": 0, "failed": 0, "stopped": None}
        for job in jobs:
            try:
                features, model = self.evaluate_job(job)
            except DailyQuotaExhausted as error:
                summary["stopped"] = f"daily quota: {error}"
                log.warning("agentrouter: se alcanzó el tope diario local")
                break
            except BillingExhausted as error:
                summary["stopped"] = f"billing: sin saldo en AgentRouter ({error})"
                log.error("agentrouter: la cuenta se quedó sin saldo")
                break
            except AuthenticationFailed as error:
                summary["stopped"] = f"authentication: {error}"
                log.error("agentrouter: %s", error)
                break
            except RequestRejected as error:
                summary["stopped"] = f"request rejected: {error}"
                log.error("agentrouter: %s", error)
                break
            except ModelsUnavailable as error:
                summary["stopped"] = f"models unavailable: {error}"
                log.warning("agentrouter: %s; corto la corrida", error)
                break
            except (ValidationError, json.JSONDecodeError, ValueError) as error:
                summary["failed"] += 1
                log.warning(
                    "agentrouter: respuesta inválida para %s: %s", job["id"], str(error)[:300]
                )
                continue
            except Exception as error:  # noqa: BLE001 — un job roto no corta la corrida
                summary["failed"] += 1
                log.exception("agentrouter: error evaluando %s: %s", job["id"], error)
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
                "agentrouter: %s -> %.0f %s",
                job["id"],
                result.score,
                "(excluido: " + "; ".join(result.exclusion_reasons) + ")"
                if result.excluded
                else "",
            )
        return summary
