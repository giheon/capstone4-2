// Pieces shared by the section-experiment screens: number formats, the range marks, status marks, and how a
// score is written.
import { Ban, Check, CircleDashed, CircleX, Loader2, MessageCircleQuestion, Pause } from "lucide-react";
import type { PipelineSection, RangeInteraction, ScoreDirection, StageRunStatus, StageStatus, WaitingFor } from "../api/stage";
import { percent } from "../lib/format";
import { cx } from "../ui";
import { WAITING_LABEL } from "./useRunView";

/* ---------- formats ---------- */

/** 0.38 → "0.38초" · 12.3 → "12.3초" · 83 → "1분 23초" */
export function secs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 60) {
    const m = Math.floor(value / 60);
    return `${m}분 ${Math.round(value - m * 60)}초`;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)}초`;
}

/** run costs are fractions of a cent: keep the digits that differ */
export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "$0";
  return `$${value.toFixed(value < 0.01 ? 4 : value < 1 ? 3 : 2)}`;
}

export function share(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : percent(value);
}

/* ---------- sections and ranges ---------- */

export function rangeOf(sections: PipelineSection[], from: string, to: string): PipelineSection[] {
  const a = sections.findIndex((s) => s.id === from);
  const b = sections.findIndex((s) => s.id === to);
  return a < 0 || b < a ? [] : sections.slice(a, b + 1);
}

/** a section's label; its id while the sections are not loaded or do not have it */
export function sectionLabel(sections: PipelineSection[] | undefined, id: string | null): string {
  return sections?.find((s) => s.id === id)?.label ?? id ?? "";
}

export function rangeLabel(sections: PipelineSection[], from: string, to: string): string {
  const label = (id: string) => sectionLabel(sections, id);
  return from === to ? label(from) : `${label(from)} → ${label(to)}`;
}

/** the range as one mark per section, in pipeline order: what a list row or a header needs of the rail */
export function RangeGlyph({ sections, from, to }: { sections: PipelineSection[]; from: string; to: string }) {
  const a = sections.findIndex((s) => s.id === from);
  const b = sections.findIndex((s) => s.id === to);
  return (
    <span className="range-glyph" role="img" aria-label={`구간: ${rangeLabel(sections, from, to)}`}>
      {sections.map((s, i) => (
        <span key={s.id} className={cx("range-mark", i < a && "is-before", i >= a && i <= b && "is-in")} title={s.label} />
      ))}
    </span>
  );
}

/* ---------- variants ---------- */

/** a combination's name without the "기준 · " the server puts in front of the base profile's */
export function variantName(variant: { base: boolean; label: string }): string {
  return variant.base ? variant.label.replace(/^기준\s*·\s*/, "") : variant.label;
}

/** the name of a combination wherever one is listed: the base profile's is marked and set in the profile's own type */
export function VariantName({ variant }: { variant: { base: boolean; label: string } }) {
  return (
    <span className="sx-variant">
      {variant.base ? <span className="tag tag-base">기준</span> : null}
      <span className={cx("sx-variant-name", variant.base && "mono")}>{variantName(variant)}</span>
    </span>
  );
}

/* ---------- status ---------- */

/** an experiment is a set that keeps growing: it is never "finished", only busy, waiting for someone, or neither */
export function ExperimentStatus({ status, waiting = 0, total }: { status: StageStatus; waiting?: number; total?: number }) {
  const word = status === "running" ? "실행 중" : status === "waiting" ? `응답 대기 ${waiting || ""}`.trim() : total === 0 ? "실행 없음" : "모두 끝남";
  return (
    <span className={cx("sx-status", `is-${status}`)}>
      {status === "running" ? <span className="pulse is-live" aria-hidden="true" /> : null}
      {status === "waiting" ? <MessageCircleQuestion size={13} aria-hidden="true" /> : null}
      {word}
    </span>
  );
}

const RUN_STATUS: Record<StageRunStatus, string> = { queued: "대기", running: "실행 중", waiting: "응답 대기", completed: "완료", failed: "실패", skipped: "건너뜀" };
const RUN_ICON = { queued: CircleDashed, running: Loader2, waiting: MessageCircleQuestion, completed: Check, failed: CircleX, skipped: Ban } as const;

/** a word and a shape for every state, so none is told by colour alone. A waiting run says what it waits for. */
export function RunStatusMark({ status, waitingFor }: { status: StageRunStatus; waitingFor?: WaitingFor | null }) {
  const Icon = status === "waiting" && waitingFor === "paused" ? Pause : RUN_ICON[status];
  return (
    <span className={cx("sx-run-status", `is-${status}`)}>
      <Icon size={13} strokeWidth={status === "completed" ? 3 : 2} aria-hidden="true" />
      {status === "waiting" && waitingFor ? WAITING_LABEL[waitingFor] : RUN_STATUS[status]}
    </span>
  );
}

export function ProgressBar({ done, total, label }: { done: number; total: number; label: string }) {
  const ratio = total > 0 ? Math.min(1, done / total) : 0;
  return (
    <span className="sx-progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label={label}>
      <span className="sx-progress-fill" style={{ width: `${ratio * 100}%` }} />
    </span>
  );
}

/* ---------- interactions ---------- */

/** "법률 분석: 같은 사람 확인 · 질문 / 검토 · 감사: 계획 검토 · 감사 확인" */
export function interactionText(sections: PipelineSection[], interactions: RangeInteraction[]): string {
  const bySection = new Map<string, string[]>();
  for (const i of interactions) bySection.set(i.section, [...(bySection.get(i.section) ?? []), i.label]);
  return [...bySection].map(([id, labels]) => `${sectionLabel(sections, id)}: ${labels.join(" · ")}`).join(" / ");
}

/** the mark of a range, or a run, that asks the user and is answered by a person */
export function AsksMark({ title }: { title?: string }) {
  return (
    <span className="tag sx-asks" title={title}>
      <MessageCircleQuestion size={12} aria-hidden="true" />
      직접 응답
    </span>
  );
}

/* ---------- scores: drawn from what a score says about itself, never from its name ---------- */

/** a value in its unit: "ratio" is a share of 1, "sec" and "usd" are formatted as times and costs, anything else is the number and its unit */
export function scoreText(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined) return "—";
  if (unit === "ratio") return `${Number((value * 100).toFixed(1))}%`;
  if (unit === "sec") return secs(value);
  if (unit === "usd") return money(value);
  const number = Number(value.toFixed(2)).toLocaleString();
  return unit && unit !== "count" ? `${number} ${unit}` : number;
}

export function directionText(direction: ScoreDirection): string {
  return direction === "higher" ? "높을수록 좋음" : direction === "lower" ? "낮을수록 좋음" : "";
}
