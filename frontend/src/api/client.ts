import type {
  AddCandidateRequest,
  AnswersRequest,
  AuditRequest,
  BackendConfig,
  CandidateLookup,
  CaseDetail,
  CaseSummary,
  Catalog,
  CatalogCandidate,
  ComputedExperiment,
  CreateCaseRequest,
  DatasetInfo,
  EvaluatorInfo,
  ExperimentDetail,
  ExperimentSummary,
  FactUpdateRequest,
  Geometry,
  GeometryRequest,
  GraphSpec,
  IdentityRequest,
  LabSettings,
  ProfileCheck,
  ProfileSpec,
  ProfileSummary,
  ResumeRequest,
  ReviewRequest,
  RunSummary,
  ServiceTarget,
  StartExperimentRequest,
  UploadCaseRequest,
} from "./contract";

class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

/** the server said the thing asked for does not exist (not worth a retry) */
export const isNotFound = (error: unknown): boolean => (error as { status?: number } | null)?.status === 404;

const UNREACHABLE = "서버에 연결할 수 없습니다. 개발 서버가 실행 중인지 확인하세요.";

/** the server's `detail` (a sentence, or FastAPI's list of validation problems), else a generic line */
function failure(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) return new ApiError(status, detail);
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const where = Array.isArray(first.loc) ? first.loc.filter((part) => part !== "body").join(".") : "";
    if (first.msg) return new ApiError(status, `요청 값이 올바르지 않습니다${where ? ` (${where})` : ""}: ${first.msg}`);
  }
  return new ApiError(status, `요청이 실패했습니다 (${status}).`);
}

function parse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null; // a proxy error page or an empty reply: the status code is all there is
  }
}

/** `url` is a full path such as `/api/cases` or a media link handed out by the server */
async function requestUrl<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { ...(init?.body ? { "Content-Type": "application/json" } : {}), ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }
  const body = parse(await response.text());
  if (!response.ok) throw failure(response.status, body);
  return body as T;
}

export const request = <T>(path: string, init?: RequestInit) => requestUrl<T>(`/api${path}`, init);

/** multipart upload with progress (fetch cannot report upload progress) */
export function upload<T>(path: string, form: FormData, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    xhr.upload.onload = () => onProgress?.(1);
    xhr.onerror = () => reject(new ApiError(0, UNREACHABLE));
    xhr.onabort = () => reject(new ApiError(0, "업로드를 취소했습니다."));
    xhr.onload = () => {
      const body = parse(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300 && body !== null) resolve(body as T);
      else reject(failure(xhr.status, body));
    };
    xhr.send(form);
  });
}

export const post = <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const put = <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = <T>(path: string) => request<T>(path, { method: "DELETE" });
export const seg = encodeURIComponent;

export const api = {
  config: () => request<BackendConfig>("/config"),
  listCases: () => request<CaseSummary[]>("/cases"),
  /** M2 mock: a JSON description of the chosen file (the mock never receives the video) */
  createCase: (body: CreateCaseRequest) => post<CaseDetail>("/cases", body),
  /** real backend: the video itself as multipart/form-data */
  uploadCase: (body: UploadCaseRequest, onProgress?: (fraction: number) => void) => {
    const form = new FormData();
    if (body.file && !body.sample) form.append("file", body.file, body.file.name);
    form.append("memo", body.memo);
    form.append("mode", body.mode);
    form.append("sample", body.sample ? "true" : "false");
    return upload<CaseDetail>("/cases", form, onProgress);
  },
  getCase: (id: string) => request<CaseDetail>(`/cases/${id}`),
  pauseCase: (id: string) => request<{ ok: boolean }>(`/cases/${id}/pause`, { method: "POST" }),
  resumeCase: (id: string, body: ResumeRequest = {}) => post<{ ok: boolean }>(`/cases/${id}/resume`, body),
  /** `url` is `CaseMedia.geometry`. Without a body: the case's current plan. With one: that plan, not saved (review edits, or decisions made up to locate a target). */
  geometry: (url: string, body?: GeometryRequest) => requestUrl<Geometry>(url, body ? { method: "POST", body: JSON.stringify(body) } : undefined),
  answerIdentity: (id: string, body: IdentityRequest) => post<{ ok: boolean }>(`/cases/${id}/identity`, body),
  answerQuestions: (id: string, body: AnswersRequest) => post<{ ok: boolean }>(`/cases/${id}/answers`, body),
  updateFact: (id: string, body: FactUpdateRequest) => post<{ ok: boolean }>(`/cases/${id}/facts`, body),
  submitReview: (id: string, body: ReviewRequest) => post<{ ok: boolean }>(`/cases/${id}/review`, body),
  submitAudit: (id: string, body: AuditRequest & { backToReview?: boolean }) =>
    post<{ ok: boolean }>(`/cases/${id}/audit`, body),
  reportUrl: (id: string) => `/api/cases/${id}/report.json`,

  graph: () => request<GraphSpec>("/lab/graph"),
  runs: () => request<RunSummary[]>("/lab/runs"),
  startDemo: (profileId?: string) => post<RunSummary>("/lab/runs", profileId ? { profileId } : {}),
  experiments: () => request<ExperimentSummary[]>("/lab/experiments"),
  experiment: (id: string) => request<ExperimentDetail>(`/lab/experiments/${seg(id)}`),
  startExperiment: (body: StartExperimentRequest) => post<ComputedExperiment>("/lab/experiments", body),
  datasets: () => request<{ datasets: DatasetInfo[]; evaluators: EvaluatorInfo[]; evaluatorSource?: string }>("/lab/datasets"),
  catalog: () => request<Catalog>("/lab/catalog"),
  lookupCandidate: (ref: string) => request<CandidateLookup>(`/lab/catalog/lookup?ref=${seg(ref)}`),
  addCandidate: (body: AddCandidateRequest) => post<CatalogCandidate>("/lab/catalog/candidates", body),
  removeCandidate: (slot: string, id: string) => del<{ ok: boolean }>(`/lab/catalog/candidates/${seg(slot)}/${seg(id)}`),
  profiles: () => request<ProfileSummary[]>("/lab/profiles"),
  checkProfile: (spec: Partial<ProfileSpec>) => post<ProfileCheck>("/lab/profiles/check", spec),
  createProfile: (spec: ProfileSpec) => post<ProfileSummary>("/lab/profiles", spec),
  updateProfile: (spec: ProfileSpec) => put<ProfileSummary>(`/lab/profiles/${seg(spec.id)}`, spec),
  deleteProfile: (id: string) => del<{ ok: boolean }>(`/lab/profiles/${seg(id)}`),
  promoteProfile: (id: string, target: ServiceTarget) => post<ProfileSummary>(`/lab/profiles/${seg(id)}/promote`, { target }),
  settings: () => request<LabSettings>("/lab/settings"),
  setSpeed: (speed: number) => post<LabSettings>("/lab/settings", { speed }),
};
