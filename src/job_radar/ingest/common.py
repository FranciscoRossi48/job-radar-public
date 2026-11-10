"""Utilidades compartidas por los adapters HTTP (SwissDevJobs, IamExpat, ATS)."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from markdownify import markdownify

from job_radar.geo import CountryResolver
from job_radar.http import Http

_HUMAN_DATE_FMTS = ("%d %b %Y", "%b %d, %Y", "%d-%b-%Y", "%B %d, %Y", "%d %B %Y")


@dataclass
class FetchContext:
    http: Http
    geo: CountryResolver
    is_relevant: Callable[[str], bool]  # el título pasa el filtro de título
    is_known: Callable[[str], bool]  # el id ya está en la DB (no hace falta pedir el detalle)


def html_to_text(raw: str | None) -> str:
    """HTML (a veces escapado dos veces, como en Greenhouse) -> markdown legible."""
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    return markdownify(raw, heading_style="ATX", strip=["img", "script", "style"]).strip()


def parse_date(value) -> date | None:
    """Acepta ISO 8601, 'YYYY-MM-DD HH:MM:SS UTC', epoch en ms o '12 Oct 2026' / 'Oct 3, 2026'."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float) or str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000).date()
        s = re.sub(r"\s+", " ", str(value).strip())
        s_norm = re.sub(r"\bSept\b", "Sep", s, flags=re.I)
        for fmt in _HUMAN_DATE_FMTS:
            try:
                return datetime.strptime(s_norm, fmt).date()
            except ValueError:
                pass
        return datetime.fromisoformat(s.replace(" UTC", "+00:00").replace("Z", "+00:00")).date()
    except (ValueError, OSError):
        return None
