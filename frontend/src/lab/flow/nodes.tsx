import { Fragment, memo } from "react";
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import {
  BrainCircuit,
  Check,
  Clock3,
  Database,
  ListChecks,
  Loader2,
  Maximize2,
  ShieldCheck,
  User,
  Wrench,
  X,
} from "lucide-react";
import type { BudgetKey, GraphNodeSpec } from "../../api/contract";
import type { Budget, NodeRun, NodeStatus } from "../../lib/runState";
import { clock, duration, usd } from "../../lib/format";
import { cx } from "../../ui";

interface TopoData extends Record<string, unknown> {
  spec: GraphNodeSpec;
  run?: NodeRun;
  now: number;
  caption?: string | null;
  budgets?: Partial<Record<BudgetKey, Budget>>;
  inner?: { id: string; label: string; status: NodeStatus }[];
  calls?: number;
  validation?: { ok: boolean; count: number } | null;
  onDrill?: () => void;
  dim?: boolean;
}

export type TopoNode = Node<TopoData>;

const SIDES = [
  ["l", Position.Left],
  ["r", Position.Right],
  ["t", Position.Top],
  ["b", Position.Bottom],
] as const;

function Handles() {
  return (
    <>
      {SIDES.map(([id, position]) => (
        <Fragment key={id}>
          <Handle type="source" id={`s-${id}`} position={position} isConnectable={false} className="tn-handle" />
          <Handle type="target" id={`t-${id}`} position={position} isConnectable={false} className="tn-handle" />
        </Fragment>
      ))}
    </>
  );
}

const STATUS_TEXT: Record<NodeStatus, string> = {
  idle: "대기",
  running: "실행 중",
  waiting: "응답 대기",
  done: "완료",
  cached: "캐시",
  error: "오류",
};

export function StatusIcon({ status, size = 13 }: { status: NodeStatus; size?: number }) {
  const label = STATUS_TEXT[status];
  switch (status) {
    case "running":
      return <Loader2 size={size} className="ti ti-spin" aria-label={label} />;
    case "waiting":
      return <Clock3 size={size} className="ti" aria-label={label} />;
    case "done":
      return <Check size={size} strokeWidth={3} className="ti" aria-label={label} />;
    case "cached":
      return <Database size={size} className="ti" aria-label={label} />;
    case "error":
      return <X size={size} strokeWidth={3} className="ti" aria-label={label} />;
    default:
      return <span className="ti ti-idle" aria-label={label} />;
  }
}

function timing(run: NodeRun | undefined, now: number): string | null {
  if (!run || run.status === "idle") return null;
  if ((run.status === "running" || run.status === "waiting") && run.startedAt !== undefined) return clock(now - run.startedAt);
  if (run.runs > 1) return `${duration(run.totalMs)} · ${run.runs}회`;
  return duration(run.totalMs);
}

function statusOf(run?: NodeRun): NodeStatus {
  return run?.status ?? "idle";
}

const StepNode = memo(function StepNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  const t = timing(data.run, data.now);
  return (
    <div className={cx("tn tn-step", `st-${status}`, selected && "is-selected", data.dim && "is-dim")}>
      <Handles />
      <div className="tn-row">
        <StatusIcon status={status} />
        <span className="tn-label">{data.spec.label}</span>
      </div>
      <div className="tn-sub">{data.spec.sublabel}</div>
      <div className="tn-meta num">
        {t ?? STATUS_TEXT[status]}
        {data.run && data.run.costUsd > 0 ? <span> · {usd(data.run.costUsd, 3)}</span> : null}
      </div>
    </div>
  );
});

const HumanNode = memo(function HumanNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  const t = timing(data.run, data.now);
  return (
    <div className={cx("tn tn-human", `st-${status}`, selected && "is-selected", data.dim && "is-dim")}>
      <Handles />
      <div className="tn-row">
        <User size={13} className="ti ti-human" aria-hidden="true" />
        <span className="tn-label">{data.spec.label}</span>
        <StatusIcon status={status} size={12} />
      </div>
      <div className="tn-meta num">{status === "waiting" ? `응답 대기 ${t}` : status === "idle" ? data.spec.sublabel : `${STATUS_TEXT[status]} · ${t}`}</div>
    </div>
  );
});

const ToolNode = memo(function ToolNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  return (
    <div className={cx("tn tn-tool", `st-${status}`, selected && "is-selected", data.dim && "is-dim")} title={data.spec.sublabel}>
      <Handles />
      <Wrench size={12} className="ti" aria-hidden="true" />
      <span className="tn-label mono">{data.spec.label}</span>
      <span className="tn-count num">{data.calls ? `×${data.calls}` : ""}</span>
      {status === "running" ? <Loader2 size={12} className="ti ti-spin" aria-label="호출 중" /> : null}
    </div>
  );
});

const BUDGET_LABEL: Partial<Record<BudgetKey, string>> = {
  tool_calls: "도구",
  searches: "검색",
  question_rounds: "질문",
  retries: "재시도",
};

function BudgetBars({ budgets }: { budgets?: Partial<Record<BudgetKey, Budget>> }) {
  if (!budgets) return null;
  const entries = (Object.keys(BUDGET_LABEL) as BudgetKey[]).filter((k) => budgets[k]);
  if (!entries.length) return null;
  return (
    <div className="tn-budgets">
      {entries.map((k) => {
        const b = budgets[k] as Budget;
        const ratio = b.limit ? b.used / b.limit : 0;
        return (
          <div key={k} className="tn-budget" title={`${BUDGET_LABEL[k]} ${b.used}/${b.limit}`}>
            <span className="tn-budget-label">{BUDGET_LABEL[k]}</span>
            <span className="tn-budget-bar">
              <span style={{ width: `${Math.min(1, ratio) * 100}%` }} className={cx(ratio >= 0.8 && "is-high")} />
            </span>
            <span className="num">
              {b.used}/{b.limit}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const ModelNode = memo(function ModelNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  return (
    <div className={cx("tn tn-model", `st-${status}`, selected && "is-selected", data.dim && "is-dim")}>
      <Handles />
      <div className="tn-row">
        <BrainCircuit size={14} className="ti ti-model" aria-hidden="true" />
        <span className="tn-label">{data.spec.label}</span>
        <StatusIcon status={status} size={12} />
      </div>
      <div className="tn-sub">{data.spec.sublabel}</div>
      <div className="tn-caption">{data.caption ?? (data.run ? `턴 ${data.run.runs}회` : "대기")}</div>
    </div>
  );
});

const ValidatorNode = memo(function ValidatorNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  const v = data.validation;
  return (
    <div className={cx("tn tn-validator", `st-${status}`, v && !v.ok && "is-failed", selected && "is-selected", data.dim && "is-dim")}>
      <Handles />
      <div className="tn-row">
        <ShieldCheck size={13} className="ti" aria-hidden="true" />
        <span className="tn-label">{data.spec.label}</span>
      </div>
      <div className="tn-meta">{v ? (v.ok ? `통과 · 검사 ${v.count}회` : `되먹임 · 검사 ${v.count}회`) : data.spec.sublabel}</div>
    </div>
  );
});

const AgentNode = memo(function AgentNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  return (
    <div className={cx("tn-group", `st-${status}`, selected && "is-selected")} data-agent={data.spec.id}>
      <Handles />
      <div className="tn-group-head">
        <span className="tn-group-kind">에이전트</span>
        <span className="tn-group-label">{data.spec.label}</span>
        <span className="tn-group-model">{data.spec.model}</span>
        <span className="tn-group-status">
          <StatusIcon status={status} size={12} /> {STATUS_TEXT[status]}
          {data.run && data.run.runs > 1 ? ` · ${data.run.runs}회` : ""}
        </span>
      </div>
      <BudgetBars budgets={data.budgets} />
    </div>
  );
});

const SubgraphNode = memo(function SubgraphNode({ data, selected }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  const inner = data.inner ?? [];
  const done = inner.filter((n) => n.status === "done" || n.status === "cached").length;
  return (
    <div className={cx("tn tn-subgraph", `st-${status}`, selected && "is-selected", data.dim && "is-dim")}>
      <Handles />
      <div className="tn-row">
        <ListChecks size={14} className="ti" aria-hidden="true" />
        <span className="tn-label">{data.spec.label}</span>
        <span className="tn-sub-inline">서브그래프</span>
      </div>
      <div className="tn-steps" aria-label={`내부 단계 ${done}/${inner.length} 완료`}>
        {inner.map((n) => (
          <span key={n.id} className={cx("tn-step-dot", `st-${n.status}`)} title={`${n.label} · ${STATUS_TEXT[n.status]}`} />
        ))}
      </div>
      <div className="tn-meta num">
        {done}/{inner.length} 단계 · {timing(data.run, data.now) ?? STATUS_TEXT[status]}
      </div>
      {data.onDrill ? (
        <button type="button" className="tn-drill nodrag" onClick={data.onDrill}>
          <Maximize2 size={11} aria-hidden="true" />
          열기
        </button>
      ) : null}
    </div>
  );
});

const TerminalNode = memo(function TerminalNode({ data }: NodeProps<TopoNode>) {
  const status = statusOf(data.run);
  return (
    <div className={cx("tn-terminal", data.spec.kind === "end" && "is-end", status !== "idle" && "is-reached")} title={data.spec.label}>
      <Handles />
    </div>
  );
});

export const nodeTypes = {
  step: StepNode,
  human: HumanNode,
  tool: ToolNode,
  model: ModelNode,
  validator: ValidatorNode,
  agent: AgentNode,
  subgraph: SubgraphNode,
  start: TerminalNode,
  end: TerminalNode,
};
