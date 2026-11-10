"""Vía Gemini, desatendida: evalúa jobs pendientes con structured output y calcula el score.

Solo se envía desc_condensed + snippets de evidencia + un resumen corto del perfil.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from job_radar import db
from job_radar.config import Config, GeminiModel, GeminiSettings, Profile
from job_radar.ingest.base import backoff_delay
from job_radar.models import RATIONALE_MAX_WORDS, EvaluationFeatures
from job_radar.scoring import JobContext, score

log = logging.getLogger(__name__)
PROVIDER = "gemini"
_PACIFIC = ZoneInfo("America/Los_Angeles")


class DailyQuotaExhausted(Exception):
    pass


class BillingExhausted(Exception):
    """402: la cuenta prepaga se quedó sin saldo (Google corta todas las claves)."""


class ModelsUnavailable(Exception):
    """Todos los modelos de la cadena agotaron su cuota o están caídos (5xx) en esta corrida."""


class RateLimiter:
    """Ventana deslizante de 60s para RPM."""

    def __init__(
        self,
        rpm: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.rpm, self.clock, self.sleep = rpm, clock, sleep
        self.calls: deque[float] = deque()

    def acquire(self) -> None:
        now = self.clock()
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        if len(self.calls) >= self.rpm:
            self.sleep(60 - (now - self.calls[0]) + 0.5)
            return self.acquire()
        self.calls.append(self.clock())


def profile_brief(p: Profile) -> str:
    langs = ", ".join(f"{k} {v}" for k, v in p.languages.items())
    return (
        f"{p.summary.strip()}\nLanguages (CEFR): {langs}. No other languages.\n"
        f"Stack list: {', '.join(p.stack)}"
    )


def build_prompt(job: sqlite3.Row | dict, profile: Profile) -> str:
    snippets = json.loads(job["lang_snippets"] or "[]")
    ev = "\n".join(f"- [{s['kind']}] {s['text']}" for s in snippets) or "(none)"
    return (
        f"## Candidate profile\n{profile_brief(profile)}\n\n"
        f"## Job ad\nTitle: {job['title']}\nCompany: {job['company'] or '?'}\n"
        f"Location: {job['location'] or '?'}\nCountry: {job['country'] or '?'}\n"
        f"Description language: {job['desc_lang'] or '?'}\n\n"
        f"### Condensed description\n{job['desc_condensed'] or '(empty)'}\n\n"
        f"### Evidence snippets (verbatim)\n{ev}\n"
    )


def sanitize(data: dict) -> dict:
    """Arreglos menores que no cambian el significado (límite de palabras y de red flags)."""
    words = str(data.get("rationale", "")).split()
    data["rationale"] = " ".join(words[:RATIONALE_MAX_WORDS])
    data["red_flags"] = list(data.get("red_flags") or [])[:5]
    return data


def _retry_delay(err) -> float | None:
    """Lee RetryInfo.retryDelay ('34s') del detalle del error, si viene."""
    m = re.search(
        r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'|\"retryDelay\":\s*\"(\d+(?:\.\d+)?)s\"",
        str(err.details),
    )
    return float(m.group(1) or m.group(2)) if m else None


def _is_daily_quota(err) -> bool:
    return bool(
        re.search(r"PerDay|per day|RequestsPerDay", str(err.details) + str(err.message), re.I)
    )


class GeminiEvaluator:
    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        client=None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg, self.conn = cfg, conn
        self.g: GeminiSettings = cfg.llm.gemini
        self.sleep = sleep
        self.limiters = {
            m.name: RateLimiter(m.rpm, clock=clock, sleep=sleep) for m in self.g.models
        }
        self.exhausted: set[str] = set()  # modelos sin cuota diaria en esta corrida
        self.unavailable: set[str] = set()  # modelos caídos (5xx persistente) en esta corrida
        self._errors_5xx: dict[str, int] = {}
        self.rubric = cfg.rubric()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise RuntimeError("Falta GEMINI_API_KEY en .env")
            timeout_ms = int(self.cfg.llm.gemini.request_timeout_s * 1000)  # la API usa ms
            self._client = genai.Client(
                api_key=key, http_options=types.HttpOptions(timeout=timeout_ms)
            )
        return self._client

    def _config(self, m: GeminiModel):
        from google.genai import types

        kw = dict(
            system_instruction=self.rubric,
            response_mime_type="application/json",
            response_json_schema=EvaluationFeatures.model_json_schema(),
            temperature=self.g.temperature,
        )
        if m.thinking_level:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=m.thinking_level)
        elif m.thinking_budget is not None:
            kw["thinking_config"] = types.ThinkingConfig(thinking_budget=m.thinking_budget)
        return types.GenerateContentConfig(**kw)

    def _calls_today(self, model: str) -> int:
        midnight = datetime.now(_PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
        return db.llm_calls_since(self.conn, PROVIDER, midnight, model)

    def _next_model(self) -> GeminiModel:
        for m in self.g.models:
            if m.name in self.exhausted or m.name in self.unavailable:
                continue
            if self._calls_today(m.name) >= m.rpd:
                log.info("gemini: %s alcanzó el RPD local (%d)", m.name, m.rpd)
                self.exhausted.add(m.name)
                continue
            return m
        if self.unavailable:
            raise ModelsUnavailable(
                f"sin modelos disponibles (caídos: {sorted(self.unavailable)}, "
                f"sin cuota: {sorted(self.exhausted)})"
            )
        raise DailyQuotaExhausted("todos los modelos de la cadena agotaron su cuota diaria")

    def _has_alternative(self, name: str) -> bool:
        """¿Queda otro modelo de la cadena con cuota y no caído?"""
        return any(
            m.name != name
            and m.name not in self.exhausted
            and m.name not in self.unavailable
            and self._calls_today(m.name) < m.rpd
            for m in self.g.models
        )

    def _call(self, prompt: str) -> tuple[str, str]:
        """(texto de la respuesta, modelo usado)."""
        from google.genai import errors

        attempt = 0
        while True:
            m = self._next_model()
            self.limiters[m.name].acquire()
            try:
                resp = self.client.models.generate_content(
                    model=m.name, contents=prompt, config=self._config(m)
                )
                u = getattr(resp, "usage_metadata", None)
                out_tokens = None
                if u is not None:
                    out_tokens = (u.candidates_token_count or 0) + (
                        getattr(u, "thoughts_token_count", None) or 0
                    )
                db.log_llm_call(
                    self.conn,
                    PROVIDER,
                    m.name,
                    True,
                    getattr(u, "prompt_token_count", None),
                    out_tokens,
                )
                self._errors_5xx[m.name] = 0
                return resp.text, m.name
            except errors.APIError as e:
                db.log_llm_call(self.conn, PROVIDER, m.name, False)
                if e.code == 402:
                    raise BillingExhausted(str(e.message)[:200]) from e
                if e.code == 429 and _is_daily_quota(e):
                    log.warning(
                        "gemini: %s agotó la cuota diaria; paso al siguiente modelo", m.name
                    )
                    self.exhausted.add(m.name)
                    continue
                if (e.code or 0) >= 500:
                    n = self._errors_5xx[m.name] = self._errors_5xx.get(m.name, 0) + 1
                    alternative = self._has_alternative(m.name)
                    # Con otro modelo disponible, se saltea rápido. Si es el último que queda,
                    # se le tiene más paciencia (la saturación suele ser de a ratos).
                    limit = self.g.unavailable_after if alternative else self.g.last_model_retries
                    if n >= limit:
                        log.warning(
                            "gemini: %s sigue con %s; lo salteo en esta corrida", m.name, e.code
                        )
                        self.unavailable.add(m.name)
                        continue
                    delay = min(
                        backoff_delay(n - 1, self.g.backoff_base_s, self.g.backoff_max_s) + 1,
                        self.g.backoff_max_s,
                    )
                    log.warning(
                        "gemini %s %s (%d/%d%s); reintento en %.0fs",
                        m.name,
                        e.code,
                        n,
                        limit,
                        "" if alternative else ", último modelo",
                        delay,
                    )
                    self.sleep(delay)
                    continue
                retryable = e.code == 429 or (e.code or 0) >= 500
                if not retryable or attempt >= self.g.max_retries:
                    raise
                delay = _retry_delay(e) or backoff_delay(
                    attempt, self.g.backoff_base_s, self.g.backoff_max_s
                )
                delay = min(delay + 1, self.g.backoff_max_s)
                log.warning(
                    "gemini %s %s %s; reintento %d en %.0fs",
                    m.name,
                    e.code,
                    e.status,
                    attempt + 1,
                    delay,
                )
                attempt += 1
                self.sleep(delay)

    def evaluate_job(self, job: sqlite3.Row | dict) -> tuple[EvaluationFeatures, str]:
        text, model = self._call(build_prompt(job, self.cfg.profile))
        return EvaluationFeatures.model_validate(sanitize(json.loads(text))), model

    def run(self, limit: int | None = None) -> dict:
        limit = min(limit or self.g.max_jobs_per_run, self.g.max_jobs_per_run)
        jobs = db.pending_for_evaluation(self.conn, limit, self.cfg.searches.active_codes)
        summary = {"candidates": len(jobs), "evaluated": 0, "failed": 0, "stopped": None}
        for job in jobs:
            try:
                f, model = self.evaluate_job(job)
            except DailyQuotaExhausted as e:
                summary["stopped"] = f"daily quota: {e}"
                log.warning("gemini: cuota diaria agotada, corto la corrida")
                break
            except BillingExhausted as e:
                summary["stopped"] = f"billing: sin saldo en la cuenta de Google ({e})"
                log.error("gemini: la cuenta se quedó sin saldo; cargá crédito en AI Studio")
                break
            except ModelsUnavailable as e:
                summary["stopped"] = f"models unavailable: {e}"
                log.warning("gemini: %s; corto la corrida", e)
                break
            except (ValidationError, json.JSONDecodeError) as e:
                summary["failed"] += 1
                log.warning("gemini: respuesta inválida para %s: %s", job["id"], str(e)[:300])
                continue
            except Exception as e:  # noqa: BLE001 — un job roto no corta la corrida
                summary["failed"] += 1
                log.exception("gemini: error evaluando %s: %s", job["id"], e)
                continue
            ctx = JobContext.from_row(job)
            result = score(f, ctx, self.cfg.profile, self.cfg.scoring)
            db.insert_evaluation(
                self.conn,
                job_id=job["id"],
                evaluator=PROVIDER,
                model=model,
                prompt_version=self.cfg.llm.prompt_version,
                features=f,
                result=result,
            )
            self.conn.commit()
            summary["evaluated"] += 1
            log.info(
                "gemini: %s -> %.0f %s",
                job["id"],
                result.score,
                "(excluido: " + "; ".join(result.exclusion_reasons) + ")"
                if result.excluded
                else "",
            )
        return summary
