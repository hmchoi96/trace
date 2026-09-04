export const API_BASE =
  process.env.NEXT_PUBLIC_TRACE_API ?? "http://localhost:8000";

export type Health = {
  ok: boolean;
  mailbox: string;
  mailboxReady: boolean;
  anthropic: boolean;
  grok: boolean;
  apollo: boolean;
  hunter: boolean;
};

export type Template = {
  id: string;
  label: string;
  email_mode: string;
  profile_kind: string;
  description: string;
};

export type Profile = {
  id: string;
  name: string;
  productName: string;
  huntDescription: string;
  senderName: string;
  senderCompany: string;
  fromEmail: string;
  defaultTemplate: string;
  builtin: boolean;
  signOff: string;
  profile: Record<string, unknown>;
};

export type FoundOn = "LinkedIn" | "X" | "Web";
export type PersonDecision = "pending" | "yes" | "no";
export type PersonStatus =
  | "researched"
  | "approved"
  | "draft"
  | "draft_failed"
  | "sent"
  | "passed"
  | "closed"
  | "disqualified";

export type DraftStatus = "pending" | "ready" | "failed" | "superseded";

export type Draft = {
  id: string;
  templateId: string | null;
  subject: string | null;
  body: string | null;
  verdict: string | null;
  sendable: boolean;
  error: string | null;
  status: DraftStatus;
  createdAt: string | null;
};

export type AdditionalSignal = {
  source?: string;
  url?: string;
  date?: string;
  text?: string;
  [key: string]: unknown;
};

export type Person = {
  id: string;
  profileId: string;
  huntId: string | null;
  name: string;
  title: string;
  company: string;
  foundOn: FoundOn | string;
  decision: PersonDecision;
  outcome: string | null;
  status: PersonStatus;
  email: string | null;
  emailSource: string | null;
  phone: string | null;
  phoneSource: string | null;
  enrichState: string | null;
  createdAt: string | null;
  decidedAt: string | null;
  signal: { text: string; url: string; date: string; why: string };
  actorType: string;
  outreachRole: string;
  recommendedAsk: string;
  secondaryRoles: string[];
  recommendation: string;
  recommendationReason: string;
  linkedinUrl: string;
  axes: Record<string, string>;
  additionalSignals: AdditionalSignal[];
  draft: Draft | null;
  sentAt: string | null;
  sendMethod: "trace" | "self" | null;
  notes: { text: string; at: string }[];
};

export type HuntStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export type HuntEvent = {
  stage: string;
  message: string;
  at: string;
};

export type Hunt = {
  id: string;
  profileId: string;
  limit: number;
  status: HuntStatus;
  error: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  currentStage: string;
  progress: string;
  jobStatus: string | null;
  estimateSec: number;
  elapsedSec: number;
  remainingSec: number;
  progressPct: number;
  events: HuntEvent[];
  candidates: Person[];
};

export type HuntSummary = {
  id: string;
  profileId: string;
  limit: number;
  status: HuntStatus;
  currentStage: string;
  candidateCount: number;
  error: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  progress: string;
  estimateSec: number;
  elapsedSec: number;
  remainingSec: number;
  progressPct: number;
};

export type Cost = {
  profileId: string;
  totalUsd: number;
  hunts: number;
  byStage: { stage: string; usd: number }[];
  byHunt: { huntId: string; usd: number }[];
  nextHunt: { low: number; high: number };
  limits: number[];
};

export type ProfilePayload = {
  name: string;
  whatItDoes: string;
  senderName: string;
  senderCompany: string;
  senderWork: string;
  signOff: string;
  desiredOutcome: string;
  buyers: string;
  problems: string;
  goodSignals: string;
  skip: string;
  qualify: string;
  searchGuidance: string;
  productContext: string;
  fromEmail: string;
  template: string;
  searchWeb: boolean;
  searchX: boolean;
  searchLinkedin: boolean;
  preferWeb: boolean;
};

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function detailMessage(payload: unknown, fallback: string): { code: string; message: string } {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (detail && typeof detail === "object") {
      const record = detail as { code?: unknown; message?: unknown };
      return {
        code: typeof record.code === "string" ? record.code : "error",
        message: typeof record.message === "string" ? record.message : fallback,
      };
    }
    if (typeof detail === "string") return { code: "error", message: detail };
  }
  return { code: "error", message: fallback };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Cannot reach the Trace API at ${API_BASE}. Start it with: python3 -m uvicorn trace_app.api:app --port 8000`,
      "no_api",
      0,
    );
  }

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const { code, message } = detailMessage(
      payload,
      `${init?.method ?? "GET"} ${path} failed with ${response.status}.`,
    );
    throw new ApiError(message, code, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

function normalizeHunt(raw: Partial<Hunt> & Pick<Hunt, "id" | "profileId" | "limit" | "status">): Hunt {
  return {
    id: raw.id,
    profileId: raw.profileId,
    limit: raw.limit,
    status: raw.status,
    error: raw.error ?? null,
    createdAt: raw.createdAt ?? null,
    startedAt: raw.startedAt ?? null,
    finishedAt: raw.finishedAt ?? null,
    currentStage: raw.currentStage ?? "",
    progress: raw.progress ?? "",
    jobStatus: raw.jobStatus ?? null,
    estimateSec: raw.estimateSec ?? 0,
    elapsedSec: raw.elapsedSec ?? 0,
    remainingSec: raw.remainingSec ?? 0,
    progressPct: raw.progressPct ?? 0,
    events: raw.events ?? [],
    candidates: raw.candidates ?? [],
  };
}

function normalizeHuntSummary(raw: Partial<HuntSummary> & Pick<HuntSummary, "id" | "status">): HuntSummary {
  return {
    id: raw.id,
    profileId: raw.profileId ?? "",
    limit: raw.limit ?? 0,
    status: raw.status,
    currentStage: raw.currentStage ?? "",
    candidateCount: raw.candidateCount ?? 0,
    error: raw.error ?? null,
    createdAt: raw.createdAt ?? null,
    startedAt: raw.startedAt ?? null,
    finishedAt: raw.finishedAt ?? null,
    progress: raw.progress ?? "",
    estimateSec: raw.estimateSec ?? 0,
    elapsedSec: raw.elapsedSec ?? 0,
    remainingSec: raw.remainingSec ?? 0,
    progressPct: raw.progressPct ?? 0,
  };
}

export const api = {
  health: () => request<Health>("/api/health"),
  templates: () => request<Template[]>("/api/templates"),
  profiles: () => request<Profile[]>("/api/profiles"),
  createProfile: (payload: ProfilePayload) => post<Profile>("/api/profiles", payload),
  people: (profileId: string) =>
    request<Person[]>(`/api/profiles/${encodeURIComponent(profileId)}/people`),
  hunts: async (profileId: string, limit = 20) => {
    const rows = await request<Partial<HuntSummary>[]>(
      `/api/profiles/${encodeURIComponent(profileId)}/hunts?limit=${limit}`,
    );
    return rows.map((row) => normalizeHuntSummary(row as Partial<HuntSummary> & Pick<HuntSummary, "id" | "status">));
  },
  cost: (profileId: string, limit: number) =>
    request<Cost>(`/api/profiles/${encodeURIComponent(profileId)}/cost?limit=${limit}`),
  createHunt: (profileId: string, limit: number) =>
    post<{ huntId: string; jobId: string }>("/api/hunts", { profileId, limit }),
  cancelHunt: (huntId: string) =>
    post<{ huntId: string; cancelled: boolean; alreadyFinished?: boolean }>(
      `/api/hunts/${encodeURIComponent(huntId)}/cancel`,
    ),
  hunt: async (huntId: string) => {
    const raw = await request<Partial<Hunt>>(`/api/hunts/${encodeURIComponent(huntId)}`);
    return normalizeHunt(raw as Partial<Hunt> & Pick<Hunt, "id" | "profileId" | "limit" | "status">);
  },
  candidate: (id: string) => request<Person>(`/api/candidates/${encodeURIComponent(id)}`),
  decide: (id: string, decision: "yes" | "no", reason?: string) =>
    post<{ candidateId: string; decision: string; jobId: string | null }>(
      `/api/candidates/${encodeURIComponent(id)}/decision`,
      { decision, reason },
    ),
  draft: (id: string, templateId?: string) =>
    post<{ candidateId: string; draftId: string; verdict: string }>(
      `/api/candidates/${encodeURIComponent(id)}/draft`,
      { templateId },
    ),
  pullContact: (id: string) =>
    post<{
      candidateId: string;
      found: boolean;
      source: string | null;
      email: string | null;
      phone: string | null;
      reason: string | null;
    }>(`/api/candidates/${encodeURIComponent(id)}/pull-contact`, {}),
  saveContact: (id: string, contact: { email?: string; phone?: string }) =>
    request<{
      candidateId: string;
      email: string | null;
      phone: string | null;
      emailSource: string | null;
      phoneSource: string | null;
    }>(`/api/candidates/${encodeURIComponent(id)}/contact`, {
      method: "PATCH",
      body: JSON.stringify(contact),
    }),
  outcome: (id: string, outcome: "closed" | "disqualified" | null) =>
    post<{ ok: boolean }>(`/api/candidates/${encodeURIComponent(id)}/outcome`, { outcome }),
  note: (id: string, text: string) =>
    post<{ ok: boolean }>(`/api/candidates/${encodeURIComponent(id)}/notes`, { text }),
  sentMyself: (id: string) =>
    post<{ sendId: string; alreadySent: boolean }>(
      `/api/candidates/${encodeURIComponent(id)}/sent-myself`,
    ),
  editDraft: (draftId: string, edit: { subject?: string; body?: string }) =>
    request<{ ok: boolean }>(`/api/drafts/${encodeURIComponent(draftId)}`, {
      method: "PATCH",
      body: JSON.stringify(edit),
    }),
  sendDraft: (draftId: string) =>
    post<{ sendId: string; alreadySent: boolean }>(
      `/api/drafts/${encodeURIComponent(draftId)}/send`,
    ),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
