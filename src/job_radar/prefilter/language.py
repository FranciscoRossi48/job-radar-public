"""Detección del idioma de la descripción y extracción de evidencia (idioma / residencia)."""

from __future__ import annotations

import re
from functools import lru_cache

from lingua import IsoCode639_1, LanguageDetectorBuilder

from job_radar.config import DeHighLevel, Evidence
from job_radar.models import Snippet


@lru_cache(maxsize=4)
def _detector(langs: tuple[str, ...]):
    codes = [getattr(IsoCode639_1, code.upper()) for code in langs]
    return LanguageDetectorBuilder.from_iso_codes_639_1(*codes).build()


def detect_lang(text: str, langs: list[str], sample_chars: int = 4000) -> str | None:
    """Idioma predominante (ISO 639-1) o None si no se puede determinar."""
    sample = text[:sample_chars].strip()
    if len(sample) < 40:
        return None
    lang = _detector(tuple(sorted(langs))).detect_language_of(sample)
    return lang.iso_code_639_1.name.lower() if lang else None


class EvidenceExtractor:
    def __init__(self, evidence: Evidence, context_chars: int = 150):
        self.ctx = context_chars
        self._patterns = {
            kind: re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
            for kind, pats in (("lang", evidence.lang), ("residency", evidence.residency))
            if pats
        }

    def extract(self, text: str) -> list[Snippet]:
        """Cada match con ±ctx caracteres. Ventanas solapadas del mismo tipo se fusionan."""
        out: list[Snippet] = []
        for kind, pat in self._patterns.items():
            windows: list[list] = []  # [start, end, matches]
            for m in pat.finditer(text):
                start, end = max(0, m.start() - self.ctx), min(len(text), m.end() + self.ctx)
                if windows and start <= windows[-1][1]:
                    windows[-1][1] = max(windows[-1][1], end)
                    windows[-1][2].append(m.group(0))
                else:
                    windows.append([start, end, [m.group(0)]])
            for start, end, matches in windows:
                snippet = " ".join(text[start:end].split())
                out.append(
                    Snippet(kind=kind, match=", ".join(dict.fromkeys(matches)), text=snippet)
                )
        return out


class HighGermanDetector:
    """Detecta, en avisos redactados en alemán, requisitos de alemán por encima de B1."""

    def __init__(self, rules: DeHighLevel):
        self._rx = [re.compile(p, re.IGNORECASE) for p in rules.patterns]
        self._guard = re.compile(rules.nice_guard) if rules.nice_guard else None
        self.window = rules.guard_window_chars

    def find(self, text: str) -> str | None:
        """El primer match que no esté acompañado de una frase de "nice to have"."""
        for rx in self._rx:
            for m in rx.finditer(text):
                around = text[m.start() : m.end() + self.window]
                if self._guard and self._guard.search(around):
                    continue
                return m.group(0)
        return None
