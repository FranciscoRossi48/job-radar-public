"""Request schemas for the local web API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class DecisionRequest(BaseModel):
    decision: Literal["applied", "discarded"] | None = None
    note: str | None = Field(default=None, max_length=1000)


class IngestRequest(BaseModel):
    sources: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=500)


class EvaluateRequest(BaseModel):
    limit: int | None = Field(default=25, ge=1, le=500)


class EvaluateAllRequest(BaseModel):
    max_jobs: int = Field(default=500, ge=1, le=2000)


class CvGenerateRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    consent: bool


class DailyRequest(BaseModel):
    skip_ingest: bool = False
    skip_evaluate: bool = False
    eval_limit: int | None = Field(default=None, ge=1, le=500)


class ExportRequest(BaseModel):
    mode: Literal["new", "all", "day"] = "new"
    day: date | None = None
    min_score: float | None = Field(default=None, ge=0, le=100)


class ConfigFileRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)
