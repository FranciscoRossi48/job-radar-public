"""Modelos de dominio compartidos."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    pending = "pending"
    evaluated = "evaluated"
    excluded_prefilter = "excluded_prefilter"
    duplicate = "duplicate"
    stale = "stale"


class RawJob(BaseModel):
    """Aviso normalizado que devuelve cualquier adapter de ingesta."""

    source: str
    job_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    country: str | None = None  # ISO-2: CH, NL, LU
    description: str = ""
    posted_at: date | None = None

    @property
    def id(self) -> str:
        return f"{self.source}:{self.job_id}"


class Snippet(BaseModel):
    kind: str  # "lang" | "residency"
    match: str
    text: str


class PrefilterResult(BaseModel):
    excluded: bool
    reason: str | None = None
    desc_lang: str | None = None
    desc_condensed: str = ""
    snippets: list[Snippet] = Field(default_factory=list)


# --- Schema de evaluación (compartido por Gemini y Claude) ---------------------------------

RATIONALE_MAX_WORDS = 25


class Lang(StrEnum):
    de = "de"
    fr = "fr"
    nl = "nl"
    it = "it"
    lb = "lb"
    es = "es"
    other = "other"


class LangLevel(StrEnum):
    none = "none"
    nice = "nice"
    required = "required"


class Cefr(StrEnum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class Seniority(StrEnum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    unknown = "unknown"


class RoleFamily(StrEnum):
    DE = "DE"
    AE = "AE"
    BI = "BI"
    DA = "DA"
    DS = "DS"
    other = "other"


class LangReq(BaseModel):
    lang: Lang
    level: LangLevel
    cefr: Cefr | None = None


class EvaluationFeatures(BaseModel):
    """Lo que extrae el LLM. Sin score: el score se calcula en Python (scoring.py)."""

    lang_required: list[LangReq] = Field(
        default_factory=list, description="Non-English languages mentioned as requirements."
    )
    residency_hard_req: bool = Field(
        description="True ONLY if the ad demands current residence/location in a country "
        "(e.g. 'must currently live in X', 'local candidates only'). Work permit / right to "
        "work / EU citizenship requirements are NOT residency requirements."
    )
    residency_country: str | None = Field(
        default=None, description="ISO-2 country of the residency requirement, if any."
    )
    onsite_only: bool
    seniority: Seniority
    role_family: RoleFamily
    stack_match: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_len(cls, v: str) -> str:
        if len(v.split()) > RATIONALE_MAX_WORDS:
            raise ValueError(f"rationale must be at most {RATIONALE_MAX_WORDS} words")
        return v.strip()

    @field_validator("red_flags")
    @classmethod
    def _red_flags_len(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            raise ValueError("red_flags must have at most 5 items")
        return v

    @field_validator("residency_country")
    @classmethod
    def _iso2(cls, v: str | None) -> str | None:
        return v.strip().upper() if v and v.strip() else None


class Evaluation(EvaluationFeatures):
    """Evaluación de un job puntual (lo que recibe save_evaluations en el MCP)."""

    job_id: str
