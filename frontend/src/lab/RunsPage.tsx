// The Lab's landing page: every run, one row each. A row opens that run's topology.
// Putting runs together and comparing them is the experiments menu's job, not this one's.
import { type MouseEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ChevronRight, GitBranch, UserRound } from "lucide-react";
import { useBackendKind, useGraph, useNoSample, useProfiles, useRuns, useStartDemo } from "../api/hooks";
import { type LabRun, experimentPath, isStageExperimentId } from "../api/stage";
import { STATUS_LABEL, relativeTime, seconds, usd } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { WAITING_LABEL, awaitsPerson, nodeLabel } from "./useRunView";

type Kind = "all" | "demo" | "experiment";

const KINDS: [Kind, string][] = [
  ["all", "전체"],
  ["demo", "서비스 · 데모"],
  ["experiment", "실험"],
];

const ofKind = (run: LabRun, kind: Kind) => kind === "all" || (kind === "experiment" ? !!run.experimentId : !run.experimentId);

export function RunsPage() {
  const runs = useRuns();
  const profiles = useProfiles();
  const startDemo = useStartDemo();
  const navigate = useNavigate();
  const [profileId, setProfileId] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const [waitingOnly, setWaitingOnly] = useState(false);
  const all = (runs.data ?? []) as LabRun[];
  const live = all.filter((r) => r.source === "live");
  const history = all.filter((r) => r.source === "history");
  const waiting = live.filter((r) => ofKind(r, kind) && awaitsPerson(r) !== null).length;
  const shown = live.filter((r) => ofKind(r, kind) && (!waitingOnly || awaitsPerson(r) !== null));
  const service = profiles.data?.find((p) => p.service.includes("default"));
  const noSample = useNoSample();
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <h1 className="lab-title">실행</h1>
          <p className="lab-lead">
            실행 하나를 찾아 여는 곳입니다. 행을 누르면 그 실행의 토폴로지에서 진행 상황과 메모리 · 이벤트 · 체크포인트를 따라갈 수 있습니다. 여러 실행을 구성하고 쌓아
            비교하는 일은 <Link to="/lab/experiments">실험</Link>에서 합니다.
          </p>
        </div>
        <div className="runs-start">
          <label className="inline-field">
            <span>프로필</span>
            <select className="select" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
              <option value="">{service ? `${service.name} (서비스 기본)` : "서비스 기본"}</option>
              {(profiles.data ?? [])
                .filter((p) => p.id !== service?.id)
                .map((p) => (
                  <option key={p.id} value={p.id} disabled={p.issues.some((i) => i.level === "error")}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary"
            disabled={startDemo.isPending || noSample}
            aria-describedby={noSample ? "no-sample-note" : undefined}
            onClick={() => startDemo.mutate(profileId || undefined, { onSuccess: (r) => navigate(`/lab/runs/${r.id}`) })}
          >
            {startDemo.isPending ? <Spinner /> : null}
            데모 실행 시작
          </button>
        </div>
      </header>
      {noSample ? (
        <p id="no-sample-note" className="lab-note">
          서버에 예시 영상이 없어 데모 실행을 시작할 수 없습니다. <span className="mono">uv run privframe-case make-sample</span>로 예시 영상을 만들거나, 서비스에서
          영상을 올리면 그 작업이 여기에 실행으로 나타납니다.
        </p>
      ) : null}
      {startDemo.isError ? <ErrorState error={startDemo.error} /> : null}
      {runs.isLoading ? <Skeleton lines={5} /> : null}
      {runs.isError ? <ErrorState error={runs.error} onRetry={() => void runs.refetch()} /> : null}
      {runs.data ? (
        <>
          <section className="lab-section" aria-labelledby="runs-list-title">
            <h2 id="runs-list-title" className="sr-only">
              실행 목록
            </h2>
            <div className="runs-filter">
              <div className="segmented" role="radiogroup" aria-label="실행 종류">
                {KINDS.map(([value, label]) => (
                  <label key={value}>
                    <input type="radio" name="run-filter" checked={kind === value} onChange={() => setKind(value)} />
                    {label}
                    <span className="runs-count num">{live.filter((r) => ofKind(r, value)).length}</span>
                  </label>
                ))}
              </div>
              <label className={cx("runs-waiting", waiting > 0 && "has-some")}>
                <input type="checkbox" checked={waitingOnly} disabled={waiting === 0 && !waitingOnly} onChange={(e) => setWaitingOnly(e.target.checked)} />
                응답 대기만
                <span className="runs-count num">{waiting}</span>
              </label>
            </div>
            {shown.length ? (
              <RunTable runs={shown} />
            ) : live.length ? (
              <EmptyState
                title="조건에 맞는 실행이 없습니다"
                action={
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={() => {
                      setKind("all");
                      setWaitingOnly(false);
                    }}
                  >
                    전체 보기
                  </button>
                }
              >
                {waitingOnly ? "사람의 응답을 기다리는 실행이 지금은 없습니다." : "이 종류의 실행이 아직 없습니다."}
              </EmptyState>
            ) : (
              <EmptyState title="아직 실행이 없습니다">
                {noSample
                  ? "서비스에서 영상을 올리면 그 작업이 여기에 실행으로 나타납니다."
                  : "서비스에서 작업을 만들거나, 위의 데모 실행을 시작하면 여기에 나타납니다."}
              </EmptyState>
            )}
          </section>
          {/* the summaries are example rows of the M2 mock; a real backend lists only runs it has events for */}
          {history.length ? (
            <section className="lab-section">
              <h2 className="lab-section-title">평가 실행 요약 {history.length}</h2>
              <p className="lab-note">평가 실험(베이스라인 비교)에서 돌린 실행의 요약입니다. 이벤트 기록은 LangSmith에 있어 토폴로지로 열 수 없고, 수치는 예시입니다.</p>
              <RunTable runs={history} summaries />
            </section>
          ) : null}
        </>
      ) : null}
    </main>
  );
}

/** `summaries`: rows without an event record (the mock's example evaluation runs). They open nothing here. */
function RunTable({ runs, summaries = false }: { runs: LabRun[]; summaries?: boolean }) {
  const graph = useGraph();
  const navigate = useNavigate();
  const real = useBackendKind() === "real";

  // The title is the row's link; a click anywhere else in the row follows it, unless it landed on another
  // control of the row or finished a text selection.
  const open = (event: MouseEvent<HTMLTableRowElement>, id: string) => {
    if (event.defaultPrevented || (event.target as HTMLElement).closest("a, button, input, select, label")) return;
    if (window.getSelection()?.toString()) return;
    const to = `/lab/runs/${id}`;
    if (event.metaKey || event.ctrlKey) window.open(to, "_blank", "noopener");
    else navigate(to);
  };

  return (
    <div className="table-wrap">
      <table className={cx("table lab-table runs-table", summaries && "is-summaries")}>
        <thead>
          <tr>
            <th scope="col">실행</th>
            <th scope="col">프로필</th>
            <th scope="col">상태</th>
            <th scope="col">시작</th>
            <th scope="col" className="num">
              경과
            </th>
            <th scope="col" className="num">
              비용
            </th>
            <th scope="col" className="num">
              LLM
            </th>
            <th scope="col" className="num">
              도구
            </th>
            <th scope="col" className="num">
              질문
            </th>
            {summaries ? (
              <>
                <th scope="col" className="num">
                  정책 F1
                </th>
                <th scope="col">
                  <span className="sr-only">기록</span>
                </th>
              </>
            ) : (
              <td aria-hidden="true" />
            )}
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const waits = awaitsPerson(r);
            return (
              <tr key={r.id} className={cx(!summaries && "run-row", waits && "is-waiting")} onClick={summaries ? undefined : (e) => open(e, r.id)}>
                <th scope="row">
                  <div className="run-cell">
                    {summaries ? (
                      <span className="run-title">{r.title}</span>
                    ) : (
                      <Link to={`/lab/runs/${r.id}`} className="run-title run-link">
                        {r.title}
                      </Link>
                    )}
                    <span className="mono muted small">
                      {r.id}
                      {r.interactive ? " · 직접 응답" : r.autopilot && !summaries && !r.experimentId ? " · 자동 응답" : ""}
                    </span>
                    {r.experimentId || r.forkedFrom || r.stoppedAfter ? (
                      <div className="run-notes small">
                        {r.experimentId ? (
                          <span className="run-lineage" data-lineage="experiment">
                            <Link to={experimentPath(r.experimentId)}>
                              {isStageExperimentId(r.experimentId) ? "구간 실험" : "평가 실험"} <span className="mono">{r.experimentId}</span>
                            </Link>
                            {r.variant ? <span data-lineage="variant">조합: {r.variant}</span> : null}
                          </span>
                        ) : null}
                        {r.forkedFrom ? (
                          <span className="run-lineage" data-lineage="fork">
                            <GitBranch size={11} aria-hidden="true" />
                            <Link to={`/lab/runs/${r.forkedFrom}`} className="mono">
                              {r.forkedFrom}
                            </Link>
                            {/* without an entry node the run did not continue a checkpoint: the real backend then ran the video again */}
                            {r.forkedAt ? (
                              <>
                                의 &lsquo;{nodeLabel(graph.data, r.forkedAt)}&rsquo; 앞 체크포인트에서 이어 감
                              </>
                            ) : real ? (
                              "에서 분기 · 처음부터 다시 실행"
                            ) : (
                              "에서 분기"
                            )}
                          </span>
                        ) : null}
                        {r.stoppedAfter ? (
                          <span className="run-lineage" data-lineage="stop">
                            &lsquo;{nodeLabel(graph.data, r.stoppedAfter)}&rsquo;까지만 실행하고 마침
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </th>
                <td className="mono runs-profile">
                  {r.profileId && !summaries && !r.profile.includes(" · ") ? <Link to={`/lab/profiles/${r.profileId}`}>{r.profile}</Link> : r.profile}
                </td>
                <td>
                  {waits ? (
                    <span className="status-text is-waiting">
                      <UserRound size={13} aria-hidden="true" />
                      {WAITING_LABEL[waits]}
                    </span>
                  ) : (
                    <span className={cx("status-text", r.live && "is-live", r.status === "FAILED" && "is-failed")}>
                      {r.live ? <span className="pulse is-live" aria-hidden="true" /> : null}
                      {STATUS_LABEL[r.status]}
                    </span>
                  )}
                </td>
                <td className="muted">{relativeTime(r.startedAt)}</td>
                <td className="num">{seconds(r.elapsedSec)}</td>
                <td className="num">{usd(r.costUsd, 3)}</td>
                <td className="num">{r.llmCalls}</td>
                <td className="num">{r.toolCalls}</td>
                <td className="num">{r.questions}</td>
                {summaries ? (
                  <>
                    <td className="num">{r.policyF1 !== undefined ? r.policyF1.toFixed(2) : "—"}</td>
                    <td>
                      <a className="btn btn-sm btn-ghost" href="https://smith.langchain.com/" target="_blank" rel="noreferrer">
                        LangSmith
                      </a>
                    </td>
                  </>
                ) : (
                  <td className="run-open" aria-hidden="true">
                    <ChevronRight size={15} />
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
