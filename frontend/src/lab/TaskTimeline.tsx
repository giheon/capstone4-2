// Task flow as a Gantt: one lane per stage/actor, bars are LangGraph tasks (node runs, tool calls, human waits).
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { clock, duration } from "../lib/format";
import type { RunState, TaskSpan } from "../lib/runState";
import { cx } from "../ui";

interface Lane {
  id: string;
  label: string;
  match: (node: string) => boolean;
}

const LANES: Lane[] = [
  { id: "ingest", label: "수집", match: (n) => n === "ingest" },
  { id: "perceive", label: "지각 (병렬)", match: (n) => n.startsWith("perceive/") },
  { id: "identity", label: "사람 · 대상 확인", match: (n) => n === "await_identity" },
  { id: "la-model", label: "Analyst · 추론", match: (n) => n === "legal_analyst/model" },
  { id: "la-tools", label: "Analyst · 도구", match: (n) => n.startsWith("legal_analyst/") && n !== "legal_analyst/model" },
  { id: "answers", label: "사람 · 질문 답변", match: (n) => n === "await_answers" },
  { id: "plan", label: "계획 · 검토 안내", match: (n) => n === "plan" || n === "review_guide" },
  { id: "review", label: "사람 · 검토", match: (n) => n === "await_review" },
  // a run whose audit is one deterministic step has no model or tool tasks inside the auditor: show the step itself
  { id: "au-check", label: "Auditor · 요건 대조", match: () => false },
  { id: "au-model", label: "Auditor · 추론", match: (n) => n === "auditor/model" },
  { id: "au-tools", label: "Auditor · 도구", match: (n) => n.startsWith("auditor/") && n !== "auditor/model" },
  { id: "confirm", label: "사람 · 권고 확인", match: (n) => n === "await_audit_confirm" },
  { id: "output", label: "렌더 · 검증 · 보고서", match: (n) => n === "render_original" || n === "verify_output" || n === "report" },
];

function barKind(node: string): string {
  if (node.startsWith("await_")) return "human";
  if (node.endsWith("/model")) return "model";
  if (node.endsWith("/validate")) return "validator";
  if (node.startsWith("legal_analyst/") || node.startsWith("auditor/")) return "tool";
  return "step";
}

function shortName(node: string): string {
  const name = node.split("/").pop() ?? node;
  return name.replace(/^await_/, "");
}

const STEPS = [1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000];

interface Props {
  state: RunState;
  now: number;
  selected: string | null;
  onSelect: (id: string) => void;
  open: boolean;
  onToggle: () => void;
}

export function TaskTimeline({ state, now, selected, onSelect, open, onToggle }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  const total = Math.max(now, state.lastT, 8000) * 1.04;
  const x = (t: number) => (t / total) * width;
  const step = STEPS.find((s) => total / s <= 9) ?? 600000;
  const ticks = Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step);

  const lanes = useMemo(() => {
    const auditorInner = state.tasks.some((t) => t.node.startsWith("auditor/"));
    return LANES.map((lane) => {
      const spans = state.tasks.filter((t) => lane.match(t.node) || (lane.id === "au-check" && !auditorInner && t.node === "auditor"));
      // greedy packing so parallel tasks get their own row inside the lane
      const rows: TaskSpan[][] = [];
      for (const span of spans) {
        const end = (s: TaskSpan) => s.end ?? Number.POSITIVE_INFINITY;
        const row = rows.find((r) => end(r[r.length - 1]) <= span.start);
        if (row) row.push(span);
        else rows.push([span]);
      }
      return { lane, rows };
    }).filter((l) => l.rows.length > 0);
  }, [state.tasks]);

  return (
    <section className={cx("timeline", !open && "is-closed")} aria-label="태스크 흐름">
      <header className="timeline-head">
        <h2 className="timeline-title">태스크 흐름</h2>
        <span className="muted small">
          태스크 {state.tasks.length} · 대기 {state.waits.length} · 막대를 누르면 노드를 선택합니다
        </span>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onToggle} aria-expanded={open}>
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronUp size={14} aria-hidden="true" />}
          {open ? "접기" : "펼치기"}
        </button>
      </header>
      {open ? (
        <div className="timeline-scroll">
          <div className="tl-grid">
            <div className="tl-axis-label" />
            <div className="tl-axis" ref={trackRef}>
              {ticks.map((t) => (
                <span key={t} className="tl-tick mono num" style={{ left: x(t) }}>
                  {clock(t)}
                </span>
              ))}
            </div>
            {lanes.map(({ lane, rows }) => (
              <LaneRow key={lane.id} label={lane.label} rows={rows} x={x} now={now} selected={selected} onSelect={onSelect} ticks={ticks} />
            ))}
            {lanes.length === 0 ? <p className="muted small tl-empty">아직 실행된 태스크가 없습니다.</p> : null}
            <div className="tl-now" style={{ left: `calc(var(--tl-label) + ${x(now)}px)` }} aria-hidden="true" />
          </div>
        </div>
      ) : null}
    </section>
  );
}

function LaneRow({
  label,
  rows,
  x,
  now,
  selected,
  onSelect,
  ticks,
}: {
  label: string;
  rows: TaskSpan[][];
  x: (t: number) => number;
  now: number;
  selected: string | null;
  onSelect: (id: string) => void;
  ticks: number[];
}) {
  return (
    <>
      <div className="tl-lane-label" style={{ height: rows.length * 20 + 6 }}>
        {label}
      </div>
      <div className="tl-lane" style={{ height: rows.length * 20 + 6 }}>
        {ticks.map((t) => (
          <span key={t} className="tl-grid-line" style={{ left: x(t) }} />
        ))}
        {rows.map((row, r) =>
          row.map((span) => {
            const end = span.end ?? now;
            const left = x(span.start);
            const w = Math.max(3, x(end) - left);
            const kind = barKind(span.node);
            const name = shortName(span.node);
            return (
              <button
                type="button"
                key={span.task}
                className={cx(
                  "tl-bar",
                  `bar-${kind}`,
                  span.end === undefined && "is-open",
                  span.result === "cached" && "is-cached",
                  span.result === "error" && "is-error",
                  selected === span.node && "is-selected",
                )}
                style={{ left, width: w, top: 3 + r * 20 }}
                onClick={() => onSelect(span.node.startsWith("perceive/") ? "perceive" : span.node)}
                title={`${span.node} · ${clock(span.start)}–${span.end !== undefined ? clock(span.end) : "진행 중"} · ${duration(end - span.start)}`}
              >
                {w > 46 ? <span className="tl-bar-text">{name}</span> : null}
              </button>
            );
          }),
        )}
      </div>
    </>
  );
}
