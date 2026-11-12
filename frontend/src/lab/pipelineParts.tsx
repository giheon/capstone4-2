// Small pieces shared by the profile, catalog and experiment screens.
import { Check, CircleX, Cloud, Cpu, Info, SlidersHorizontal, SquareCode, TriangleAlert, X } from "lucide-react";
import type { AdapterKind, Capability, Catalog, ProfileIssue, ProfileSummary, RunComponent, ServiceTarget } from "../api/contract";
import { cx } from "../ui";

export const KIND_LABEL: Record<AdapterKind, string> = { api: "외부 API", local: "로컬", builtin: "코드", option: "설정" };
const KIND_ICON = { api: Cloud, local: Cpu, builtin: SquareCode, option: SlidersHorizontal } as const;

export function KindBadge({ kind }: { kind: AdapterKind }) {
  const Icon = KIND_ICON[kind];
  return (
    <span className={cx("kind-badge", `kind-${kind}`)}>
      <Icon size={12} aria-hidden="true" />
      {KIND_LABEL[kind]}
    </span>
  );
}

export const CAP_LABEL: Record<Capability, string> = { tools: "도구 호출", json: "JSON 스키마 출력", image: "이미지 입력", video: "영상 입력" };

/** whether the videos of a dataset may be sent to cloud models */
export function SendTag({ sendable }: { sendable: boolean }) {
  return <span className={cx("send-tag", sendable ? "is-open" : "is-closed")}>{sendable ? "외부 전송 허용" : "외부 전송 금지"}</span>;
}

export function priceText(price?: { in: number; out: number }): string {
  if (!price) return "—";
  const f = (v: number) => (v === 0 ? "0" : v < 0.1 ? v.toFixed(3).replace(/0+$/, "") : String(Number(v.toFixed(3))));
  return `$${f(price.in)} / $${f(price.out)}`;
}

const ISSUE_ICON = { error: CircleX, warn: TriangleAlert, info: Info } as const;
const ISSUE_LABEL = { error: "오류", warn: "주의", info: "참고" } as const;

export function IssueList({ issues, empty }: { issues: ProfileIssue[]; empty?: string }) {
  if (issues.length === 0) return empty ? <p className="issue-empty">{empty}</p> : null;
  const order = { error: 0, warn: 1, info: 2 } as const;
  return (
    <ul className="issue-list">
      {[...issues]
        .sort((a, b) => order[a.level] - order[b.level])
        .map((issue, i) => {
          const Icon = ISSUE_ICON[issue.level];
          return (
            <li key={`${issue.slot ?? ""}-${i}`} className={cx("issue", `issue-${issue.level}`)}>
              <Icon size={14} aria-hidden="true" />
              <span className="sr-only">{ISSUE_LABEL[issue.level]}: </span>
              {issue.text}
            </li>
          );
        })}
    </ul>
  );
}

const GATE_LABEL = { passed: "게이트 통과", failed: "게이트 미달", not_run: "평가 전" } as const;

export function GateChip({ gate }: { gate: ProfileSummary["gate"] }) {
  return (
    <span className={cx("gate-chip", `gate-${gate}`)}>
      {gate === "passed" ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : gate === "failed" ? <X size={12} strokeWidth={3} aria-hidden="true" /> : null}
      {GATE_LABEL[gate]}
    </span>
  );
}

const SERVICE_LABEL: Record<ServiceTarget, string> = { default: "서비스 기본", local_only: "서비스 로컬 처리" };

export function ServiceChips({ service }: { service: ServiceTarget[] }) {
  return (
    <>
      {service.map((s) => (
        <span key={s} className="tag tag-promoted">
          {SERVICE_LABEL[s]}
        </span>
      ))}
    </>
  );
}

/** stage label lookup for components that carry only the stage id */
export function stageLabels(catalog?: Catalog): Record<string, string> {
  const fallback: Record<string, string> = { perception: "지각", scene: "장면 이해 (VLM)", retrieval: "법률 검색 (RAG)", agents: "에이전트 · LLM", render: "렌더" };
  return { ...fallback, ...Object.fromEntries((catalog?.stages ?? []).map((s) => [s.id, s.label])) };
}

export function groupByStage(components: RunComponent[]): [string, RunComponent[]][] {
  const groups = new Map<string, RunComponent[]>();
  for (const c of components) groups.set(c.stage, [...(groups.get(c.stage) ?? []), c]);
  return [...groups.entries()];
}
