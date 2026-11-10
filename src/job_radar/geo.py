"""Resolución de país (ISO-2) a partir de códigos explícitos o texto de ubicación."""

from __future__ import annotations

import re
import unicodedata


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


class CountryResolver:
    def __init__(self, aliases: dict[str, list[str]], targets: list[str] | None = None):
        # Se resuelven todos los países con alias (un aviso de Amsterdam queda como NL, no como
        # "desconocido"), pero solo se guardan los de `targets` (vacío = todos).
        self.targets = {code.upper() for code in (targets or aliases)}
        self._rx = {
            code.upper(): re.compile(
                r"\b(?:" + "|".join(re.escape(_fold(a)) for a in names) + r")\b"
            )
            for code, names in aliases.items()
        }

    def resolve(self, location: str | None = None, explicit: str | None = None) -> str | None:
        """ISO-2 si el país es identificable. Un código explícito tiene prioridad."""
        if explicit:
            code = explicit.strip().upper()
            if len(code) == 2:
                return code
            location = f"{explicit} {location or ''}"
        if not location:
            return None
        text = _fold(location)
        for code, rx in self._rx.items():
            if rx.search(text):
                return code
        return None

    def keep(self, location: str | None, explicit: str | None, fallback: str | None) -> str | None:
        """País si es un objetivo; None si hay que descartar el aviso.

        `fallback` (el país de la empresa en companies.yaml) solo se usa si la ubicación viene vacía.
        """
        code = self.resolve(location, explicit)
        if code is None and not (location or "").strip() and not explicit:
            code = fallback.upper() if fallback else None
        return code if code in self.targets else None
