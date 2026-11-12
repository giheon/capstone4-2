import type { CaseStatus, Method, RunWaiting, Shape } from "../api/contract";

/** 72.4 → "01:12.4" (the player's clock, tenths of a second) */
export function timecode(sec: number): string {
  const tenths = Math.round(Math.max(0, sec) * 10);
  return `${String(Math.floor(tenths / 600)).padStart(2, "0")}:${((tenths % 600) / 10).toFixed(1).padStart(4, "0")}`;
}

/**
 * A boundary of a time range: tenths when that is exact, otherwise as many digits as the value has, up to
 * milliseconds. Ranges are aligned to frames, so 9.958 must not read as "10.0" or lose its tail as "9.9".
 */
export function rangeTime(sec: number): string {
  const ms = Math.round(Math.max(0, sec) * 1000);
  const digits = ms % 100 === 0 ? 1 : ms % 10 === 0 ? 2 : 3;
  return `${String(Math.floor(ms / 60_000)).padStart(2, "0")}:${((ms % 60_000) / 1000).toFixed(digits).padStart(digits + 3, "0")}`;
}

/** [5, 9.958] → "00:05.0–00:09.958" */
export function rangeText([start, end]: [number, number]): string {
  return `${rangeTime(start)}–${rangeTime(end)}`;
}

/** 83_400 ms → "1:23" */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** short duration for task bars and node badges */
export function duration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  return clock(ms);
}

export function seconds(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  return m ? `${m}분 ${s}초` : `${s}초`;
}

export function bytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

export function usd(n: number, digits = 2): string {
  return `$${n.toFixed(digits)}`;
}

export function percent(n: number, digits = 0): string {
  return `${(n * 100).toFixed(digits)}%`;
}

export function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "방금 전";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}시간 전`;
  return new Date(iso).toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

export const METHOD_LABEL: Record<Method, string> = {
  keep: "유지",
  mosaic: "모자이크",
  remove: "삭제",
};

/** a plan stored before the methods were reduced to these may name another one: its stored word is shown as it is */
export function methodLabel(method: string): string {
  return (METHOD_LABEL as Record<string, string | undefined>)[method] ?? method;
}

export const SHAPE_LABEL: Record<Shape, string> = {
  ellipse: "타원",
  box: "사각형",
  outline: "윤곽",
};

export const STATUS_LABEL: Record<CaseStatus, string> = {
  QUEUED: "대기 중",
  ANALYZING: "영상 분석 중",
  AWAITING_IDENTITY: "대상 확인 필요",
  ANALYZING_LEGAL: "법적 근거 분석 중",
  AWAITING_ANSWERS: "질문 답변 필요",
  PLANNING: "처리 계획 수립 중",
  AWAITING_REVIEW: "검토 필요",
  AUDITING: "재검증 중",
  AWAITING_AUDIT_CONFIRM: "감사 권고 확인 필요",
  RENDERING: "출력 영상 만드는 중",
  COMPLETED: "완료",
  FAILED: "실패",
  PAUSED: "멈춤",
};

/** the statuses a run stands still in, and what it stands still for */
export const WAITING_FOR: Partial<Record<CaseStatus, RunWaiting>> = {
  AWAITING_IDENTITY: "identity",
  AWAITING_ANSWERS: "questions",
  AWAITING_REVIEW: "review",
  AWAITING_AUDIT_CONFIRM: "audit",
  PAUSED: "paused",
};

/** a run that is working on its own: not waiting for the user, not paused, not finished */
export function isRunning(status: CaseStatus): boolean {
  return status === "QUEUED" || status === "ANALYZING" || status === "ANALYZING_LEGAL" || status === "PLANNING" || status === "AUDITING" || status === "RENDERING";
}

export function needsUser(status: CaseStatus): boolean {
  return status === "AWAITING_IDENTITY" || status === "AWAITING_ANSWERS" || status === "AWAITING_REVIEW" || status === "AWAITING_AUDIT_CONFIRM";
}

/** is `t` inside one of the half-open ranges `[start, end)` */
export function inRanges(t: number, ranges: [number, number][]): boolean {
  return ranges.some(([s, e]) => t >= s && t < e);
}
