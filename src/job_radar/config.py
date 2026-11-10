"""Carga de configuración YAML (config/*.yaml) a modelos Pydantic."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get("JOB_RADAR_HOME", Path(__file__).resolve().parents[2]))
CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1", "C2"]


class DedupSettings(BaseModel):
    simhash_max_distance: int = 3
    simhash_same_title_max_distance: int = 10
    simhash_min_chars: int = 400
    window_days: int = 45


class McpSettings(BaseModel):
    evaluator: str = "claude"
    shortlist_score_range: tuple[float, float] = (50, 80)
    shortlist_days: int = 1
    batch_max: int = 25


class LoggingSettings(BaseModel):
    level: str = "INFO"
    file: str = "job-radar.log"
    max_bytes: int = 5_000_000
    backup_count: int = 5
    runs_file: str = "runs.jsonl"
    quiet: list[str] = Field(default_factory=list)


class NetworkSettings(BaseModel):
    check_hosts: list[str] = Field(default_factory=list)
    wait_s: float = 120
    poll_s: float = 10
    error_patterns: str = r"nodename nor servname|Max retries exceeded|ConnectError"
    max_daily_attempts: int = 3


class NtfySettings(BaseModel):
    enabled: bool = False
    server: str = "https://ntfy.sh"
    notify_success: bool = True
    top_n: int = 3
    timeout_s: float = 10
    retries: int = 3
    retry_wait_s: float = 20


class BackupSettings(BaseModel):
    enabled: bool = False
    subdir: str = "backups"
    keep: int = 7


class AppSettings(BaseModel):
    db_path: str = "jobs.db"
    logs_dir: str = "logs"
    exports_dir: str = "exports"
    exports_mirror_dirs: list[str] = Field(default_factory=list)
    lease_minutes: int = 30
    stale_days: int = 14
    notify_on_error: bool = True
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    ntfy: NtfySettings = Field(default_factory=NtfySettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


class Profile(BaseModel):
    summary: str
    languages: dict[str, str] = Field(default_factory=dict)
    residency_countries: list[str] = Field(default_factory=list)
    stack: list[str] = Field(default_factory=list)
    stack_aliases: dict[str, list[str]] = Field(default_factory=dict)


class Backoff(BaseModel):
    max_retries: int = 3
    base_s: float = 30
    max_s: float = 300


class JobSpySettings(BaseModel):
    sites: list[str] = Field(default_factory=lambda: ["linkedin", "indeed", "google"])
    hours_old: int = 24
    # Búsquedas fijas (`queries`): ventana desde la última corrida limpia, en vez de hours_old.
    since_last_run: bool = True
    since_last_run_min_h: int = 24
    since_last_run_margin_h: int = 6
    results_wanted: int = 25
    linkedin_fetch_description: bool = True
    linkedin_detail_delay_s: tuple[float, float] = (2, 5)
    linkedin_detail_max_failures: int = 5
    linkedin_missing_warn_min: int = 5
    delay_between_calls_s: tuple[float, float] = (4, 9)
    cooldown_after_cut_s: tuple[float, float] = (60, 120)
    # El rescate (--skip-ingest) retoma las búsquedas de LinkedIn cortadas por 429 a la mañana.
    retry_in_rescue: bool = True
    backoff: Backoff = Field(default_factory=Backoff)
    google_template: str = "{query} jobs near {location} since yesterday"


class CountrySearch(BaseModel):
    code: str
    linkedin_location: str
    indeed_country: str
    indeed_location: str = ""
    google_location: str


class HttpSettings(BaseModel):
    timeout_s: float = 30
    delay_s: tuple[float, float] = (1.0, 2.5)
    max_retries: int = 3
    backoff_base_s: float = 10
    backoff_max_s: float = 120


class SwissDevJobsSettings(BaseModel):
    enabled: bool = True
    tech_categories: list[str] = Field(default_factory=list)
    skip_exp_levels: list[str] = Field(default_factory=lambda: ["Junior"])
    max_details: int = 60


class IamExpatSettings(BaseModel):
    enabled: bool = True
    listing_urls: list[str] = Field(default_factory=list)
    max_pages: int = 2
    max_details: int = 30


class AtsSettings(BaseModel):
    max_details_per_company: int = 40
    workday_search_terms: list[str] = Field(default_factory=lambda: ["data"])


class Searches(BaseModel):
    jobspy: JobSpySettings = Field(default_factory=JobSpySettings)
    queries: list[str]
    rotating_queries: list[str] = Field(default_factory=list)
    rotating_per_day: int = 0
    countries: list[CountrySearch]
    geo: dict[str, list[str]]
    # Subconjunto de países a buscar y guardar (ISO-2). Vacío = todos los de `geo`.
    active_countries: list[str] = Field(default_factory=list)
    http: HttpSettings = Field(default_factory=HttpSettings)
    swissdevjobs: SwissDevJobsSettings = Field(default_factory=SwissDevJobsSettings)
    iamexpat: IamExpatSettings = Field(default_factory=IamExpatSettings)
    ats: AtsSettings = Field(default_factory=AtsSettings)

    @property
    def active_codes(self) -> list[str]:
        """Países activos en mayúsculas. Vacío = todos."""
        return [c.upper() for c in self.active_countries]

    def queries_for(self, day: date) -> list[str]:
        """Búsquedas de ese día: las fijas más el turno de las rotativas."""
        pool = self.rotating_queries
        n = min(self.rotating_per_day, len(pool))
        start = day.toordinal() * n
        return [*self.queries, *(pool[(start + i) % len(pool)] for i in range(n))]


class Company(BaseModel):
    name: str
    ats: Literal[
        "greenhouse",
        "lever",
        "ashby",
        "smartrecruiters",
        "workday",
        "recruitee",
        "personio",
        "successfactors",
        "brassring",
        "matador",
        "rss",
    ]
    token: str
    country: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class TitleRules(BaseModel):
    exclude_words: list[str]
    neutralize: list[str] = Field(default_factory=list)
    include_any: list[str] = Field(default_factory=list)


class DeHighLevel(BaseModel):
    patterns: list[str] = Field(default_factory=list)
    nice_guard: str | None = None
    guard_window_chars: int = 60


class DescriptionRules(BaseModel):
    exclude_langs: list[str] = Field(default_factory=list)
    de_high_level: DeHighLevel = Field(default_factory=DeHighLevel)
    detect_langs: list[str] = Field(default_factory=lambda: ["en", "de", "fr", "nl", "it"])
    condensed_max_chars: int = 2000
    snippet_context_chars: int = 150


class Evidence(BaseModel):
    lang: list[str]
    residency: list[str] = Field(default_factory=list)


class CondenseRules(BaseModel):
    priority: list[str]
    secondary: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)
    drop_sentences: list[str] = Field(default_factory=list)


class Prefilter(BaseModel):
    title: TitleRules
    description: DescriptionRules = Field(default_factory=DescriptionRules)
    evidence: Evidence
    condense: CondenseRules


class LocationPrior(BaseModel):
    name: str
    pattern: str
    penalty: float


class HardExclusions(BaseModel):
    lang_above_profile: bool = True
    residency: bool = True
    seniority_junior: bool = True
    security_clearance: bool = True
    security_clearance_pattern: str | None = None
    security_clearance_negation: str | None = None
    security_clearance_negation_window: int = 40


class Penalties(BaseModel):
    # Penalización por idioma "nice to have", por idioma (clave "default" para el resto).
    lang_nice: dict[str, float] = Field(default_factory=lambda: {"default": 8})
    lang_required_within_level: float = 6
    desc_lang: dict[str, float] = Field(default_factory=dict)
    onsite_only: float = 5
    red_flag: float = 4
    red_flag_max: float = 12
    red_flag_ignore: str | None = None
    location_priors: list[LocationPrior] = Field(default_factory=list)


class AdjacentRole(BaseModel):
    pattern: str = ""
    min_stack: int = 2
    points: float = -5
    fallback_role: str = "other"


class Bonus(BaseModel):
    stack_match_per_item: float = 4
    stack_match_max: float = 20
    seniority: dict[str, float] = Field(default_factory=dict)
    role_family: dict[str, float] = Field(default_factory=dict)
    country: dict[str, float] = Field(default_factory=dict)
    # Idioma a favor -> puntos por nivel (required/nice).
    lang: dict[str, dict[str, float]] = Field(default_factory=dict)
    adjacent_role: AdjacentRole = Field(default_factory=AdjacentRole)


class ScoringConfig(BaseModel):
    base: float = 60
    hard_exclusions: HardExclusions = Field(default_factory=HardExclusions)
    missing_cefr: dict[str, str] = Field(default_factory=lambda: {"default": "B2"})
    penalties: Penalties = Field(default_factory=Penalties)
    bonus: Bonus = Field(default_factory=Bonus)
    threshold: float = 75
    evaluator_priority: list[str] = Field(default_factory=lambda: ["claude", "gemini"])


class GeminiModel(BaseModel):
    name: str
    rpm: int = 5
    rpd: int = 20
    # Gemini 3.x: thinking_level (minimal|low|medium|high). Gemini 2.5: thinking_budget (tokens).
    thinking_level: str | None = None
    thinking_budget: int | None = None


class GeminiSettings(BaseModel):
    # Cadena de modelos: cuando uno agota su cuota diaria, se pasa al siguiente.
    models: list[GeminiModel] = Field(
        default_factory=lambda: [GeminiModel(name="gemini-3.8-flash")]
    )
    max_jobs_per_run: int = 150
    temperature: float = 0
    max_retries: int = 4
    # Errores 5xx seguidos de un mismo modelo antes de saltearlo en la corrida.
    unavailable_after: int = 2
    # Si el modelo con 5xx es el último disponible: reintentos (con backoff) antes de cortar.
    last_model_retries: int = 6
    backoff_base_s: float = 15
    backoff_max_s: float = 120
    # Timeout por request. Sin él, un pedido en vuelo cuando la Mac se duerme queda colgado
    # para siempre (09/11: la corrida quedó trabada 2 h en un read de SSL).
    request_timeout_s: float = 120


class LlmConfig(BaseModel):
    prompt_version: str = "v2"
    rubric_path: str = "prompts/rubric_v2.md"
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)


class Config(BaseModel):
    root: Path
    app: AppSettings
    profile: Profile
    searches: Searches
    prefilter: Prefilter
    companies: list[Company] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)

    model_config = {"arbitrary_types_allowed": True}

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def db_path(self) -> Path:
        return self.path(self.app.db_path)

    def rubric(self) -> str:
        return self.path(self.llm.rubric_path).read_text(encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(root: Path | None = None, companies_file: Path | None = None) -> Config:
    """Lee config/*.yaml y .env. Falla temprano con un error de validación claro."""
    root = Path(root or ROOT)
    load_dotenv(root / ".env")
    cfg_dir = root / "config"
    companies = _read_yaml(companies_file or cfg_dir / "companies.yaml").get("companies") or []
    return Config(
        root=root,
        app=AppSettings(**_read_yaml(cfg_dir / "app.yaml")),
        profile=Profile(**_read_yaml(cfg_dir / "profile.yaml")),
        searches=Searches(**_read_yaml(cfg_dir / "searches.yaml")),
        prefilter=Prefilter(**_read_yaml(cfg_dir / "prefilter.yaml")),
        companies=companies,
        scoring=ScoringConfig(**_read_yaml(cfg_dir / "scoring.yaml")),
        llm=LlmConfig(**_read_yaml(cfg_dir / "llm.yaml")),
    )
