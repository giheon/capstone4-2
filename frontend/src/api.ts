import type { Run, ScenarioInput, Treatment } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map(
                (item: { msg?: string }) => item.msg ?? "입력값을 확인하세요.",
              )
              .join(" ")
          : `요청을 처리하지 못했습니다. (${response.status})`;
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
export function artifactUrl(artifact: string) {
  return `/api/artifacts/${artifact.split("/").map(encodeURIComponent).join("/")}`;
}
export function createRun(body: {
  video_id: string;
  index_id: string | null;
  scenario: ScenarioInput;
  profile: string;
}) {
  return post<Run>("/runs", body);
}
export function saveOverrides(
  id: string,
  treatments: Record<string, Treatment>,
  no_faces_confirmed: boolean,
) {
  return request<Run>(`/runs/${id}/overrides`, {
    method: "PUT",
    body: JSON.stringify({ treatments, no_faces_confirmed }),
  });
}
export function externalUrl(value?: string | null) {
  return value && /^https?:\/\//i.test(value) ? value : null;
}
