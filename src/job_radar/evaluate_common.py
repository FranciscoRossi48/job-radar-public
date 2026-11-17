"""Piezas compartidas por los evaluadores LLM."""

from __future__ import annotations

import json
import sqlite3
import time
from collections import deque
from collections.abc import Callable

from job_radar.config import Profile
from job_radar.models import RATIONALE_MAX_WORDS


class DailyQuotaExhausted(Exception):
    pass


class BillingExhausted(Exception):
    pass


class AuthenticationFailed(Exception):
    pass


class RequestRejected(Exception):
    pass


class ModelsUnavailable(Exception):
    pass


class ConfigurationError(Exception):
    pass


class RetryableError(Exception):
    pass


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


def profile_brief(profile: Profile) -> str:
    languages = ", ".join(f"{language} {level}" for language, level in profile.languages.items())
    return (
        f"{profile.summary.strip()}\nLanguages (CEFR): {languages}. No other languages.\n"
        f"Stack list: {', '.join(profile.stack)}"
    )


def build_prompt(job: sqlite3.Row | dict, profile: Profile) -> str:
    snippets = json.loads(job["lang_snippets"] or "[]")
    evidence = "\n".join(f"- [{snippet['kind']}] {snippet['text']}" for snippet in snippets)
    evidence = evidence or "(none)"
    return (
        f"## Candidate profile\n{profile_brief(profile)}\n\n"
        f"## Job ad\nTitle: {job['title']}\nCompany: {job['company'] or '?'}\n"
        f"Location: {job['location'] or '?'}\nCountry: {job['country'] or '?'}\n"
        f"Description language: {job['desc_lang'] or '?'}\n\n"
        f"### Condensed description\n{job['desc_condensed'] or '(empty)'}\n\n"
        f"### Evidence snippets (verbatim)\n{evidence}\n"
    )


def sanitize(data: dict) -> dict:
    """Arreglos menores que no cambian el significado (límite de palabras y de red flags)."""
    words = str(data.get("rationale", "")).split()
    data["rationale"] = " ".join(words[:RATIONALE_MAX_WORDS])
    data["red_flags"] = list(data.get("red_flags") or [])[:5]
    return data
