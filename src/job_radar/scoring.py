"""Score determinístico 0-100 a partir de las features del LLM + datos del job."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from job_radar.config import CEFR_ORDER, HardExclusions, Profile, ScoringConfig
from job_radar.models import EvaluationFeatures, LangLevel, Seniority


@dataclass
class JobContext:
    """Datos del job (no del LLM) que entran en el score."""

    location: str | None = None
    country: str | None = None
    desc_lang: str | None = None
    text: str = ""  # título + descripción, para detectar el stack en el aviso
    title: str = ""

    @classmethod
    def from_row(cls, row) -> JobContext:
        """Desde una fila de `jobs` (sqlite3.Row o dict con las mismas claves)."""
        return cls(
            location=row["location"],
            country=row["country"],
            desc_lang=row["desc_lang"],
            text=f"{row['title'] or ''}\n{row['description_raw'] or ''}",
            title=row["title"] or "",
        )


_SENTENCE_END = re.compile(r"[.!?;\n]")


def _clearance_required(text: str, hx: HardExclusions) -> bool:
    """True si el texto pide security clearance en al menos un match no negado."""
    prev_end = 0
    for m in re.finditer(hx.security_clearance_pattern, text):
        if not hx.security_clearance_negation:
            return True
        w = hx.security_clearance_negation_window
        # Sin cruzar el match anterior: su "no" no niega a este.
        before = text[max(prev_end, m.start() - w) : m.start()]
        prev_end = m.end()
        after = text[m.end() : m.end() + w]
        # Solo dentro de la misma oración: "Clearance needed. No relocation" sigue excluyendo.
        before = _SENTENCE_END.split(before)[-1]
        after = _SENTENCE_END.split(after)[0]
        if not re.search(hx.security_clearance_negation, f"{before} {after}"):
            return True
    return False


@lru_cache(maxsize=8)
def _stack_patterns(stack: tuple[str, ...], aliases: tuple) -> dict[str, re.Pattern]:
    alias_map = dict(aliases)
    out = {}
    for tech in stack:
        names = [tech, *alias_map.get(tech, ())]
        alts = "|".join(re.escape(n).replace(r"\ ", r"[\s-]?") for n in names)
        out[tech] = re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.IGNORECASE)
    return out


def stack_in_text(text: str, profile: Profile) -> set[str]:
    """Tecnologías del stack del perfil que aparecen en el texto del aviso."""
    if not text:
        return set()
    aliases = tuple((k, tuple(v)) for k, v in sorted(profile.stack_aliases.items()))
    pats = _stack_patterns(tuple(profile.stack), aliases)
    return {tech for tech, rx in pats.items() if rx.search(text)}


@dataclass
class ScoreResult:
    score: float
    excluded: bool
    breakdown: list[dict] = field(default_factory=list)  # [{rule, points}] o [{exclude: motivo}]

    @property
    def exclusion_reasons(self) -> list[str]:
        return [b["exclude"] for b in self.breakdown if "exclude" in b]


def _rank(cefr: str | None) -> int:
    """A1=1 ... C2=6; 0 = ningún conocimiento."""
    return CEFR_ORDER.index(cefr) + 1 if cefr in CEFR_ORDER else 0


def score(
    f: EvaluationFeatures, job: JobContext, profile: Profile, cfg: ScoringConfig
) -> ScoreResult:
    hx, pen, bon = cfg.hard_exclusions, cfg.penalties, cfg.bonus
    out: list[dict] = []
    excluded: list[str] = []

    # --- Exclusiones duras ---
    for req in f.lang_required:
        if req.level != LangLevel.required:
            continue
        asked = (
            req.cefr.value
            if req.cefr
            else cfg.missing_cefr.get(req.lang.value, cfg.missing_cefr.get("default", "B2"))
        )
        mine = profile.languages.get(req.lang.value)
        if _rank(asked) > _rank(mine):
            if hx.lang_above_profile:
                excluded.append(f"{req.lang.value} {asked} required (profile: {mine or 'none'})")
        elif req.lang.value in bon.lang:
            pass  # idioma a favor: suma en la sección de bonus, sin penalización
        else:
            out.append(
                {
                    "rule": f"lang required within level: {req.lang.value} {asked}",
                    "points": -pen.lang_required_within_level,
                }
            )
    if (
        hx.residency
        and f.residency_hard_req
        and (f.residency_country is None or f.residency_country not in profile.residency_countries)
    ):
        excluded.append(f"residency required: {f.residency_country or 'unspecified'}")
    if hx.seniority_junior and f.seniority == Seniority.junior:
        excluded.append("junior")
    if (
        hx.security_clearance
        and hx.security_clearance_pattern
        and any(_clearance_required(t, hx) for t in [*f.red_flags, job.text])
    ):
        excluded.append("security clearance required")

    if excluded:
        return ScoreResult(0.0, True, [{"exclude": r} for r in excluded])

    # --- Penalizaciones ---
    for req in f.lang_required:
        # "nice" no penaliza si el aviso pide un nivel concreto que ya tenés.
        covered = req.cefr is not None and _rank(req.cefr.value) <= _rank(
            profile.languages.get(req.lang.value)
        )
        nice_pts = pen.lang_nice.get(req.lang.value, pen.lang_nice.get("default", 0))
        if req.lang.value in bon.lang:
            continue  # idioma a favor: nunca penaliza
        if req.level == LangLevel.nice and not covered and nice_pts:
            out.append({"rule": f"lang nice: {req.lang.value}", "points": -nice_pts})
    if job.desc_lang and job.desc_lang in pen.desc_lang:
        out.append(
            {"rule": f"description in {job.desc_lang}", "points": -pen.desc_lang[job.desc_lang]}
        )
    priors = [p for p in pen.location_priors if re.search(p.pattern, job.location or "", re.I)]
    if priors:
        worst = max(priors, key=lambda p: p.penalty)
        out.append({"rule": f"location prior: {worst.name}", "points": -worst.penalty})
    if f.onsite_only:
        out.append({"rule": "onsite only", "points": -pen.onsite_only})
    flags = [
        r for r in f.red_flags if not (pen.red_flag_ignore and re.search(pen.red_flag_ignore, r))
    ]
    if flags:
        out.append(
            {
                "rule": f"red flags: {'; '.join(flags)}",
                "points": -min(pen.red_flag * len(flags), pen.red_flag_max),
            }
        )

    # --- Bonus ---
    # Unión de lo que extrajo el LLM y lo que aparece literalmente en el aviso. El nombre
    # canónico es el del perfil (así "bigquery" del LLM y "BigQuery" del texto cuentan una vez).
    canon = {t.lower(): t for t in profile.stack}
    from_llm = {canon[s.lower()] for s in f.stack_match if s.lower() in canon}
    matched = sorted(from_llm | stack_in_text(job.text, profile), key=str.lower)
    if matched:
        pts = min(bon.stack_match_per_item * len(matched), bon.stack_match_max)
        out.append({"rule": f"stack: {', '.join(matched)}", "points": pts})
    if pts := bon.seniority.get(f.seniority.value, 0):
        out.append({"rule": f"seniority {f.seniority.value}", "points": pts})
    adj = bon.adjacent_role
    if adj.pattern and re.search(adj.pattern, job.title or ""):
        if len(matched) >= adj.min_stack:
            out.append({"rule": "role adjacent", "points": adj.points})
        elif pts := bon.role_family.get(adj.fallback_role, 0):
            out.append({"rule": f"role adjacent, stack < {adj.min_stack}", "points": pts})
    elif pts := bon.role_family.get(f.role_family.value, 0):
        out.append({"rule": f"role {f.role_family.value}", "points": pts})
    # Idioma a favor: la mejor coincidencia por idioma (required > nice), una sola vez.
    for lang, by_level in bon.lang.items():
        levels = {r.level.value for r in f.lang_required if r.lang.value == lang}
        level = next((lv for lv in ("required", "nice") if lv in levels), None)
        if level and (pts := by_level.get(level, 0)):
            out.append({"rule": f"lang {level}: {lang}", "points": pts})
    if pts := bon.country.get((job.country or "").upper(), 0):
        out.append({"rule": f"country {job.country.upper()}", "points": pts})

    total = cfg.base + sum(b["points"] for b in out)
    return ScoreResult(
        round(max(0.0, min(100.0, total)), 1), False, [{"rule": "base", "points": cfg.base}, *out]
    )
