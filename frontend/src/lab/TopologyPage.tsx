import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { ArrowLeft, Crosshair, Map as MapIcon, Maximize, Pause, Play, Radio, RotateCcw } from "lucide-react";
import type { GraphSpec, GraphView } from "../api/contract";
import { useRunStream } from "../api/events";
import { useGraph, useRuns } from "../api/hooks";
import { type LabRun, experimentPath, isStageExperimentId } from "../api/stage";
import { STATUS_LABEL, clock, usd } from "../lib/format";
import type { RunState } from "../lib/runState";
import { EmptyState, ErrorState, Spinner, cx } from "../ui";
import { buildFlow, focusNode, nodeCenter } from "./flow/build";
import { edgeTypes } from "./flow/edges";
import { nodeTypes } from "./flow/nodes";
import { Inspector } from "./Inspector";
import { TaskTimeline } from "./TaskTimeline";
import { ANSWER_LABEL, type RunView, awaitsPerson, nodeLabel, useRunView, waitingOf } from "./useRunView";

const FIT = { padding: { top: "64px", right: "24px", bottom: "28px", left: "24px" } } as const;

// One run, opened from the run list (`/lab/runs/:runId`). Which run is shown is the address; the way to another
// run is back through the list.
export function TopologyPage() {
  const { runId } = useParams();
  const runs = useRuns();
  const graph = useGraph();
  const stream = useRunStream(runId);
  // the list as it was before this run was opened cannot say that the run does not exist
  const openedAt = useMemo(() => Date.now(), [runId]);
  const run = (runs.data as LabRun[] | undefined)?.find((r) => r.id === runId);
  const listed = run !== undefined;
  const refetch = runs.refetch;
  useEffect(() => {
    if (!listed) void refetch();
  }, [runId, listed, refetch]);

  if (!runId) return <Navigate replace to="/lab/runs" />;
  // a run that has just been started may reach the stream before it reaches the list
  const known = run?.source === "live" || stream.events.length > 0;
  if (!known) {
    if (run) return <NoRun runId={runId} summary />;
    if (runs.isError) return <LabError error={runs.error} />;
    if (!runs.data || runs.dataUpdatedAt < openedAt) return <LabLoading />;
    return <NoRun runId={runId} />;
  }
  if (graph.isLoading) return <LabLoading />;
  if (graph.isError || !graph.data) return <LabError error={graph.error} />;
  return (
    <ReactFlowProvider>
      <Topology runId={runId} graph={graph.data} run={run} />
    </ReactFlowProvider>
  );
}

/** the address names a run the server does not have, or (`summary`) one it only keeps numbers of */
function NoRun({ runId, summary = false }: { runId: string; summary?: boolean }) {
  return (
    <div className="lab-empty">
      <EmptyState
        title={summary ? "이 실행은 이벤트 기록이 없습니다" : "이 실행을 찾을 수 없습니다"}
        action={
          <Link to="/lab/runs" className="btn btn-primary">
            <ArrowLeft size={15} aria-hidden="true" />
            실행 목록으로
          </Link>
        }
      >
        <span className="mono">{runId}</span>
        {summary
          ? " 실행은 수치 요약만 남아 있어 토폴로지로 볼 수 없습니다."
          : " 실행이 서버에 없습니다. 주소를 확인하거나 실행 목록에서 다시 찾아 주세요."}
      </EmptyState>
    </div>
  );
}

function LabLoading() {
  return (
    <div className="lab-empty">
      <Spinner size={18} label="불러오는 중" />
    </div>
  );
}

function LabError({ error }: { error: unknown }) {
  return (
    <div className="lab-empty">
      <ErrorState error={error} />
    </div>
  );
}

function Topology({ runId, graph, run }: { runId: string; graph: GraphSpec; run?: LabRun }) {
  const view = useRunView(runId);
  const [graphView, setGraphView] = useState<GraphView>("main");
  const [selected, setSelected] = useState<string | null>(null);
  // counts explicit picks (diagram node, timeline bar): the inspector scrolls to the node's records on each one,
  // also when the node that is already selected is picked again
  const [pick, setPick] = useState(0);
  const [follow, setFollow] = useState(true);
  const [showTimeline, setShowTimeline] = useState(true);
  const [showMap, setShowMap] = useState(false);
  const flow = useReactFlow();
  const { state, now } = view;
  const initialized = useRef(false);
  const openedAt = useRef(0);
  const overviewed = useRef(false);

  useEffect(() => {
    setSelected(null);
    setGraphView("main");
    initialized.current = false;
    overviewed.current = false;
  }, [runId]);

  // a finished run opens as an overview; a live run follows the active node
  useEffect(() => {
    if (overviewed.current || view.stream.events.length === 0) return;
    if (!initialized.current) {
      initialized.current = true;
      openedAt.current = performance.now();
      setFollow(!state.ended);
    }
    // a server may send the backlog of a finished run over several frames: the end arriving right after the
    // first events still means the run was already over when it was opened
    if (state.ended && performance.now() - openedAt.current < 1500) {
      overviewed.current = true;
      setFollow(false);
      window.setTimeout(() => void flow.fitView({ ...FIT, duration: 250 }), 60);
    }
  }, [view.stream.events.length, state.ended, flow]);

  const onDrill = useCallback((v: GraphView) => {
    setGraphView(v);
    setSelected(null);
  }, []);

  const pickNode = useCallback((id: string) => {
    setSelected(id);
    setPick((n) => n + 1);
  }, []);

  const { nodes, edges } = useMemo(() => buildFlow(graph, graphView, state, now, onDrill), [graph, graphView, state, now, onDrill]);
  const nodesWithSelection = useMemo(() => nodes.map((n) => (n.id === selected ? { ...n, selected: true } : n)), [nodes, selected]);

  // follow the active node while live
  const focus = focusNode(state, graphView);
  const lastFocus = useRef<string | null>(null);
  useEffect(() => {
    if (!follow || !focus || focus === lastFocus.current) return;
    lastFocus.current = focus;
    const c = nodeCenter(graph, focus);
    if (c) void flow.setCenter(c.x, c.y, { zoom: 0.8, duration: 450 });
  }, [follow, focus, graph, flow]);

  // fit when switching graph views
  useEffect(() => {
    lastFocus.current = null;
    const id = window.setTimeout(() => void flow.fitView({ ...FIT, duration: 300 }), 30);
    return () => window.clearTimeout(id);
  }, [graphView, flow]);

  return (
    <div className={cx("topo", !showTimeline && "no-timeline")}>
      <RunBar runId={runId} view={view} run={run} graph={graph} />
      <div className="topo-body">
        <div className="topo-canvas" aria-label="멀티에이전트 토폴로지">
          <ReactFlow
            nodes={nodesWithSelection}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            colorMode="dark"
            fitView
            fitViewOptions={FIT}
            minZoom={0.3}
            maxZoom={1.6}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            onNodeClick={(_, node) => pickNode(node.id)}
            onPaneClick={() => setSelected(null)}
            onMoveStart={(event) => {
              if (event) setFollow(false);
            }}
            proOptions={{ hideAttribution: false }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#2a3036" />
            <Controls showInteractive={false} position="bottom-right" />
            {showMap ? (
              <MiniMap pannable zoomable position="bottom-left" nodeColor={miniColor} maskColor="rgba(14,16,18,0.7)" style={{ width: 168, height: 104 }} />
            ) : null}
          </ReactFlow>
          <div className="canvas-tools">
            {graphView !== "main" ? (
              <button type="button" className="btn btn-sm btn-secondary" onClick={() => onDrill("main")}>
                <ArrowLeft size={14} aria-hidden="true" />
                전체 토폴로지
              </button>
            ) : null}
            <span className="canvas-crumb">{graphView === "main" ? "case 그래프" : "case › 지각 서브그래프"}</span>
            <button
              type="button"
              className={cx("btn btn-sm btn-secondary", follow && "is-on")}
              aria-pressed={follow}
              onClick={() => {
                lastFocus.current = null;
                setFollow((f) => !f);
              }}
              title="실행 중인 노드로 화면을 옮깁니다"
            >
              <Crosshair size={14} aria-hidden="true" />
              따라가기
            </button>
            <button type="button" className="btn btn-sm btn-secondary" onClick={() => void flow.fitView({ ...FIT, duration: 300 })}>
              <Maximize size={14} aria-hidden="true" />
              전체 보기
            </button>
            <button type="button" className={cx("btn btn-sm btn-secondary", showMap && "is-on")} aria-pressed={showMap} onClick={() => setShowMap((m) => !m)}>
              <MapIcon size={14} aria-hidden="true" />
              미니맵
            </button>
          </div>
          <Legend />
        </div>
        <Inspector
          runId={runId}
          graph={graph}
          state={state}
          now={now}
          selected={selected}
          pick={pick}
          // the record still moves: a live run that has not ended, or a replay whose end is the cursor
          advancing={view.mode === "replay" || !state.ended}
          onSelect={setSelected}
        />
      </div>
      <TaskTimeline state={state} now={now} selected={selected} onSelect={pickNode} open={showTimeline} onToggle={() => setShowTimeline((s) => !s)} />
    </div>
  );
}

function miniColor(node: { type?: string }): string {
  switch (node.type) {
    case "agent":
      return "#1f3a44";
    case "human":
      return "#6b5226";
    case "model":
      return "#4a4270";
    default:
      return "#3a424a";
  }
}

function Legend() {
  return (
    <div className="legend" aria-label="범례">
      <span>
        <i className="lg lg-running" />
        실행 중
      </span>
      <span>
        <i className="lg lg-waiting" />
        사람 응답 대기
      </span>
      <span>
        <i className="lg lg-done" />
        완료
      </span>
      <span>
        <i className="lg lg-cached" />
        캐시
      </span>
      <span>
        <i className="lg-line lg-cond" />
        조건 분기
      </span>
      <span>
        <i className="lg-line lg-retry" />
        재시도 · 되돌림
      </span>
    </div>
  );
}

function RunBar({ runId, view, run, graph }: { runId: string; view: RunView; run?: LabRun; graph: GraphSpec }) {
  const { state, now, mode, setMode, cursor, setCursor, playing, setPlaying, lastSeq, stream } = view;
  const waiting = state.waits.filter((w) => w.end === undefined).length;
  const title = state.title || run?.title;
  // What a person has to answer on the service screen, as of now (not as of the replay cursor). The list says it
  // (`waitingFor`, else its status), but the list is polled: a status the live stream already shows is newer.
  const liveStatus = mode === "live" ? state.status : null;
  const answer = !run || run.autopilot ? null : liveStatus && liveStatus !== run.status ? waitingOf(liveStatus) : awaitsPerson(run);
  return (
    <div className="runbar">
      <Link to="/lab/runs" className="runbar-back">
        <ArrowLeft size={14} aria-hidden="true" />
        실행 목록
      </Link>
      <div className="runbar-run">
        <h1 className="runbar-title" title={title}>
          {title ?? runId}
        </h1>
        {title ? <span className="runbar-id mono">{runId}</span> : null}
      </div>
      <RunStatus state={state} live={stream.connected} run={run} graph={graph} />
      {state.profile ? (
        state.profileId && !state.forkedFrom ? (
          <Link to={`/lab/profiles/${state.profileId}`} className="run-profile mono" title={`config_hash ${state.configHash}`}>
            {state.profile}
          </Link>
        ) : (
          <span className="run-profile mono" title={`config_hash ${state.configHash}`}>
            {state.profile}
          </span>
        )
      ) : null}
      <dl className="run-metrics num">
        <div>
          <dt>경과</dt>
          <dd>{clock(now)}</dd>
        </div>
        <div>
          <dt>비용</dt>
          <dd>
            {usd(state.costUsd, 3)} <span className="muted">/ {usd(state.costCapUsd)}</span>
          </dd>
        </div>
        <div>
          <dt>LLM 호출</dt>
          <dd>{state.llmCalls}</dd>
        </div>
        <div>
          <dt>도구 호출</dt>
          <dd>{state.toolCalls}</dd>
        </div>
        <div>
          <dt>사람 대기</dt>
          <dd className={cx(waiting > 0 && "is-waiting")}>{waiting > 0 ? `${waiting}건` : "없음"}</dd>
        </div>
      </dl>
      <div className="run-mode">
        <div className="segmented" role="radiogroup" aria-label="보기 방식">
          <label>
            <input type="radio" name="run-mode" checked={mode === "live"} onChange={() => setMode("live")} />
            <Radio size={13} aria-hidden="true" />
            실시간
          </label>
          <label>
            <input type="radio" name="run-mode" checked={mode === "replay"} onChange={() => setMode("replay")} disabled={lastSeq === 0} />
            <RotateCcw size={13} aria-hidden="true" />
            재생
          </label>
        </div>
        {mode === "replay" ? (
          <div className="replay">
            <button type="button" className="btn btn-sm btn-secondary btn-icon" onClick={() => {
              if (!playing && cursor >= lastSeq) setCursor(1);
              setPlaying(!playing);
            }} aria-label={playing ? "일시정지" : "재생"}>
              {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
            </button>
            <input
              type="range"
              min={1}
              max={Math.max(1, lastSeq)}
              value={Math.max(1, cursor)}
              onChange={(e) => {
                setPlaying(false);
                setCursor(Number(e.target.value));
              }}
              aria-label="이벤트 위치"
            />
            <span className="mono num replay-pos">
              #{cursor}/{lastSeq}
            </span>
          </div>
        ) : null}
      </div>
      <div className="runbar-actions">
        {run?.experimentId ? (
          <Link to={experimentPath(run.experimentId)} className="btn btn-sm btn-ghost">
            {isStageExperimentId(run.experimentId) ? "구간 실험 보기" : "평가 실험 보기"}
          </Link>
        ) : null}
        {/* a run that answers itself has no service screen; one that waits for a person is answered there */}
        {run && !run.autopilot ? (
          <Link to={`/cases/${runId}`} className={cx("btn btn-sm", answer ? "btn-primary" : "btn-ghost")}>
            {answer ? ANSWER_LABEL[answer] : "서비스 화면"}
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function RunStatus({ state, live, run, graph }: { state: RunState; live: boolean; run?: LabRun; graph: GraphSpec }) {
  const label = state.status ? STATUS_LABEL[state.status] : "시작 전";
  const paused = state.status === "PAUSED";
  const tone = state.status === "COMPLETED" ? "done" : state.status === "FAILED" ? "error" : paused || state.status?.startsWith("AWAITING") ? "waiting" : "running";
  return (
    <span className={cx("run-status", `tone-${tone}`)}>
      <span className={cx("pulse", live && !state.ended && !paused && "is-live")} aria-hidden="true" />
      {label}
      {state.forkedFrom ? (
        <span className="muted">
          {" "}
          · {state.forkedFrom}
          {run?.forkedAt ? `의 ‘${nodeLabel(graph, run.forkedAt)}’ 앞 체크포인트에서 이어 감` : "에서 분기"}
        </span>
      ) : null}
      {run?.stoppedAfter ? <span className="muted"> · ‘{nodeLabel(graph, run.stoppedAfter)}’까지만 실행</span> : null}
      {run?.variant ? <span className="muted"> · 조합: {run.variant}</span> : null}
    </span>
  );
}
