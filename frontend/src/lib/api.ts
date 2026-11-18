import type {
  ConfigFile,
  CvPreview,
  DashboardData,
  ExportFile,
  JobDetail,
  JobsResponse,
  RunnerSnapshot,
  CvLanguage,
  SettingsData,
  UsageData,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const body = init?.body;
  const hasJsonBody = Boolean(body) && !(body instanceof FormData);
  const response = await fetch(path, {
    ...init,
    headers: hasJsonBody
      ? { "Content-Type": "application/json", ...(init?.headers || {}) }
      : init?.headers,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail || message;
    } catch {
      // Keep the HTTP status text when an upstream error is not JSON.
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

function params(values: object): string {
  const output = new URLSearchParams();
  for (const [key, value] of Object.entries(values as Record<string, unknown>)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((item) => output.append(key, String(item)));
    else output.set(key, String(value));
  }
  const result = output.toString();
  return result ? `?${result}` : "";
}

export interface JobsQuery {
  page?: number;
  page_size?: number;
  q?: string;
  country?: string[];
  status?: string[];
  decision?: string;
  min_score?: number;
  max_score?: number;
  kind?: "shortlist" | "internship" | "ranking";
  sort?: string;
  direction?: "asc" | "desc";
}

export const api = {
  dashboard: () => request<DashboardData>("/api/dashboard"),
  jobs: (query: JobsQuery) => request<JobsResponse>(`/api/jobs${params(query)}`),
  job: (id: string) => request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`),
  decide: (id: string, decision: "applied" | "discarded" | null, note?: string) =>
    request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/decision`, {
      method: "PUT",
      body: JSON.stringify({ decision, note: note || null }),
    }),
  usage: (days = 30) => request<UsageData>(`/api/analytics/usage?days=${days}`),
  activity: (days = 30) =>
    request<Array<{ day: string; jobs: number }>>(`/api/analytics/activity?days=${days}`),
  runs: (limit = 30) =>
    request<{ items: Record<string, unknown>[]; runner: RunnerSnapshot }>(
      `/api/runs?limit=${limit}`,
    ),
  logs: (lines = 100) =>
    request<{ items: Array<{ ts?: string; level: string; logger?: string; msg: string }> }>(
      `/api/logs?lines=${lines}`,
    ),
  exports: () => request<{ items: ExportFile[] }>("/api/exports"),
  settings: () => request<SettingsData>("/api/settings"),
  configFile: (name: string) =>
    request<ConfigFile>(`/api/settings/config/${encodeURIComponent(name)}`),
  saveConfigFile: (name: string, content: string) =>
    request<ConfigFile>(`/api/settings/config/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  validateConfigFile: (name: string, content: string) =>
    request<{ valid: boolean; message: string }>(
      `/api/settings/config/${encodeURIComponent(name)}/validate`,
      { method: "POST", body: JSON.stringify({ content }) },
    ),
  operation: <T extends object>(name: string, body?: T) =>
    request<RunnerSnapshot>(`/api/operations/${name}`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  operationStatus: () => request<RunnerSnapshot>("/api/operations/status"),
  previewCv: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<CvPreview>("/api/profile/cv/preview", { method: "POST", body });
  },
  generateCvProfile: (token: string, consent: boolean) =>
    request<RunnerSnapshot>("/api/profile/cv/generate", {
      method: "POST",
      body: JSON.stringify({ token, consent }),
    }),
  applyCvProfile: (body: {
    summary: string;
    current_title: string | null;
    years_experience: number | null;
    domains: string[];
    languages: CvLanguage[];
    residency_countries: string[];
    stack: string[];
  }) =>
    request<{ rescored: number; pending: number; profile: ConfigFile; scoring: ConfigFile }>(
      "/api/profile/cv/apply",
      { method: "POST", body: JSON.stringify(body) },
    ),
};
