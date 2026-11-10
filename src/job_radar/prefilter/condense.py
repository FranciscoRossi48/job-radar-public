"""Limpieza de descripciones: sacar boilerplate y priorizar requisitos."""

from __future__ import annotations

import re

from job_radar.config import CondenseRules

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_ESCAPES = re.compile(r"\\([*_#\-.!()\[\]+])")
_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*(?P<h1>.+?)|\*\*(?P<h2>[^*]{2,80})\*\*:?|(?P<h3>[^.!?\n]{2,60}):)\s*$"
)


def _clean_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _ESCAPES.sub(r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)]. El texto antes del primer heading tiene heading ''."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            heading = (m.group("h1") or m.group("h2") or m.group("h3") or "").strip(" *:")
            sections.append((heading, []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in sections if h or "\n".join(b).strip()]


class Condenser:
    def __init__(self, rules: CondenseRules, max_chars: int = 2000):
        self.max_chars = max_chars

        def rx(words: list[str]) -> re.Pattern | None:
            return re.compile("|".join(words), re.IGNORECASE) if words else None

        self._priority = rx(rules.priority)
        self._secondary = rx(rules.secondary)
        self._drop = rx(rules.drop)
        self._drop_sent = (
            re.compile(
                r"[^.\n]*(?:" + "|".join(rules.drop_sentences) + r")[^.\n]*[.\n]?", re.IGNORECASE
            )
            if rules.drop_sentences
            else None
        )

    def _rank(self, heading: str) -> int | None:
        """0 = requisitos, 1 = tareas, 2 = otro, None = descartar."""
        if not heading:
            return 2
        if self._drop and self._drop.search(heading):
            return None
        if self._priority and self._priority.search(heading):
            return 0
        if self._secondary and self._secondary.search(heading):
            return 1
        return 2

    def condense(self, description: str) -> str:
        text = _clean_markdown(description or "")
        if self._drop_sent:
            text = self._drop_sent.sub("", text)
        ranked = []
        for i, (heading, body) in enumerate(_split_sections(text)):
            rank = self._rank(heading)
            if rank is not None and body:
                ranked.append((rank, i, f"{heading}:\n{body}" if heading else body))
        ranked.sort()
        out = "\n\n".join(chunk for _, _, chunk in ranked)
        if len(out) <= self.max_chars:
            return out
        cut = out[: self.max_chars]
        return cut[: cut.rfind(" ")] + " …" if " " in cut else cut
