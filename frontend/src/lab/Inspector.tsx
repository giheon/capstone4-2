import { type ReactNode, memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { ArrowDown, X } from "lucide-react";
import type { GraphNodeSpec, GraphSpec } from "../api/contract";
import { useCatalog } from "../api/hooks";
import { useRunStream } from "../api/events";
import { clock, duration, usd } from "../lib/format";
import { type MemoryItem, type RunState, agentOf, checkpointBefore, eventOfNode, messageNode, withinNode } from "../lib/runState";
import { cx } from "../ui";
import { StatusIcon } from "./flow/nodes";
import { KIND_LABEL as PART_KIND_LABEL, groupByStage, stageLabels } from "./pipelineParts";

type Tab = "node" | "memory" | "events" | "checkpoints";

const KIND_LABEL: Record<GraphNodeSpec["kind"], string> = {
  start: "시작",
  end: "종료",
  step: "결정적 노드",
  subgraph: "서브그래프",
  agent: "에이전트",
  model: "LLM 턴",
  tool: "도구",
  human: "사람 (interrupt)",
  validator: "결정적 검증기",
};

interface Props {
  runId: string;
  graph: GraphSpec;
  state: RunState;
  now: number;
  selected: string | null;
  /** bumps on every explicit pick of a node, also of the one that is already selected */
  pick: number;
  /** the record can still grow or move: a live run that has not ended, or a replay */
  advancing: boolean;
  onSelect: (id: string | null) => void;
}

export function Inspector({ runId, graph, state, now, selected, pick, advancing, onSelect }: Props) {
  const [tab, setTab] = useState<Tab>("memory");
  const body = useRef<HTMLDivElement>(null);
  const spec = selected ? graph.nodes.find((n) => n.id === selected) ?? null : null;
  const agent = agentOf(selected);
  const focus: Focus = { runId, node: selected, label: spec?.label ?? selected ?? "", pick, advancing };
  return (
    <aside className="inspector" aria-label="검사기">
      <div className="inspector-head">
        {spec ? (
          <>
            <div className="inspector-title">
              <span className="inspector-kind">{KIND_LABEL[spec.kind]}</span>
              <h2>{spec.label}</h2>
            </div>
            <button type="button" className="btn btn-sm btn-ghost btn-icon" onClick={() => onSelect(null)} aria-label="선택 해제">
              <X size={14} aria-hidden="true" />
            </button>
          </>
        ) : (
          <div className="inspector-title">
            <span className="inspector-kind">실행 전체</span>
            <h2>{state.title || runId}</h2>
          </div>
        )}
      </div>
      <div className="tabs lab-tabs" role="tablist" aria-label="검사 항목">
        {(
          [
            ["memory", agent ? "에이전트 메모리" : "상태 메모리"],
            ["node", spec ? "노드" : "개요"],
            ["events", "이벤트"],
            ["checkpoints", "체크포인트"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={cx("tab", tab === id && "is-active")}
            onClick={() => {
              // every tab opens from the top; a record list then places itself (tail, or the selected node's records)
              if (tab !== id && body.current) body.current.scrollTop = 0;
              setTab(id);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="inspector-body" role="tabpanel" ref={body} data-rec-scroller>
        {tab === "memory" ? (
          agent ? (
            <AgentMemory key={agent} agent={agent} state={state} focus={focus} />
          ) : (
            <StateMemory state={state} reads={spec?.reads} focus={focus} />
          )
        ) : null}
        {tab === "node" ? spec ? <NodeDetail key={spec.id} spec={spec} state={state} now={now} /> : <RunOverview state={state} /> : null}
        {tab === "events" ? <EventLog upto={state.lastSeq} focus={focus} /> : null}
        {tab === "checkpoints" ? <Checkpoints state={state} focus={focus} /> : null}
      </div>
    </aside>
  );
}

/* ---------- record lists: oldest first, tail-follow, and the selected node's records ---------- */

/** what a record list needs to know about the run and the node selected in the diagram */
interface Focus {
  runId: string;
  node: string | null;
  /** the node's label, for the note above a list */
  label: string;
  pick: number;
  advancing: boolean;
}

interface RecordListOptions {
  /** records in the list, before any cap on how many are rendered */
  count: number;
  /** how many of them belong to the selected node */
  hits: number;
  /** seq of the newest record */
  tail: number;
  /** changes when the list is laid out anew (a filter): it is then placed as if it had just been opened */
  layout?: string;
}

/** px from the bottom that still count as "at the newest record" */
const NEAR_END = 24;
/** a run whose end arrives this soon after the list opened was already over: its events came as a backlog */
const BACKLOG_MS = 1500;

function atEnd(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_END;
}

/**
 * Scroll behaviour shared by every time-ordered list of the inspector (records run oldest → newest, top → bottom).
 *
 * - While the record advances the list stays with its newest row. Scrolling away stops that; scrolling back to the
 *   end by hand, or the jump button, resumes it.
 * - Picking a node brings its first record to the top of the list and stops following: the pick wins over the tail.
 *   Rows mark themselves with `data-hit`; when every row is the node's (a list already scoped to it) or none is,
 *   there is nowhere to go and the position is left alone.
 * - A record that no longer moves (a finished run) opens at its first row.
 *
 * `root` goes on an element that contains the rows and sits inside (or is) the `[data-rec-scroller]` that scrolls them.
 */
function useRecordList(focus: Focus, { count, hits, tail, layout = "" }: RecordListOptions) {
  const { runId, node, pick, advancing } = focus;
  const root = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(advancing);
  const [away, setAway] = useState(false);
  /** how many records there were when following stopped */
  const [seen, setSeen] = useState(count);
  const live = useRef({ following, count, advancing, picked: `${node}#${pick}`, gesture: false, settled: false, openedAt: 0 });
  const mixed = hits > 0 && hits < count;

  const scroller = () => root.current?.closest<HTMLElement>("[data-rec-scroller]") ?? null;

  const follow = (on: boolean) => {
    if (live.current.following === on) return;
    live.current.following = on;
    setFollowing(on);
    if (!on) setSeen(live.current.count);
  };

  /** bring the node's first record to the top of the list, clear of whatever sticks there */
  const toNode = (el: HTMLElement, smooth: boolean): boolean => {
    const hit = root.current?.querySelector<HTMLElement>("[data-hit]");
    if (!hit) return false;
    const head = root.current?.querySelector<HTMLElement>("[data-rec-head]");
    el.style.scrollPaddingTop = head ? `${head.offsetHeight}px` : "";
    const behavior: ScrollBehavior = smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "instant";
    // `container` keeps the movement inside the list; a browser without it may also move an outer scroller
    const within = { behavior, inline: "nearest", container: "nearest" };
    hit.scrollIntoView({ ...within, block: "start" } as ScrollIntoViewOptions);
    // a list that scrolls on its own (agent memory) may be out of sight in the panel: show where it begins, the note above it
    if (el.parentElement?.closest("[data-rec-scroller]")) {
      (el.previousElementSibling ?? el).scrollIntoView({ ...within, block: "nearest" } as ScrollIntoViewOptions);
    }
    return true;
  };

  useLayoutEffect(() => {
    live.current.count = count;
  });

  // opened, or laid out anew
  useLayoutEffect(() => {
    const el = scroller();
    if (!el) return;
    const s = live.current;
    s.openedAt = performance.now();
    s.gesture = false;
    s.settled = false;
    // a list that scrolls on its own opens with the panel around it at the top, wherever the previous view left it
    const panel = el.parentElement?.closest<HTMLElement>("[data-rec-scroller]");
    if (panel) panel.scrollTop = 0;
    if (node && mixed && toNode(el, false)) {
      follow(false);
      s.settled = true;
    } else if (advancing) {
      follow(true);
      el.scrollTop = el.scrollHeight;
    } else {
      follow(false);
      el.scrollTop = 0;
    }
    setAway(!atEnd(el));
  }, [runId, layout]);

  // a node was picked (again), or the selection was cleared
  useLayoutEffect(() => {
    const s = live.current;
    const picked = `${node}#${pick}`;
    if (picked === s.picked) return;
    s.picked = picked;
    const el = scroller();
    if (!el) return;
    if (!node) {
      // nothing holds the position any more: a list left at its end goes back to following
      if (advancing && atEnd(el)) follow(true);
    } else if (mixed && toNode(el, true)) {
      follow(false);
      s.settled = true;
      s.gesture = false;
    }
  }, [node, pick]);

  // new records
  useLayoutEffect(() => {
    const el = scroller();
    if (!el) return;
    if (live.current.following) el.scrollTop = el.scrollHeight;
    setAway(!atEnd(el));
    setSeen((n) => Math.min(n, count));
  }, [tail, count]);

  // the record starts or stops moving
  useLayoutEffect(() => {
    const s = live.current;
    if (s.advancing === advancing) return;
    s.advancing = advancing;
    const el = scroller();
    if (!el || s.settled) return;
    if (advancing) {
      // a replay starts: the list now ends at the cursor, stay with it
      follow(true);
      el.scrollTop = el.scrollHeight;
    } else if (performance.now() - s.openedAt < BACKLOG_MS) {
      // the run was over before it was opened and nobody has touched the list: read it from its first record.
      // A run that ends while it is being watched stays where it is, at its last records.
      follow(false);
      el.scrollTop = 0;
    }
    setAway(!atEnd(el));
  }, [advancing]);

  useEffect(() => {
    const el = scroller();
    if (!el) return;
    const s = live.current;
    const onScroll = () => {
      const end = atEnd(el);
      setAway(!end);
      if (!end && s.following) {
        follow(false);
        s.settled = true;
      } else if (end && !s.following && s.gesture) {
        // only the user's own scrolling resumes following: a pick that happens to land on the last rows does not
        follow(true);
      }
    };
    const onGesture = () => {
      s.gesture = true;
      s.settled = true;
    };
    const gestures = ["wheel", "touchmove", "pointerdown", "keydown"] as const;
    el.addEventListener("scroll", onScroll, { passive: true });
    for (const type of gestures) el.addEventListener(type, onGesture, { passive: true });
    const resized = new ResizeObserver(() => {
      if (s.following) el.scrollTop = el.scrollHeight;
      setAway(!atEnd(el));
    });
    resized.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      for (const type of gestures) el.removeEventListener(type, onGesture);
      resized.disconnect();
      el.style.scrollPaddingTop = "";
    };
  }, []);

  const unseen = following ? 0 : Math.max(0, count - seen);
  return {
    root,
    following,
    unseen,
    /** there is something newer below and the list is not going there by itself */
    behind: away && !following && (advancing || unseen > 0),
    jump: () => {
      const el = scroller();
      if (!el) return;
      follow(true);
      el.scrollTop = el.scrollHeight;
      setAway(false);
    },
  };
}

type RecordList = ReturnType<typeof useRecordList>;

/** the way back to the newest record; takes no room, so the list does not shift when it appears */
function TailJump({ list }: { list: RecordList }) {
  return (
    <div className="tail-jump">
      {list.behind ? (
        <button type="button" className="btn btn-sm btn-secondary" onClick={list.jump}>
          <ArrowDown size={13} aria-hidden="true" />
          {list.unseen > 0 ? `새 기록 ${list.unseen}건` : "최신 기록으로"}
        </button>
      ) : null}
    </div>
  );
}

/** one line above a list: what the marks in it mean, or that the selected node left nothing here */
function NodeNote({ hits, children }: { hits: number; children: ReactNode }) {
  return (
    <p className={cx("rec-note", hits === 0 && "is-empty")} role={hits === 0 ? "status" : undefined}>
      {children}
    </p>
  );
}

/** read by screen readers in front of a marked row: the mark itself is a bar and heavier text */
function HitLabel() {
  return <span className="sr-only">선택한 노드의 기록 </span>;
}

/* ---------- memory ---------- */

const ROLE_LABEL: Record<MemoryItem["role"], string> = {
  system: "시스템",
  user: "입력",
  assistant: "모델",
  tool: "도구 결과",
  call: "도구 호출",
};

function AgentMemory({ agent, state, focus }: { agent: "legal_analyst" | "auditor"; state: RunState; focus: Focus }) {
  const items = state.memory[agent] ?? [];
  const mine = (m: MemoryItem) => withinNode(focus.node, messageNode(agent, m.role, m.tool));
  const hits = items.filter(mine).length;
  const list = useRecordList(focus, { count: items.length, hits, tail: items[items.length - 1]?.seq ?? 0 });
  const budgets = state.budgets[agent] ?? {};
  const writes = Object.values(state.channels).filter((c) => c.writer === agent || c.history.some((h) => h.writer === agent));
  const model = state.models[agent === "legal_analyst" ? "analyst" : "auditor"];
  const toolCount = state.tools.filter((t) => t.agent === agent).length;
  return (
    <div className="memory">
      <section className="mem-section">
        <h3 className="mem-title">
          작업 메모리 <span className="muted">메시지 {items.filter((i) => i.role !== "call").length} · 도구 호출 {toolCount}</span>
        </h3>
        <p className="mem-hint">LangGraph 스레드 체크포인트에 저장되는 이 에이전트의 메시지 기록입니다. 모델: {model ?? "—"}</p>
        {items.length ? (
          <NodeNote hits={hits}>{hits ? `${focus.label} 노드가 남긴 메시지 ${hits}건` : `${focus.label} 노드가 남긴 메시지는 없습니다.`}</NodeNote>
        ) : null}
        <div className="mem-scroll" ref={list.root} data-rec-scroller>
          {items.length === 0 ? (
            <p className="muted small">아직 이 에이전트가 실행되지 않았습니다.</p>
          ) : (
            <ol className="mem-list">
              {items.map((m) => {
                const hit = mine(m);
                return (
                  <li key={m.seq} className={cx("mem-item rec", `role-${m.role}`, hit && "is-hit")} data-seq={m.seq} data-hit={hit || undefined}>
                    <span className="mem-role">
                      {hit ? <HitLabel /> : null}
                      {ROLE_LABEL[m.role]}
                      {m.tool ? <span className="mono"> · {m.tool}</span> : null}
                      <span className="mem-time mono num">{clock(m.t)}</span>
                    </span>
                    {m.role === "system" ? (
                      <details>
                        <summary>프롬프트 보기</summary>
                        <p className="mem-text">{m.text}</p>
                      </details>
                    ) : (
                      <p className={cx("mem-text", m.role === "call" && "mono")}>{m.text || "—"}</p>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
          <TailJump list={list} />
        </div>
      </section>
      <section className="mem-section">
        <h3 className="mem-title">예산</h3>
        <div className="budget-table">
          {(["tool_calls", "searches", "question_rounds", "retries"] as const)
            .filter((k) => budgets[k])
            .map((k) => {
              const b = budgets[k]!;
              return (
                <div key={k} className="budget-row">
                  <span>{{ tool_calls: "도구 호출", searches: "검색", question_rounds: "질문 라운드", retries: "검증 재시도" }[k]}</span>
                  <span className="budget-bar">
                    <span style={{ width: `${Math.min(1, b.used / b.limit) * 100}%` }} />
                  </span>
                  <span className="num">
                    {b.used}/{b.limit}
                  </span>
                </div>
              );
            })}
        </div>
      </section>
      {writes.length ? (
        <section className="mem-section">
          <h3 className="mem-title">이 에이전트가 쓴 상태 채널</h3>
          <ul className="channel-list">
            {writes.map((c) => (
              <li key={c.name}>
                <span className="mono channel-name">
                  {c.name} <span className="muted">v{c.version}</span>
                </span>
                <span className="channel-summary">{c.summary}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/** not graph state: the record of steps that took a stored result instead of computing (and of a checkpoint fork) */
const REUSE = "reuse";

function reuseSummary(history: { writer: string }[]): string {
  const steps = [...new Set(history.map((h) => h.writer).filter((w) => w !== "__start__"))].map((w) => w.split("/").pop());
  const forked = history.some((h) => h.writer === "__start__");
  return [forked ? "체크포인트에서 이어 감 (그 앞 단계는 실행하지 않음)" : "", steps.length ? `저장된 결과를 가져온 단계 ${steps.length}개: ${steps.join(", ")}` : ""].filter(Boolean).join(" · ");
}

function StateMemory({ state, reads = [], focus }: { state: RunState; reads?: string[]; focus: Focus }) {
  // a channel sits where its latest write happened: the list reads top → bottom in the order the run wrote state
  const channels = Object.values(state.channels)
    .sort((a, b) => a.seq - b.seq)
    // the versions the selected node wrote (v1 is the first entry of the history)
    .map((c) => ({ ...c, mine: c.history.flatMap((h, i) => (withinNode(focus.node, h.writer) ? [i + 1] : [])) }));
  const [open, setOpen] = useState<string | null>(null);
  const hits = channels.filter((c) => c.mine.length > 0).length;
  const list = useRecordList(focus, { count: channels.length, hits, tail: channels[channels.length - 1]?.seq ?? 0 });
  return (
    <div className="memory" ref={list.root}>
      <section className="mem-section">
        <h3 className="mem-title">
          케이스 상태 (단기 메모리) <span className="muted">채널 {channels.length}</span>
        </h3>
        <p className="mem-hint">
          노드가 쓰는 그래프 상태 채널을 마지막으로 쓰인 순서대로 보여 줍니다(아래가 최신). 체크포인트마다 버전이 저장됩니다. 노드를 고르면 그 노드가 쓴 채널을
          표시하고, 읽는 채널에는 ‘읽음’을 붙입니다.
        </p>
        {focus.node && channels.length ? (
          <div className="rec-head" data-rec-head>
            <NodeNote hits={hits}>{hits ? `${focus.label} 노드가 쓴 채널 ${hits}개` : `${focus.label} 노드가 쓴 상태 채널은 없습니다.`}</NodeNote>
          </div>
        ) : null}
        {channels.length === 0 ? (
          <p className="muted small">아직 기록된 상태가 없습니다.</p>
        ) : (
          <ul className="channel-list">
            {channels.map((c) => {
              const hit = c.mine.length > 0;
              return (
                <li key={c.name} className={cx("rec", hit && "is-hit")} data-seq={c.seq} data-channel={c.name} data-hit={hit || undefined}>
                  <button type="button" className="channel-btn" onClick={() => setOpen(open === c.name ? null : c.name)} aria-expanded={open === c.name}>
                    <span className="mono channel-name">
                      {hit ? <HitLabel /> : null}
                      {c.name} <span className="muted">v{c.version}</span>
                      {c.name === REUSE ? <span className="channel-tag channel-tag-reuse">재사용</span> : null}
                      {reads.includes(c.name) ? <span className="channel-tag">읽음</span> : null}
                    </span>
                    <span className="channel-writer mono">
                      {c.writer} <span className="num">{clock(c.t)}</span>
                    </span>
                  </button>
                  <span className="channel-summary">{c.name === REUSE ? reuseSummary(c.history) : c.summary}</span>
                  {hit && c.mine.length < c.history.length ? (
                    <span className="channel-mine">선택한 노드가 쓴 버전 {c.mine.map((v) => `v${v}`).join(", ")}</span>
                  ) : null}
                  {open === c.name ? (
                    <ol className="channel-history">
                      {c.history.map((h, i) => (
                        <li key={h.seq} className={cx(c.mine.includes(i + 1) && "is-hit")}>
                          <span className="mono num">{clock(h.t)}</span> <span className="mono">{h.writer}</span> — {h.summary}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        <TailJump list={list} />
      </section>
      <section className="mem-section">
        <h3 className="mem-title">장기 메모리 (Store)</h3>
        <p className="mem-hint">케이스를 넘나드는 저장소(답변 재사용 등)는 M6에서 켭니다. 지금은 비어 있습니다.</p>
      </section>
    </div>
  );
}

/* ---------- node detail / overview ---------- */

/** how many of a node's tasks the node tab lists before it offers the earlier ones */
const TASK_WINDOW = 12;

function NodeDetail({ spec, state, now }: { spec: GraphNodeSpec; state: RunState; now: number }) {
  const run = state.nodes[spec.id];
  const tasks = state.tasks.filter((t) => withinNode(spec.id, t.node));
  const [allTasks, setAllTasks] = useState(false);
  const earlier = allTasks ? 0 : Math.max(0, tasks.length - TASK_WINDOW);
  const toolName = spec.kind === "tool" ? spec.id.split("/")[1] : null;
  const agent = spec.kind === "tool" ? spec.id.split("/")[0] : null;
  const calls = toolName ? state.tools.filter((t) => t.tool === toolName && t.agent === agent) : [];
  const waits = spec.kind === "human" ? state.waits.filter((w) => w.node === spec.id) : [];
  const validations = state.validations.filter((v) => v.node === spec.id);
  return (
    <div className="node-detail">
      <p className="node-desc">{spec.description}</p>
      <dl className="kv">
        {spec.model ? (
          <div>
            <dt>모델</dt>
            <dd>{spec.model}</dd>
          </div>
        ) : null}
        <div>
          <dt>상태</dt>
          <dd className="kv-status">
            <StatusIcon status={run?.status ?? "idle"} /> {run ? `${run.runs}회 실행 · 누적 ${duration(run.totalMs)}` : "실행 전"}
          </dd>
        </div>
        {run && run.costUsd > 0 ? (
          <div>
            <dt>비용</dt>
            <dd className="num">{usd(run.costUsd, 4)}</dd>
          </div>
        ) : null}
        {spec.reads.length ? (
          <div>
            <dt>읽는 채널</dt>
            <dd className="chips">
              {spec.reads.map((r) => (
                <span key={r} className="chip mono">
                  {r}
                </span>
              ))}
            </dd>
          </div>
        ) : null}
        {spec.writes.length ? (
          <div>
            <dt>쓰는 채널</dt>
            <dd className="chips">
              {spec.writes.map((r) => (
                <span key={r} className="chip mono chip-write">
                  {r}
                </span>
              ))}
            </dd>
          </div>
        ) : null}
      </dl>
      {calls.length ? (
        <section className="mem-section">
          <h3 className="mem-title">호출 기록 {calls.length}</h3>
          <ol className="call-list">
            {calls.map((c) => (
              <li key={`${c.call}-${c.seq}`} className={cx(c.ok === false && "is-failed")}>
                <span className="mono call-args">{c.args || "(인자 없음)"}</span>
                <span className="call-result">{c.summary ?? (c.end === undefined ? "실행 중…" : "")}</span>
                <span className="mono num muted small">
                  {clock(c.start)} · {c.end !== undefined ? duration(c.end - c.start) : clock(now - c.start)}
                </span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      {waits.length ? (
        <section className="mem-section">
          <h3 className="mem-title">interrupt 기록</h3>
          <ol className="call-list">
            {waits.map((w) => (
              <li key={`${w.id}-${w.start}`}>
                <span className="call-args">{w.summary}</span>
                <span className="call-result">{w.resume ? `재개: ${w.resume}` : "사용자 응답 대기 중"}</span>
                <span className="mono num muted small">
                  {clock(w.start)} · 대기 {duration((w.end ?? now) - w.start)}
                  {w.auto ? " · 자동 응답" : ""}
                </span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      {validations.length ? (
        <section className="mem-section">
          <h3 className="mem-title">검증 결과</h3>
          <ol className="call-list">
            {validations.map((v, i) => (
              <li key={i} className={cx(!v.ok && "is-failed")}>
                <span className="call-args">{v.ok ? "통과" : "되먹임"}</span>
                {v.issues.map((issue) => (
                  <span key={issue} className="call-result">
                    {issue}
                  </span>
                ))}
                <span className="mono num muted small">{clock(v.t)}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      {tasks.length && !calls.length ? (
        <section className="mem-section">
          <h3 className="mem-title">태스크 {tasks.length}</h3>
          {earlier ? (
            <button type="button" className="link-button rec-more" onClick={() => setAllTasks(true)}>
              앞선 태스크 {earlier}건 보기
            </button>
          ) : null}
          <ol className="call-list">
            {tasks.slice(earlier).map((t) => (
              <li key={`${t.task}-${t.start}`}>
                <span className="mono call-args">{t.node}</span>
                <span className="mono num muted small">
                  {clock(t.start)} · {t.end !== undefined ? duration(t.end - t.start) : `진행 ${clock(now - t.start)}`}
                  {t.result === "cached" ? " · 캐시" : ""}
                </span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

function RunOverview({ state }: { state: RunState }) {
  const catalog = useCatalog();
  const labels = stageLabels(catalog.data);
  return (
    <div className="node-detail">
      <dl className="kv">
        <div>
          <dt>프로필</dt>
          <dd className="mono">
            {state.profileId && !state.forkedFrom ? <Link to={`/lab/profiles/${state.profileId}`}>{state.profile}</Link> : state.profile || "—"}
          </dd>
        </div>
        <div>
          <dt>config_hash</dt>
          <dd className="mono">{state.configHash || "—"}</dd>
        </div>
        <div>
          <dt>토큰</dt>
          <dd className="num">
            입력 {state.tokensIn.toLocaleString()} (캐시 {state.cachedIn.toLocaleString()}) · 출력 {state.tokensOut.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>비용</dt>
          <dd className="num">
            {usd(state.costUsd, 4)} <span className="muted">/ 상한 {usd(state.costCapUsd)}</span>
          </dd>
        </div>
      </dl>
      {groupByStage(state.components).map(([stage, parts]) => (
        <section key={stage} className="mem-section">
          <h3 className="mem-title">{labels[stage] ?? stage}</h3>
          <dl className="kv kv-parts">
            {parts.map((c) => (
              <div key={c.slot}>
                <dt>{c.slotLabel}</dt>
                <dd>
                  {c.label}
                  {c.kind === "api" ? <span className="muted small"> · {PART_KIND_LABEL.api}</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <p className="mem-hint">
        노드를 누르면 그 노드의 설명·호출 기록·읽고 쓰는 채널을, 에이전트를 누르면 메시지 기록(작업 메모리)을 봅니다. 상태·이벤트·체크포인트 목록은 시간순(위가 먼저)이며,
        고른 노드의 첫 기록으로 이동해 그 노드의 기록을 왼쪽 막대로 표시합니다.
      </p>
    </div>
  );
}

/* ---------- events ---------- */

const FILTERS = [
  ["all", "전체"],
  ["node", "노드"],
  ["tool", "도구"],
  ["llm", "LLM"],
  ["state", "상태"],
  ["human", "사람"],
] as const;

type FilterId = (typeof FILTERS)[number][0];
type RunEvent = ReturnType<typeof useRunStream>["events"][number];

function groupOf(kind: RunEvent["kind"]): FilterId | "other" {
  switch (kind) {
    case "node.start":
    case "node.end":
    case "edge":
    case "checkpoint":
      return "node";
    case "tool.call":
    case "tool.result":
      return "tool";
    case "llm":
    case "message":
      return "llm";
    case "state":
    case "budget":
    case "validator":
      return "state";
    case "interrupt":
    case "resume":
      return "human";
    default:
      return "other";
  }
}

/** rows kept in the page while the list follows the newest event; a list that is being read is never cut */
const EVENT_WINDOW = 300;

interface EventRowData {
  seq: number;
  t: number;
  kind: RunEvent["kind"];
  text: string;
  hit: boolean;
}

const EventRow = memo(function EventRow({ seq, t, kind, text, hit }: EventRowData) {
  return (
    <li className={cx("event-row rec", `ev-${kind.replace(".", "-")}`, hit && "is-hit")} data-seq={seq} data-hit={hit || undefined}>
      <span className="mono num muted">
        {hit ? <HitLabel /> : null}#{seq}
      </span>
      <span className="mono num muted">{clock(t)}</span>
      <span className="ev-kind mono">{kind}</span>
      <span className="ev-text">{text}</span>
    </li>
  );
});

function EventLog({ upto, focus }: { upto: number; focus: Focus }) {
  const { runId, node: selected } = focus;
  const stream = useRunStream(runId);
  const [filter, setFilter] = useState<FilterId>("all");
  const [onlySelected, setOnlySelected] = useState(false);
  /** the run whose earlier events the user asked to see */
  const [wholeRun, setWholeRun] = useState<string | null>(null);
  const scoped = onlySelected && selected !== null;
  const all = useMemo(() => {
    const out: EventRowData[] = [];
    for (const e of stream.events) {
      if (e.seq > upto) break;
      if (filter !== "all" && groupOf(e.kind) !== filter) continue;
      const hit = eventOfNode(e, selected);
      if (scoped && !hit) continue;
      out.push({ seq: e.seq, t: e.t, kind: e.kind, text: describe(e), hit });
    }
    return out;
  }, [stream.events, stream.version, upto, filter, scoped, selected]);
  const firstHit = all.findIndex((r) => r.hit);
  const hits = firstHit === -1 ? 0 : all.reduce((n, r) => n + (r.hit ? 1 : 0), 0);
  const list = useRecordList(focus, { count: all.length, hits, tail: all[all.length - 1]?.seq ?? 0, layout: `${filter}|${scoped ? selected : ""}` });

  // Oldest first, so the cap drops rows from the top — only while the list follows the tail of a record that still
  // moves. Rows above a reader stay where they are, a finished run is listed whole, and so is a run whose selected
  // node has records above the cut: they must stay reachable.
  const floor = useRef(0);
  let start = !focus.advancing || wholeRun === runId ? 0 : Math.max(0, all.length - EVENT_WINDOW);
  if (start > 0 && !list.following) {
    const kept = all.findIndex((r) => r.seq >= floor.current);
    if (kept !== -1) start = Math.min(start, kept);
  }
  if (firstHit !== -1 && firstHit < start) start = 0;
  const rows = start > 0 ? all.slice(start) : all;
  const top = rows[0]?.seq ?? 0;
  useLayoutEffect(() => {
    floor.current = top;
  }, [top]);

  const filterLabel = FILTERS.find(([id]) => id === filter)?.[1] ?? "";
  return (
    <div className="events" ref={list.root}>
      <div className="rec-head event-head" data-rec-head>
        <div className="event-filters">
          <div className="segmented small-seg" role="radiogroup" aria-label="이벤트 종류">
            {FILTERS.map(([id, label]) => (
              <label key={id}>
                <input type="radio" name="event-filter" checked={filter === id} onChange={() => setFilter(id)} />
                {label}
              </label>
            ))}
          </div>
          <label className="check small">
            <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} disabled={!selected} />
            선택한 노드만
          </label>
        </div>
        {selected ? (
          <NodeNote hits={hits}>
            {hits
              ? `${focus.label} 노드 이벤트 ${hits}건`
              : filter === "all"
                ? `${focus.label} 노드가 남긴 이벤트는 없습니다.`
                : `‘${filterLabel}’ 종류에는 ${focus.label} 노드의 이벤트가 없습니다.`}
          </NodeNote>
        ) : null}
      </div>
      {rows.length === 0 && !(selected && hits === 0) ? (
        <p className="muted small event-none">{filter === "all" && !scoped ? "아직 이벤트가 없습니다." : "조건에 맞는 이벤트가 없습니다."}</p>
      ) : null}
      <ol className="event-list">
        {start > 0 ? (
          <li className="event-more">
            <button type="button" className="link-button rec-more" onClick={() => setWholeRun(runId)}>
              앞선 이벤트 {start}건 보기
            </button>
          </li>
        ) : null}
        {rows.map((r) => (
          <EventRow key={r.seq} {...r} />
        ))}
      </ol>
      <TailJump list={list} />
    </div>
  );
}

function describe(e: RunEvent): string {
  switch (e.kind) {
    case "run.start":
      return `${e.title} · ${e.profile}`;
    case "run.status":
      return e.status;
    case "node.start":
      return e.node;
    case "node.end":
      return `${e.node} → ${e.result}${e.writes.length ? ` · writes ${e.writes.join(", ")}` : ""}`;
    case "edge":
      return `${e.edge}${e.dir === "back" ? " (반환)" : ""}`;
    case "llm":
      return `${e.agent} · ${e.model} · in ${e.tokensIn}${e.cachedIn ? ` (캐시 ${e.cachedIn})` : ""} → out ${e.tokensOut} · ${usd(e.costUsd, 4)}`;
    case "tool.call":
      return `${e.agent}/${e.tool}(${e.args})`;
    case "tool.result":
      return `${e.agent}/${e.tool} ${e.ok ? "ok" : "fail"} · ${e.summary}`;
    case "message":
      return `${e.agent} ${e.role}: ${e.text}`;
    case "state":
      return `${e.channel} ← ${e.writer}: ${e.summary}`;
    case "interrupt":
      return `${e.node} interrupt(${e.payload}) · ${e.summary}`;
    case "resume":
      return `${e.node} resume · ${e.summary}`;
    case "checkpoint":
      return `${e.id} step ${e.step} → next ${e.next.join(", ")}`;
    case "budget":
      return `${e.agent} ${e.key} ${e.used}/${e.limit}`;
    case "validator":
      return `${e.node} ${e.ok ? "통과" : `되먹임: ${e.issues.join("; ")}`}`;
    case "activity":
      return e.text;
    case "run.end":
      return e.status;
    default:
      return "";
  }
}

/* ---------- checkpoints ---------- */

/** LangGraph checkpoint ids are time-ordered UUIDs that share their first block within a run: show the blocks that differ */
function shortCheckpoint(id: string): string {
  const blocks = id.split("-");
  return blocks.length === 5 && id.length === 36 ? `…${blocks.slice(1, 4).join("-")}` : id;
}

// Read-only: a run is looked at here. Running again with other parts, from one of these checkpoints or from the
// start, is what a section experiment does.
function Checkpoints({ state, focus }: { state: RunState; focus: Focus }) {
  const checkpoints = state.checkpoints;
  const mine = (next: string[]) => checkpointBefore(focus.node, next);
  const hits = checkpoints.filter((c) => mine(c.next)).length;
  const list = useRecordList(focus, {
    count: checkpoints.length,
    hits,
    tail: checkpoints[checkpoints.length - 1]?.seq ?? 0,
  });
  if (checkpoints.length === 0) {
    return (
      <div className="checkpoints" ref={list.root}>
        <p className="muted small">아직 체크포인트가 없습니다.</p>
      </div>
    );
  }
  return (
    <div className="checkpoints" ref={list.root}>
      <p className="mem-hint">
        체크포인트는 super-step마다 저장되는 스레드 상태입니다. 부품을 바꿔 보거나 앞 단계의 체크포인트에서 이어 실행하는 일은{" "}
        <Link to="/lab/experiments">구간 실험</Link>에서 합니다.
      </p>
      {focus.node ? (
        <div className="rec-head" data-rec-head>
          <NodeNote hits={hits}>
            {hits ? `${focus.label} 노드 직전에 저장된 체크포인트 ${hits}건` : `${focus.label} 노드 직전에 저장된 체크포인트는 없습니다.`}
          </NodeNote>
        </div>
      ) : null}
      <ol className="ckpt-list">
        {checkpoints.map((c) => {
          const hit = mine(c.next);
          return (
            <li key={c.id} className={cx("ckpt rec", hit && "is-hit")} data-seq={c.seq} data-hit={hit || undefined}>
              <div className="ckpt-row">
                <span className="mono ckpt-id" title={c.id}>
                  {hit ? <HitLabel /> : null}
                  {shortCheckpoint(c.id)}
                </span>
                <span className="muted small num">
                  step {c.step} · {clock(c.t)}
                </span>
                <span className="ckpt-next mono">→ {c.next.length ? c.next.join(", ") : "끝"}</span>
              </div>
            </li>
          );
        })}
      </ol>
      <TailJump list={list} />
    </div>
  );
}
