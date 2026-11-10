"""Deduplicación: clave (empresa + título normalizado + país) y simhash de la descripción."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_GENDER = re.compile(
    r"\((?:[mwfdhx]\s*/\s*){1,3}[mwfdhx]\)|\b(?:m|w|f|h)/(?:w|m|f|h)(?:/(?:d|x))?\b|\*in\b|:in\b",
    re.I,
)
_WORK_MODE = re.compile(r"\((?:[\s,/|-]|on[- ]?site|hybrid|remote)+\)", re.I)
_AT_TAIL = re.compile(r"\s+@\s+.*$")
_PERCENT = re.compile(r"\b\d{2,3}\s*(?:-|–|to|bis)?\s*(?:\d{2,3})?\s*%")
_LEGAL = re.compile(
    r"\b(?:ag|sa|gmbh|bv|nv|ltd|limited|inc|llc|sarl|se|plc|"
    r"group|holding|switzerland|schweiz|suisse|netherlands|nederland|luxembourg)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def normalize_title(title: str) -> str:
    t = _GENDER.sub(" ", title or "")
    t = _WORK_MODE.sub(" ", t)
    t = _AT_TAIL.sub(" ", t)
    t = _PERCENT.sub(" ", t)
    return _NON_ALNUM.sub(" ", _fold(t)).strip()


def normalize_company(company: str | None) -> str:
    return _NON_ALNUM.sub(" ", _LEGAL.sub(" ", _fold(company or "").replace(".", ""))).strip()


def dedup_key(company: str | None, title: str, country: str | None) -> str | None:
    c, t = normalize_company(company), normalize_title(title)
    if not c or not t:
        return None
    return f"{c}|{t}|{(country or '').upper()}"


def simhash(text: str, ngram: int = 3) -> int:
    """Simhash de 64 bits sobre shingles de `ngram` palabras."""
    words = _NON_ALNUM.sub(" ", _fold(text)).split()
    if len(words) < ngram:
        return 0
    acc = [0] * 64
    for i in range(len(words) - ngram + 1):
        h = int.from_bytes(
            hashlib.blake2b(" ".join(words[i : i + ngram]).encode(), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            acc[bit] += 1 if (h >> bit) & 1 else -1
    return sum(1 << bit for bit in range(64) if acc[bit] > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()
