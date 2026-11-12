import { Component, type ReactNode, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Activity, Check, Pause } from "lucide-react";
import { isNotFound } from "../api/client";
import type { CaseDetail, CaseStatus, RunEvent } from "../api/contract";
import { useBackendKind, useCase, useCaseLive, usePauseCase } from "../api/hooks";
import { STATUS_LABEL, isRunning, needsUser, seconds, usd } from "../lib/format";
import { EmptyState, ErrorState, Meter, Skeleton, Spinner, cx } from "../ui";
import { FrameStill } from "./media/FrameStill";
import { SceneView } from "./scene/SceneView";
import { recallThumbnail } from "./upload";
import { AnalyzingStage } from "./stages/Analyzing";
import { IdentityStage } from "./stages/Identity";
import { QuestionsStage } from "./stages/Questions";
import { ReviewStage } from "./stages/Review";
import { AuditStage } from "./stages/Audit";
import { PartialStage } from "./stages/Partial";
import { PausedStage } from "./stages/Paused";
import { RenderingStage } from "./stages/Rendering";
import { ResultStage } from "./stages/Result";

type StepId = "upload" | "analyze" | "identity" | "legal" | "review" | "audit" | "result";

const STEPS: { id: StepId; label: string }[] = [
  { id: "upload", label: "업로드" },
  { id: "analyze", label: "영상 분석" },
  { id: "identity", label: "대상 확인" },
  { id: "legal", label: "법적 판단" },
  { id: "review", label: "계획 검토" },
  { id: "audit", label: "감사" },
  { id: "result", label: "결과" },
];

const STEP_OF: Record<CaseStatus, StepId> = {
  QUEUED: "analyze",
  ANALYZING: "analyze",
  AWAITING_IDENTITY: "identity",
  ANALYZING_LEGAL: "legal",
  AWAITING_ANSWERS: "legal",
  PLANNING: "legal",
  AWAITING_REVIEW: "review",
  AUDITING: "audit",
  AWAITING_AUDIT_CONFIRM: "audit",
  RENDERING: "result",
  COMPLETED: "result",
  FAILED: "result",
  PAUSED: "analyze", // a paused case stays on the step it stopped in: see stepOf
};

/** the status a paused run had when it stopped: the last one the event stream reported before the pause */
function statusBeforePause(events: RunEvent[]): CaseStatus | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (e.kind === "run.status" && e.status !== "PAUSED") return e.status;
  }
  return null;
}

/** the step a case is on. A paused case keeps the step it was in; without the event stream, the data it has says how far it got. */
function stepOf(c: CaseDetail, was: CaseStatus | null): StepId {
  if (c.status !== "PAUSED") return STEP_OF[c.status];
  if (was) return STEP_OF[was];
  if (c.report || c.renderSteps.length) return "result";
  if (c.findings.length) return "audit";
  if (c.plan) return "review";
  if (c.perception) return "legal";
  return "analyze";
}

/**
 * A run that covered only part of the pipeline and ended there (a Lab section experiment): COMPLETED, but with
 * no output and no report. It has no result screen.
 */
function isPartial(c: CaseDetail): boolean {
  return c.status === "COMPLETED" && (!!c.stoppedAfter || !c.report);
}

/** the graph node a partial run ended after → the step that node belongs to */
const STEP_ENDED: Record<string, StepId> = { perceive: "analyze", legal_analyst: "legal", review_guide: "legal", auditor: "audit" };

/** the last step a partial run finished: the node it ended after, or else what it has says how far it went */
function lastStepOf(c: CaseDetail): StepId {
  const named = c.stoppedAfter ? STEP_ENDED[c.stoppedAfter] : undefined;
  return named ?? (c.plan || c.requirements.length || c.analyses.length ? "legal" : "analyze");
}

/** a stage that cannot be drawn says so in its place, and the rest of the page stays */
class StageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return <ErrorState error={new Error(`이 단계의 화면을 그리지 못했습니다. (${error.message})`)} onRetry={() => this.setState({ error: null })} />;
  }
}

export function CasePage() {
  const { caseId } = useParams();
  const query = useCase(caseId);
  const stream = useCaseLive(caseId);
  const backend = useBackendKind();

  if (query.isLoading) {
    return (
      <main className="case-page">
        <Skeleton lines={4} />
      </main>
    );
  }
  if (query.isError || !query.data) {
    const notFound = isNotFound(query.error);
    return (
      <main className="case-page">
        {notFound ? (
          <EmptyState
            title="작업을 찾을 수 없습니다"
            action={
              <Link to="/" className="btn btn-primary">
                새 작업 만들기
              </Link>
            }
          >
            {backend === "mock" ? "모의 백엔드는 개발 서버를 다시 시작하면 작업 목록을 비웁니다." : "주소가 바뀌었거나 지워진 작업입니다."}
          </EmptyState>
        ) : (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        )}
      </main>
    );
  }
  const c = query.data;
  const was = c.status === "PAUSED" ? statusBeforePause(stream.events) : null;
  const partial = isPartial(c);
  const step = partial ? lastStepOf(c) : stepOf(c, was);
  const stageKey = `${c.id}:${c.status === "PAUSED" ? "paused" : partial ? "partial" : step}`;
  return (
    <main className="case-page">
      <CaseHeader c={c} live={stream.connected} canPause={backend === "real"} />
      <Stepper c={c} current={step} ended={partial} />
      <div className="stage" key={stageKey}>
        <StageBoundary key={stageKey}>
          <StageView c={c} was={was} />
        </StageBoundary>
      </div>
    </main>
  );
}

function StageView({ c, was }: { c: CaseDetail; was: CaseStatus | null }) {
  switch (c.status) {
    case "AWAITING_IDENTITY":
      return <IdentityStage c={c} />;
    case "AWAITING_ANSWERS":
      return <QuestionsStage c={c} />;
    case "AWAITING_REVIEW":
      return <ReviewStage c={c} />;
    case "AWAITING_AUDIT_CONFIRM":
      return <AuditStage c={c} />;
    case "RENDERING":
      return <RenderingStage c={c} />;
    case "COMPLETED":
      return isPartial(c) ? <PartialStage c={c} /> : <ResultStage c={c} />;
    case "PAUSED":
      return <PausedStage c={c} was={was} />;
    case "FAILED":
      // output verification failed: the report exists and says which checks did not pass
      if (c.report && c.plan) return <ResultStage c={c} />;
      return (
        <ErrorState error={new Error(c.error ? `처리 중 오류가 발생했습니다: ${c.error}` : "처리 중 오류가 발생했습니다.")} />
      );
    default:
      return <AnalyzingStage c={c} />;
  }
}

function CaseThumb({ c }: { c: CaseDetail }) {
  if (c.media) return <FrameStill c={c} t={Math.min(1, c.video.durationSec / 3)} cover width={320} />;
  const thumb = recallThumbnail(c.id);
  return thumb ? <img src={thumb} alt="" /> : <SceneView compact t={12} mode="original" />;
}

/** asks a running case to stop; it pauses once the step it is in has finished */
function PauseButton({ c }: { c: CaseDetail }) {
  const pause = usePauseCase(c.id);
  const [asked, setAsked] = useState(false);
  const running = isRunning(c.status);
  useEffect(() => {
    if (!running) setAsked(false);
  }, [running]);
  if (!running) return null;
  return (
    <span className="pause-control">
      <button
        type="button"
        className="btn btn-sm btn-ghost"
        disabled={pause.isPending || asked}
        title="지금 하는 단계가 끝나면 멈춥니다. 멈춘 작업은 이어서 진행할 수 있습니다."
        onClick={() => pause.mutate(undefined, { onSuccess: () => setAsked(true) })}
      >
        {pause.isPending || asked ? <Spinner size={13} /> : <Pause size={14} aria-hidden="true" />}
        {asked ? "멈추는 중" : "멈춤"}
      </button>
      {pause.isError ? (
        <span className="pause-control-error" role="alert">
          {pause.error instanceof Error ? pause.error.message : "멈추지 못했습니다."}
        </span>
      ) : null}
    </span>
  );
}

function CaseHeader({ c, live, canPause }: { c: CaseDetail; live: boolean; canPause: boolean }) {
  const v = c.video;
  return (
    <header className="case-head">
      <div className="case-thumb">
        <CaseThumb c={c} />
      </div>
      <div className="case-title-block">
        <p className="eyebrow">
          {needsUser(c.status) ? <span className="attention">응답이 필요합니다 · </span> : null}
          {c.status === "PAUSED" ? <span className="attention">계속하려면 확인이 필요합니다 · </span> : null}
          {isPartial(c) ? "일부 구간만 실행" : STATUS_LABEL[c.status]}
        </p>
        <h1 className="case-title">{c.upload?.filename ?? c.title}</h1>
        <p className="case-specs num">
          {seconds(v.durationSec)} · {v.width}×{v.height} · {v.fps} fps · {v.codec} · {v.audio}
          {c.upload ? <span className="muted"> — 분석 결과는 예시 시나리오(카페 인터뷰) 기준</span> : null}
        </p>
      </div>
      <div className="case-head-side">
        {canPause ? <PauseButton c={c} /> : null}
        <div className="cost-box">
          <span className="cost-label">
            비용 <span className="num">{usd(c.costUsd)}</span> <span className="muted num">/ {usd(c.costCapUsd)}</span>
          </span>
          <Meter value={c.costUsd} max={c.costCapUsd} label="비용 상한 대비 사용량" />
        </div>
        {c.experimentId ? (
          <Link to={`/lab/experiments/${c.experimentId}`} className="btn btn-sm btn-secondary" title="이 작업은 구간 실험의 실행입니다. 답을 마치면 결과가 그 실험에 들어갑니다">
            구간 실험
          </Link>
        ) : null}
        <Link to={`/lab/runs/${c.id}`} className="btn btn-sm btn-secondary" title="이 작업의 에이전트 토폴로지와 메모리를 실험실에서 봅니다">
          <Activity size={14} aria-hidden="true" />
          실험실에서 보기
        </Link>
        <span className={cx("live-dot", live && "is-live")} title={live ? "실시간 연결됨" : "연결 대기"} aria-label={live ? "실시간 연결됨" : "연결 대기"} />
      </div>
    </header>
  );
}

/** `ended`: the run stopped for good after `current` (a partial run): the steps up to it are done and none is in progress */
function Stepper({ c, current, ended = false }: { c: CaseDetail; current: StepId; ended?: boolean }) {
  const currentIndex = STEPS.findIndex((s) => s.id === current);
  const identitySkipped = c.identity === null && currentIndex > STEPS.findIndex((s) => s.id === "identity");
  const paused = c.status === "PAUSED";
  const waiting = needsUser(c.status) || paused;
  return (
    <ol className="stepper" aria-label="진행 단계">
      {STEPS.map((step, i) => {
        const done = ended ? i <= currentIndex : i < currentIndex || (c.status === "COMPLETED" && step.id === "result");
        const isCurrent = !ended && i === currentIndex && c.status !== "COMPLETED";
        const skipped = step.id === "identity" && identitySkipped;
        return (
          <li
            key={step.id}
            className={cx("step", done && "is-done", isCurrent && "is-current", skipped && "is-skipped", isCurrent && waiting && "is-waiting")}
            aria-current={isCurrent ? "step" : undefined}
          >
            <span className="step-mark" aria-hidden="true">
              {done && !skipped ? <Check size={12} strokeWidth={3} /> : i + 1}
            </span>
            <span className="step-label">
              {step.label}
              {skipped ? <span className="step-sub">필요 없음</span> : null}
              {isCurrent && waiting ? <span className="step-sub">{paused ? "멈춤" : "응답 필요"}</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
