export type Filters = {
  source?: "live" | "demo" | null;
  employment_type?: string | null; remote_policy?: string | null; company_category?: string | null;
  location?: string | null; max_required_years?: number | null; skills?: string[]; posted_within_days?: number | null;
  min_salary?: number | null; currency?: string | null;
};
export type Profile = {
  education: string[]; skills: string[]; projects: string[]; work_experience: string[];
  tools: string[]; certifications: string[]; domains: string[]; experience_level: string | null;
  years_experience: number | null;
};
export type ProfileView = { profile: Profile; preferences: Filters };
export type Job = {
  id: string; title: string; company: string; description: string; location: string | null; employment_type: string | null;
  remote_policy: string | null; company_category: string | null; required_skills: string[]; preferred_skills: string[];
  min_years: number | null; salary_min: number | null; salary_max: number | null; currency: string | null;
  source_url: string | null; source_name: string; source_provenance?: string; synthetic: boolean; active: boolean; posted_at: string | null;
  expires_at: string | null;
};
export type Match = {
  job: Job; score: number | null; evidence: string; explanation: string;
  matched_skills: string[]; missing_skills: string[]; eligibility_warnings: string[];
};
export type Results = { items: Match[]; filters: Filters; ranking_version: string; reranked: boolean; embedding_model?: string | null };
export type Catalog = { items: Job[]; total: number; page: number; page_size: number;
  sources: { name: string; synthetic: boolean; provenance: string; total: number }[];
};
export type Turn = {
  role: "assistant" | "user"; message: string; conversation_id?: string;
  model_name?: string | null; model_usage?: { model_failures?: number };
  recommendations?: Match[]; jobs?: Job[]; trace_id?: string | null;
  trajectory?: { name: string; status: string }[];
  source_checks?: { name: string; status: string; fetched_count: number }[];
  external_links?: { label: string; url: string }[];
};
export type Account = { id: string; email: string; admin: boolean };
export type Conversation = { id: string; title: string; updated_at: string };
export type Draft = { id: string; draft: Profile; confirmed: boolean; extraction_model?: string | null };
export type ResumeExtractor = { provider: string; model: string | null; configured: boolean };
export type Metric = number | null | string;
export type EvalRun = {
  id: string; dataset: string; status: string; metrics: Record<string, Metric | Record<string, Metric>>;
  configuration: { label: string; model?: string; embedding_model?: string; prompt_label?: string };
  created_at: string; finished_at: string | null;
};

export async function request<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", "Bearer " + token);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch("/api/careerlens" + path, { ...options, headers, cache: "no-store" });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(typeof problem.detail === "string" ? problem.detail : "Request failed (" + response.status + ")");
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
