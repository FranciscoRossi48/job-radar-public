export type JobStatus =
  | "pending"
  | "evaluated"
  | "excluded_prefilter"
  | "duplicate"
  | "stale";

export type Decision = "applied" | "discarded" | null;

export interface EvaluationFeatures {
  lang_required?: Array<{ lang: string; level: string; cefr?: string | null }>;
  residency_hard_req?: boolean;
  residency_country?: string | null;
  onsite_only?: boolean;
  seniority?: string;
  role_family?: string;
  stack_match?: string[];
  red_flags?: string[];
  [key: string]: unknown;
}

export interface ScoreBreakdown {
  rule?: string;
  points?: number;
  exclude?: string;
}

export interface Evaluation {
  id: number;
  job_id: string;
  evaluator: string;
  model: string | null;
  prompt_version: string;
  created_at: string;
  features: EvaluationFeatures;
  rationale: string | null;
  score_computed: number | null;
  score_breakdown: ScoreBreakdown[];
}

export interface JobSummary {
  id: string;
  source: string;
  url: string;
  title: string;
  company: string | null;
  location: string | null;
  country: string | null;
  posted_at: string | null;
  first_seen: string;
  last_seen: string;
  status: JobStatus;
  exclude_reason: string | null;
  duplicate_of: string | null;
  decision: Decision;
  decided_at: string | null;
  decision_note: string | null;
  score: number | null;
  evaluator: string | null;
  model: string | null;
  rationale: string | null;
  features: EvaluationFeatures;
  fit_excluded: boolean;
  fit_exclusion_reasons: string[];
}

export interface JobDetail extends Omit<JobSummary, "score" | "evaluator" | "model" | "rationale" | "features" | "fit_excluded" | "fit_exclusion_reasons"> {
  description_raw: string | null;
  desc_condensed: string | null;
  desc_lang: string | null;
  lang_snippets: Array<{ kind: string; match?: string; text: string }>;
  claimed_at: string | null;
  claimed_by: string | null;
  exported_at: string | null;
  evaluations: Evaluation[];
  current_evaluation: Evaluation | null;
  duplicate?: { id: string; title: string; company: string | null } | null;
}

export interface Facet {
  value: string;
  count: number;
}

export interface JobsResponse {
  items: JobSummary[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  facets: { countries: Facet[]; statuses: Facet[] };
}

export interface DashboardData {
  metrics: {
    total: number;
    new_today: number;
    pending: number;
    evaluated: number;
    shortlisted: number;
    applied: number;
    threshold: number;
  };
  pipeline: Array<{ label: string; value: number }>;
  status: Array<{ status: string; count: number }>;
  countries: Array<{ country: string; total: number; pending: number }>;
  sources: Array<{ source: string; total: number }>;
  score_buckets: Array<{ label: string; count: number }>;
  activity: Array<{ day: string; jobs: number }>;
  recent: JobSummary[];
  top_matches: JobSummary[];
  runner: RunnerSnapshot;
}

export interface RunnerSnapshot {
  id: string | null;
  running: boolean;
  task: string | null;
  started_at: string | null;
  finished_at: string | null;
  result: unknown;
  error: string | null;
  log: string[];
  history: Array<Omit<RunnerSnapshot, "history">>;
}

export interface UsageData {
  days: number;
  rows: Array<{
    day: string;
    provider: string;
    model: string;
    requests: number;
    ok: number;
    tokens_in: number;
    tokens_out: number;
  }>;
  totals: { requests: number; ok: number; tokens_in: number; tokens_out: number };
}

export interface ExportFile {
  name: string;
  size: number;
  rows: number;
  modified_at: string;
}

export interface SettingsData {
  profile: {
    summary: string;
    current_title: string | null;
    years_experience: number | null;
    domains: string[];
    languages: Record<string, string>;
    residency_countries: string[];
    stack: string[];
  };
  search: {
    active_countries: string[];
    available_countries: string[];
    queries: string[];
    rotating_queries: string[];
    sources: string[];
    hours_old: number;
  };
  scoring: { threshold: number; base: number; evaluator_priority: string[]; no_cv_stack_match: boolean };
  llm: { provider: string; models: string[]; key_name: string; key_configured: boolean };
  paths: { root: string; database: string; exports: string; logs: string };
  database: { path: string; exists: boolean; size: number; modified_at: string | null };
  config_files: string[];
}

export interface ConfigFile {
  name: string;
  content: string;
  modified_at: string;
  saved?: boolean;
  backup?: string | null;
}

export interface CvPreview {
  token: string;
  filename: string;
  kind: string;
  characters: number;
  pages: number | null;
  preview: string;
  expires_in_minutes: number;
}

export interface CvLanguage {
  code: string;
  level: "A1" | "A2" | "B1" | "B2" | "C1" | "C2";
  evidence: string;
}

export interface ProfileDraft {
  summary: string;
  languages: CvLanguage[];
  residency_countries: string[];
  stack: string[];
  current_title: string | null;
  years_experience: number | null;
  domains: string[];
  warnings: string[];
}

export interface CvProfileResult {
  draft: ProfileDraft;
  model: string;
  provider: string;
  source: { filename: string; kind: string; characters_sent: number };
}
