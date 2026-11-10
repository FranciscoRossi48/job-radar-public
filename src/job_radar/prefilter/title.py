"""Reglas determinísticas sobre el título del aviso."""

from __future__ import annotations

import re

from job_radar.config import TitleRules


class TitleFilter:
    def __init__(self, rules: TitleRules):
        flags = re.IGNORECASE
        self._neutralize = [re.compile(p, flags) for p in rules.neutralize]
        self._exclude = re.compile(r"\b(?:" + "|".join(rules.exclude_words) + r")\b", flags)
        self._include = [re.compile(p, flags) for p in rules.include_any]

    def check(self, title: str) -> str | None:
        """Devuelve el motivo de exclusión, o None si el título pasa."""
        cleaned = title
        for pat in self._neutralize:
            cleaned = pat.sub(" ", cleaned)
        if m := self._exclude.search(cleaned):
            return f"title_excluded:{m.group(0).lower()}"
        if self._include and not any(p.search(title) for p in self._include):
            return "title_not_relevant"
        return None
