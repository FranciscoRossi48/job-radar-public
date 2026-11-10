"""Cliente HTTP común: User-Agent, timeouts, pausa entre requests y backoff ante 429/5xx."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

import httpx

from job_radar.ingest.base import RateLimited, backoff_delay

log = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) job-radar/0.1 (personal job search)"


class Http:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_UA,
        timeout_s: float = 30,
        delay_s: tuple[float, float] = (1, 2),
        max_retries: int = 3,
        backoff_base_s: float = 10,
        backoff_max_s: float = 120,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ):
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self.delay_s = delay_s
        self.max_retries = max_retries
        self.base, self.cap = backoff_base_s, backoff_max_s
        self._sleep = sleep
        self._last = 0.0

    def _pace(self) -> None:
        wait = random.uniform(*self.delay_s) - (time.monotonic() - self._last)
        if wait > 0 and self._last:
            self._sleep(wait)
        self._last = time.monotonic()

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        """Devuelve la respuesta (también 4xx). Ante 429/5xx reintenta; 429 persistente -> RateLimited."""
        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                resp = self.client.request(method, url, **kw)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise
                log.warning("%s %s: %s; reintento", method, url, e)
                self._sleep(backoff_delay(attempt, self.base, self.cap))
                continue
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            if attempt == self.max_retries:
                if resp.status_code == 429:
                    raise RateLimited(url)
                return resp
            retry_after = resp.headers.get("Retry-After", "")
            delay = (
                min(float(retry_after), self.cap)
                if retry_after.isdigit()
                else backoff_delay(attempt, self.base, self.cap)
            )
            log.warning("%s %s -> %d; reintento en %.0fs", method, url, resp.status_code, delay)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw):
        resp = self.get(url, **kw)
        resp.raise_for_status()
        return resp.json()

    def post_json(self, url: str, payload: dict, **kw):
        resp = self.request("POST", url, json=payload, **kw)
        resp.raise_for_status()
        return resp.json()
