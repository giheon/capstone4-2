import { useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FileText,
  GitBranch,
  Image,
  Layers3,
  RefreshCw,
  ScanFace,
  Shield,
  SlidersHorizontal,
  Video,
} from "lucide-react";
import { artifactUrl, externalUrl, post, saveOverrides } from "./api";
import {
  Badge,
  Empty,
  ErrorNotice,
  FixtureBadge,
  FixtureNotice,
  formatDate,
  Loading,
  Panel,
  ProfileDetails,
  ProfileSelect,
  seconds,
} from "./components";
import {
  actionNames,
  busyStatuses,
  indexMatchesProfile,
  labelNames,
  profileNames,
} from "./types";
import type {
  Action,
  Evidence,
  IndexRecord,
  Profile,
  Run,
  Track,
  Treatment,
  VideoRecord,
} from "./types";

const reviewStatuses = new Set(["REVIEW_READY", "REVIEW_REQUIRED"]);
const actionList: Action[] = ["KEEP", "BLUR", "MOSAIC", "MASK"];

export default function RunWorkspace({
  run,
  profiles,
  indexes,
  allRuns,
  video,
  onRun,
}: {
  run: Run;
  profiles: Profile[];
  indexes: IndexRecord[];
  allRuns: Run[];
  video?: VideoRecord;
  onRun: (run: Run) => void;
}) {
  const client = useQueryClient();
  const [forkOpen, setForkOpen] = useState(false);
  const update = (result: Run) => {
    client.setQueryData(["run", result.id], result);
    void client.invalidateQueries({ queryKey: ["runs"] });
  };
  const retry = useMutation({
    mutationFn: (stage: string) =>
      post<Run>(`/runs/${run.id}/retry`, { stage }),
    onSuccess: update,
  });
  const report = useMutation({
    mutationFn: () => post<Run>(`/runs/${run.id}/report`),
    onSuccess: update,
  });
  const revise = useMutation({
    mutationFn: () => post<Run>(`/runs/${run.id}/revise`),
    onSuccess: (result) => {
      update(result);
      setForkOpen(false);
      onRun(result);
    },
  });
  const reusesReview = Boolean(
    run.reused_stages?.analyze && run.reused_stages?.decide,
  );
  const tracks = run.observation?.tracks ?? [];
  const lastFailed = [...(run.stages ?? [])]
    .reverse()
    .find((stage) => stage.status === "FAILED");
  const retryStage =
    lastFailed?.stage === "decide" || lastFailed?.stage === "render"
      ? lastFailed.stage
      : "analyze";
  const parent = allRuns.find((item) => item.id === run.parent_id);
  const differences = parent
    ? [
        ...new Set([...Object.keys(parent.config), ...Object.keys(run.config)]),
      ].filter(
        (key) =>
          JSON.stringify(parent.config[key]) !==
          JSON.stringify(run.config[key]),
      )
    : [];
  const step =
    run.status === "COMPLETED" || run.status === "RENDERING"
      ? 3
      : reviewStatuses.has(run.status ?? "QUEUED") || run.status === "DECIDING"
        ? 2
        : run.status === "AWAITING_LABELS"
          ? 1
          : 0;
  return (
    <>
      <div className="run-overview">
        <div className="run-title">
          <span className="video-symbol">
            <Video size={23} />
          </span>
          <div>
            <h2>{video?.filename ?? "영상 실험"}</h2>
            <span>
              {formatDate(run.created_at)} ·{" "}
              {profileNames[String(run.config.name)] ?? String(run.config.name)}
              {reusesReview
                ? " · 처리 수정"
                : run.parent_id
                  ? " · 비교 실행"
                  : ""}
            </span>
          </div>
        </div>
        <div className="run-actions">
          {run.config.mode === "fixture" && <FixtureBadge />}
          <Badge status={run.status} />
          <button
            className="button secondary small"
            onClick={() => setForkOpen(!forkOpen)}
          >
            <GitBranch size={15} />
            구성 바꿔 비교
          </button>
        </div>
      </div>
      {run.config.mode === "fixture" && <FixtureNotice />}
      {reusesReview && (
        <div className="notice info">
          <Copy size={18} />
          <span>
            <strong>이전 실행의 분석·검색 근거·판정을 재사용합니다.</strong>
            <br />
            얼굴별 처리만 수정해 새 결과를 출력합니다. 이전 영상과 보고서는 실행
            기록에 그대로 보존됩니다.
          </span>
        </div>
      )}
      <div className="progress-strip" aria-label="실행 진행 단계">
        {["영상 분석", "얼굴 확인", "처리 판정", "영상 · 보고서"].map(
          (title, position) => (
            <div
              key={title}
              className={`${position < step || run.status === "COMPLETED" ? "done" : ""} ${position === step && run.status !== "COMPLETED" ? "current" : ""}`}
            >
              <span>
                {position < step || run.status === "COMPLETED" ? (
                  <Check size={15} />
                ) : (
                  position + 1
                )}
              </span>
              <strong>{title}</strong>
              {position < 3 && <ChevronRight size={16} />}
            </div>
          ),
        )}
      </div>
      {forkOpen && (
        <ForkPanel
          run={run}
          profiles={profiles}
          indexes={indexes}
          onFork={onRun}
          onClose={() => setForkOpen(false)}
        />
      )}
      {run.status === "FAILED" && (
        <div className="failure-panel">
          <div>
            <ErrorNotice
              error={
                run.error ?? lastFailed?.error ?? "실행 중 오류가 발생했습니다."
              }
            />
            <p>
              이전 시도의 기록은 유지됩니다. 문제를 확인한 뒤 실패한 단계부터
              다시 실행할 수 있습니다.
            </p>
          </div>
          <button
            className="button secondary"
            disabled={retry.isPending}
            onClick={() => retry.mutate(retryStage)}
          >
            <RefreshCw size={16} />
            {retry.isPending ? "재시도 요청 중…" : "실패 단계 재시도"}
          </button>
          <ErrorNotice error={retry.error} />
        </div>
      )}
      {busyStatuses.has(run.status ?? "QUEUED") && (
        <div className="working-panel" aria-live="polite">
          <Loading
            text={
              run.status === "RENDERING"
                ? "지정한 설정으로 영상을 생성하고 있습니다."
                : run.status === "DECIDING"
                  ? "입력과 얼굴 관계로 근거를 검색하고 선택한 모델이 판정하고 있습니다."
                  : "영상에서 얼굴과 등장 구간을 분석하고 있습니다."
            }
          />
          <p>
            이 화면을 벗어나도 실행은 계속됩니다. 실행 기록에서 다시 확인할 수
            있습니다.
          </p>
        </div>
      )}
      <div className="two-column run-layout">
        <div className="stack">
          <Panel
            title="입력한 상황"
            subtitle="이 실행에서 사용한 입력과 구성입니다."
            accessory={<Shield size={19} className="muted" />}
          >
            <p className="context-text">{run.scenario.context}</p>
            {(run.observation?.working_artifact || video?.artifact) && (
              <details className="details-block input-preview">
                <summary>입력 영상 확인</summary>
                <video
                  controls
                  preload="none"
                  className="result-video"
                  src={artifactUrl(
                    run.observation?.working_artifact ?? video!.artifact,
                  )}
                  aria-label="입력 영상"
                />
              </details>
            )}
            {Object.keys(run.scenario.facts ?? {}).length > 0 && (
              <dl className="facts-list">
                {Object.entries(run.scenario.facts ?? {}).map(
                  ([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{value}</dd>
                    </div>
                  ),
                )}
              </dl>
            )}
            <ProfileDetails config={run.config} />
            <details className="details-block">
              <summary>실행 구성과 식별자 확인</summary>
              <code>{run.id}</code>
              <pre>{JSON.stringify(run.config, null, 2)}</pre>
              {parent && (
                <>
                  <h3>이전 실행과 달라진 구성</h3>
                  {differences.length ? (
                    <dl className="diff-list">
                      {differences.map((key) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>
                            <span>{String(parent.config[key] ?? "없음")}</span>
                            <ArrowRight size={12} />
                            <b>{String(run.config[key] ?? "없음")}</b>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : reusesReview ? (
                    <p>
                      이전 분석·검색 근거·판정을 그대로 사용한 처리 수정
                      실행입니다.
                    </p>
                  ) : (
                    <p>
                      실험 구성은 같습니다. 입력 상황 또는 자료 묶음을 비교할 수
                      있습니다.
                    </p>
                  )}
                </>
              )}
            </details>
          </Panel>
          {run.observation && (
            <TrackReview
              key={`${run.id}:${run.status}`}
              run={run}
              update={update}
            />
          )}
          {run.render && (
            <Panel
              title="출력 결과"
              subtitle={
                run.manual_output
                  ? "사용자 지정으로 생성한 실험 출력입니다. 원래 검토 사유는 유지됩니다."
                  : "실제 실행한 처리 설정과 결과를 확인하세요."
              }
              accessory={<CheckCircle2 size={20} className="success-text" />}
            >
              <video
                controls
                preload="metadata"
                className="result-video"
                src={artifactUrl(run.render.artifact)}
                aria-label="처리 결과 영상"
              />
              <div className="result-meta">
                <span>{run.render.frame_count}개 프레임</span>
                <span>
                  {run.render.has_audio ? "오디오 포함" : "오디오 없음"}
                </span>
                {run.manual_output && (
                  <span className="badge warning">사용자 지정 출력</span>
                )}
              </div>
              <div className="download-row">
                <a
                  className="button primary"
                  href={artifactUrl(run.render.artifact)}
                  download
                >
                  <Download size={16} />
                  영상 다운로드
                </a>
                {run.reports?.html && (
                  <a
                    className="button secondary"
                    href={artifactUrl(run.reports?.html)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText size={16} />
                    보고서 보기
                  </a>
                )}
                {run.reports?.json && (
                  <a
                    className="button secondary"
                    href={artifactUrl(run.reports?.json)}
                    download
                  >
                    <Download size={16} />
                    실행 기록 JSON
                  </a>
                )}
              </div>
              {run.status === "COMPLETED" && (
                <div className="notice info">
                  <div>
                    <button
                      className="button secondary"
                      disabled={revise.isPending || report.isPending}
                      onClick={() => revise.mutate()}
                    >
                      <SlidersHorizontal size={16} />
                      {revise.isPending
                        ? "수정 실행 준비 중…"
                        : "처리만 수정해 다시 출력"}
                    </button>
                    <p>
                      기존 분석·검색 근거·판정을 재사용해 처리 방법과 강도를
                      비교합니다. 현재 영상과 보고서는 별도 실행 기록으로
                      보존됩니다.
                    </p>
                  </div>
                </div>
              )}
              <ErrorNotice error={revise.error} />
              <button
                className="text-button"
                disabled={report.isPending || revise.isPending}
                onClick={() => report.mutate()}
              >
                <RefreshCw size={14} />
                {report.isPending
                  ? "보고서 생성 중…"
                  : "저장된 기록으로 보고서 다시 만들기"}
              </button>
              <ErrorNotice error={report.error} />
              <details className="details-block">
                <summary>실제로 적용된 처리 설정</summary>
                <pre>
                  {JSON.stringify(
                    {
                      treatments: run.render.treatments,
                      parameters: run.render.parameters,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </Panel>
          )}
        </div>
        <div className="stack">
          <EvidencePanel run={run} />
          {!run.render && (
            <RunReportPanel
              run={run}
              pending={report.isPending}
              error={report.error}
              regenerate={() => report.mutate()}
            />
          )}
          <Panel
            title="실행 진행 기록"
            subtitle="실패 및 재시도 기록도 함께 표시합니다."
            accessory={<Layers3 size={19} className="muted" />}
          >
            <div className="stage-list">
              {(run.stages ?? []).length ? (
                (run.stages ?? []).map((stage) => (
                  <div
                    className={`stage-row ${stage.status === "FAILED" ? "failed" : ""}`}
                    key={stage.id}
                  >
                    <span className="stage-dot" />
                    <div>
                      <strong>
                        {(
                          {
                            analyze: "영상 분석",
                            decide: "처리 판정",
                            render: "영상 생성",
                            report: "보고서 생성",
                          } as Record<string, string>
                        )[stage.stage] ?? stage.stage}
                      </strong>
                      <span>
                        {stage.status === "FAILED"
                          ? "실패"
                          : stage.status === "COMPLETED" ||
                              stage.status === "SUCCESS"
                            ? "완료"
                            : stage.status === "RUNNING"
                              ? "진행 중"
                              : stage.status}{" "}
                        ·{" "}
                        {stage.elapsed_seconds == null
                          ? formatDate(stage.started_at)
                          : `${stage.elapsed_seconds.toFixed(1)}초`}
                      </span>
                      {stage.error && <p>{stage.error}</p>}
                    </div>
                  </div>
                ))
              ) : reusesReview ? (
                <Empty>
                  분석과 판정은 이전 기록을 재사용했습니다. 출력하면 영상 생성
                  단계만 새로 기록됩니다.
                </Empty>
              ) : (
                <Empty>작업이 시작되면 단계별 진행 기록이 나타납니다.</Empty>
              )}
            </div>
            {run.observation && (
              <div className="observation-meta">
                <span>
                  얼굴 <b>{tracks.length}개</b>
                </span>
                <span>
                  길이 <b>{seconds(run.observation.duration)}</b>
                </span>
                <span>
                  해상도{" "}
                  <b>
                    {run.observation.width} × {run.observation.height}
                  </b>
                </span>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}

function TrackReview({
  run,
  update,
}: {
  run: Run;
  update: (run: Run) => void;
}) {
  const tracks = run.observation!.tracks;
  const [labels, setLabels] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      tracks.map((track) => [
        track.id,
        run.scenario.track_labels?.[track.id] ?? "unknown",
      ]),
    ),
  );
  const [overrides, setOverrides] = useState<Record<string, Treatment>>(
    run.overrides ?? {},
  );
  const [confirmed, setConfirmed] = useState(run.no_faces_confirmed ?? false);
  const [bulk, setBulk] = useState<Action>("MASK");
  const [bulkStrength, setBulkStrength] = useState(3);
  const plan = run.decision?.plan;
  const mustSpecifyAll =
    run.status === "REVIEW_REQUIRED" ||
    (run.review_reasons ?? []).length > 0 ||
    plan?.needs_review ||
    !!plan?.missing_facts.length;
  const missing = tracks.filter((track) =>
    mustSpecifyAll
      ? !overrides[track.id]
      : !overrides[track.id] &&
        !plan?.decisions.some((decision) => decision.track_id === track.id),
  );
  const editable = reviewStatuses.has(run.status ?? "QUEUED");
  const decide = useMutation({
    mutationFn: async () => {
      await post<Run>(`/runs/${run.id}/labels`, { track_labels: labels });
      return post<Run>(`/runs/${run.id}/decide`);
    },
    onSuccess: update,
  });
  const render = useMutation({
    mutationFn: async () => {
      await saveOverrides(run.id, overrides, confirmed);
      return post<Run>(`/runs/${run.id}/render`);
    },
    onSuccess: update,
  });
  const applyAll = () =>
    setOverrides(
      Object.fromEntries(
        tracks.map((track) => [
          track.id,
          { action: bulk, strength: bulk === "KEEP" ? null : bulkStrength },
        ]),
      ),
    );
  return (
    <Panel
      title={
        run.status === "AWAITING_LABELS"
          ? "검출된 얼굴을 확인하세요"
          : "얼굴별 처리 검토"
      }
      subtitle={`${tracks.length}개 얼굴 · 썸네일과 등장 구간으로 대상을 확인하세요.`}
      accessory={<ScanFace size={20} className="muted" />}
    >
      {mustSpecifyAll && (
        <div className="notice warning" role="status">
          <Eye size={18} />
          <span>
            <strong>
              {run.status === "COMPLETED"
                ? "검토가 필요한 판정을 사용자 지정으로 출력했습니다."
                : "판정을 확정할 수 없어 사용자 검토가 필요합니다."}
            </strong>
            <br />
            {run.status === "COMPLETED"
              ? "원래 판정과 검토 사유, 실제 적용한 설정이 보고서에 남아 있습니다."
              : "모든 얼굴의 처리 방법을 직접 지정하면 실험 출력을 만들 수 있습니다. 원래 검토 사유는 보고서에 남습니다."}
          </span>
        </div>
      )}
      {(run.review_reasons?.length ?? 0) > 0 && (
        <ul className="review-reasons">
          {run.review_reasons!.map((reason, index) => (
            <li key={index}>{reason.replace(/^[A-Z_]+:\s*/, "")}</li>
          ))}
        </ul>
      )}
      {plan?.missing_facts.length ? (
        <div className="missing-facts">
          <strong>부족한 정보</strong>
          <ul>
            {plan.missing_facts.map((fact, index) => (
              <li key={index}>{fact}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {plan?.summary && <p className="decision-summary">{plan.summary}</p>}
      {run.decision?.error && (
        <div className="notice warning">{run.decision.error}</div>
      )}
      {!tracks.length && (
        <>
          <Empty>
            <ScanFace size={29} />
            <strong>얼굴이 검출되지 않았습니다.</strong>
            <p>
              검출 결과는 영상에 식별정보가 없다는 확인을 의미하지 않습니다.
            </p>
          </Empty>
          {editable && (
            <label className="check-confirm">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              검출 결과를 확인했고, 원본 그대로 실험 출력합니다.
            </label>
          )}
        </>
      )}
      {editable && tracks.length > 0 && (
        <div className="bulk-toolbar">
          <span>
            <SlidersHorizontal size={16} />
            전체 사용자 지정
          </span>
          <select
            aria-label="전체 처리 방법"
            value={bulk}
            onChange={(event) => setBulk(event.target.value as Action)}
          >
            {actionList.map((action) => (
              <option key={action} value={action}>
                {actionNames[action]}
              </option>
            ))}
          </select>
          <select
            aria-label="전체 처리 강도"
            value={bulkStrength}
            disabled={bulk === "KEEP"}
            onChange={(event) => setBulkStrength(Number(event.target.value))}
          >
            {[1, 2, 3].map((strength) => (
              <option key={strength} value={strength}>
                강도 {strength}
              </option>
            ))}
          </select>
          <button className="button secondary small" onClick={applyAll}>
            전체 적용
          </button>
        </div>
      )}
      <div className="track-grid">
        {tracks.map((track) => {
          const decision = plan?.decisions.find(
            (item) => item.track_id === track.id,
          );
          const override = overrides[track.id];
          return (
            <TrackCard key={track.id} track={track}>
              <label className="field compact">
                이 얼굴의 관계
                <select
                  aria-label={`${track.id} 관계`}
                  disabled={run.status !== "AWAITING_LABELS"}
                  value={labels[track.id]}
                  onChange={(event) =>
                    setLabels({ ...labels, [track.id]: event.target.value })
                  }
                >
                  {Object.entries(labelNames).map(([key, name]) => (
                    <option key={key} value={key}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              {decision && (
                <div className="model-treatment">
                  <span>모델 제안</span>
                  <strong>
                    {actionNames[decision.treatment.action]}
                    {decision.treatment.strength
                      ? ` · 강도 ${decision.treatment.strength}`
                      : ""}
                  </strong>
                  <p>{decision.reason}</p>
                </div>
              )}
              {editable && (
                <div className="treatment-selects">
                  <label className="field compact">
                    사용자 지정
                    <select
                      aria-label={`${track.id} 처리 방법`}
                      value={override?.action ?? ""}
                      onChange={(event) => {
                        const action = event.target.value as Action | "";
                        if (!action) {
                          const next = { ...overrides };
                          delete next[track.id];
                          setOverrides(next);
                        } else
                          setOverrides({
                            ...overrides,
                            [track.id]: {
                              action,
                              strength:
                                action === "KEEP"
                                  ? null
                                  : (override?.strength ?? 2),
                            },
                          });
                      }}
                    >
                      <option value="">
                        {mustSpecifyAll ? "처리 방법 선택" : "모델 제안 사용"}
                      </option>
                      {actionList.map((action) => (
                        <option key={action} value={action}>
                          {actionNames[action]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field compact">
                    강도
                    <select
                      aria-label={`${track.id} 처리 강도`}
                      disabled={!override || override.action === "KEEP"}
                      value={override?.strength ?? 2}
                      onChange={(event) =>
                        setOverrides({
                          ...overrides,
                          [track.id]: {
                            ...override!,
                            strength: Number(event.target.value),
                          },
                        })
                      }
                    >
                      {[1, 2, 3].map((strength) => (
                        <option key={strength} value={strength}>
                          {strength}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              )}
              {!editable && run.effective_treatments?.[track.id] && (
                <div className="model-treatment">
                  <span>실제 적용</span>
                  <strong>
                    {actionNames[run.effective_treatments[track.id].action]}
                    {run.effective_treatments[track.id].strength
                      ? ` · 강도 ${run.effective_treatments[track.id].strength}`
                      : ""}
                  </strong>
                </div>
              )}
              {override && (
                <span className="override-tag">
                  <Check size={12} />
                  사용자 지정
                </span>
              )}
            </TrackCard>
          );
        })}
      </div>
      <ErrorNotice error={decide.error ?? render.error} />
      {run.status === "AWAITING_LABELS" && (
        <div className="panel-action">
          <p>관계를 모르는 얼굴은 ‘관계 미확인’으로 전달됩니다.</p>
          <button
            className="button primary"
            disabled={decide.isPending}
            onClick={() => decide.mutate()}
          >
            {decide.isPending ? "판정 요청 중…" : "이 설정으로 판정 생성"}
            <ArrowRight size={17} />
          </button>
        </div>
      )}
      {editable && (
        <div className="panel-action">
          <p>
            {!tracks.length && !confirmed
              ? "원본 출력 확인이 필요합니다."
              : missing.length
                ? `${missing.length}개 얼굴의 처리 방법을 지정해 주세요.`
                : mustSpecifyAll
                  ? "모든 얼굴을 지정했습니다. 사용자 지정으로 출력합니다."
                  : "처리 방법을 확인한 뒤 영상을 생성하세요."}
          </p>
          <button
            className="button primary"
            disabled={
              render.isPending ||
              missing.length > 0 ||
              (!tracks.length && !confirmed)
            }
            onClick={() => render.mutate()}
          >
            {render.isPending
              ? "영상 생성 요청 중…"
              : mustSpecifyAll || !tracks.length
                ? "사용자 지정으로 실험 출력"
                : "비식별 영상 생성"}
            <ArrowRight size={17} />
          </button>
        </div>
      )}
    </Panel>
  );
}

function TrackCard({ track, children }: { track: Track; children: ReactNode }) {
  const [imageFailed, setImageFailed] = useState(false);
  return (
    <article className="track-card">
      <div className="track-preview">
        {track.thumbnail && !imageFailed ? (
          <img
            src={artifactUrl(track.thumbnail)}
            alt={`${track.id} 얼굴 썸네일`}
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <span className="thumbnail-placeholder">
            <Image size={27} />
            썸네일 없음
          </span>
        )}
        <span className="track-id">{track.id}</span>
      </div>
      <div className="track-body">
        <div className="track-time">
          <span>
            {seconds(track.start)} — {seconds(track.end)}
          </span>
          <small>{track.points.length}개 관측</small>
        </div>
        {children}
      </div>
    </article>
  );
}

function EvidencePanel({ run }: { run: Run }) {
  const [tab, setTab] = useState<"candidates" | "provided" | "cited">(
    "provided",
  );
  const bundle = run.evidence;
  const citedIds = new Set(
    run.decision?.plan?.decisions.flatMap(
      (decision) => decision.evidence_ids,
    ) ?? [],
  );
  const provided = bundle?.provided ?? [];
  const cited = provided.filter((evidence) => citedIds.has(evidence.id));
  const selected =
    tab === "candidates"
      ? (bundle?.candidates ?? [])
      : tab === "cited"
        ? cited
        : provided;
  return (
    <Panel
      title="이 실행에서 사용한 근거"
      subtitle="검색 결과와 실제 모델 입력을 나누어 확인하세요."
      accessory={<FileText size={19} className="muted" />}
    >
      <div className="evidence-tabs" role="tablist" aria-label="근거 종류">
        {(
          [
            {
              key: "candidates",
              name: "검색 후보",
              count: (bundle?.candidates ?? []).length,
            },
            { key: "provided", name: "모델에 전달", count: provided.length },
            { key: "cited", name: "인용", count: cited.length },
          ] as const
        ).map((item) => (
          <button
            role="tab"
            aria-selected={tab === item.key}
            className={tab === item.key ? "active" : ""}
            key={item.key}
            onClick={() => setTab(item.key)}
          >
            {item.name}
            <span>{item.count}</span>
          </button>
        ))}
      </div>
      {bundle ? (
        <>
          <p className="evidence-note">
            {tab === "provided"
              ? "판정 시 모델에 실제로 전달한 문구입니다. 원문과 길이가 다를 수 있습니다."
              : tab === "candidates"
                ? "검색으로 찾은 후보입니다. 모든 후보가 모델에 전달되는 것은 아닙니다."
                : "모델이 판정에 연결한 근거입니다. 인용 여부가 법적 적합성을 보장하지는 않습니다."}
          </p>
          {!selected.length ? (
            <Empty>
              {tab === "cited"
                ? "판정에 연결된 인용 근거가 없습니다."
                : "이 실행에 해당 근거가 없습니다."}
            </Empty>
          ) : (
            <div className="evidence-list">
              {selected.map((evidence) => (
                <EvidenceCard key={evidence.id} evidence={evidence} />
              ))}
            </div>
          )}
          <details className="details-block">
            <summary>검색 질의와 자료 묶음</summary>
            <p>{bundle.query}</p>
            <code>{bundle.index_id}</code>
          </details>
        </>
      ) : (
        <Empty>
          얼굴을 확인하고 판정을 생성하면 검색 근거가 표시됩니다. 자료 묶음 없이
          실행한 경우 근거가 없습니다.
        </Empty>
      )}
    </Panel>
  );
}

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const sourceUrl = externalUrl(evidence.metadata.source_url);
  return (
    <details className="evidence-card">
      <summary>
        <span className="source-icon">
          <FileText size={17} />
        </span>
        <span>
          <strong>{evidence.metadata.title}</strong>
          <small>
            {[
              evidence.page != null ? `${evidence.page}페이지` : "",
              evidence.article,
              evidence.metadata.version ?? evidence.metadata.edition,
            ]
              .filter(Boolean)
              .join(" · ") || "위치 정보 없음"}
          </small>
        </span>
        <ChevronRight size={15} />
      </summary>
      <div className="evidence-body">
        <p>{evidence.text}</p>
        {sourceUrl && (
          <a
            className="text-button"
            href={sourceUrl}
            target="_blank"
            rel="noreferrer"
          >
            원문 출처 <ExternalLink size={12} />
          </a>
        )}
        <code>{evidence.id}</code>
      </div>
    </details>
  );
}

function RunReportPanel({
  run,
  pending,
  error,
  regenerate,
}: {
  run: Run;
  pending: boolean;
  error: unknown;
  regenerate: () => void;
}) {
  if (
    !["COMPLETED", "FAILED", "REVIEW_READY", "REVIEW_REQUIRED"].includes(
      run.status ?? "",
    )
  )
    return null;
  return (
    <Panel
      title="현재 실행 기록 내보내기"
      subtitle="영상을 출력하기 전에도 저장된 근거와 판정을 확인할 수 있습니다."
      accessory={<FileText size={18} className="muted" />}
    >
      <div className="download-row">
        {run.reports?.html && (
          <a
            className="button secondary"
            href={artifactUrl(run.reports.html)}
            target="_blank"
            rel="noreferrer"
          >
            <FileText size={15} />
            현재 보고서 보기
          </a>
        )}
        {run.reports?.json && (
          <a
            className="button secondary"
            href={artifactUrl(run.reports.json)}
            download
          >
            <Download size={15} />
            실행 기록 JSON
          </a>
        )}
      </div>
      <button
        className="button secondary"
        disabled={pending}
        onClick={regenerate}
      >
        <RefreshCw size={15} />
        {pending ? "기록을 내보내는 중…" : "현재 기록으로 보고서 만들기"}
      </button>
      <ErrorNotice error={error} />
    </Panel>
  );
}

function ForkPanel({
  run,
  profiles,
  indexes,
  onFork,
  onClose,
}: {
  run: Run;
  profiles: Profile[];
  indexes: IndexRecord[];
  onFork: (run: Run) => void;
  onClose: () => void;
}) {
  const [context, setContext] = useState(run.scenario.context);
  const [profile, setProfile] = useState(String(run.config.name));
  const [indexId, setIndexId] = useState(run.index_id ?? "");
  const fixture =
    profiles.find((item) => item.name === profile)?.config.mode === "fixture";
  const fork = useMutation({
    mutationFn: () =>
      post<Run>(`/runs/${run.id}/fork`, {
        profile,
        index_id: indexId || null,
        scenario: {
          ...run.scenario,
          context: context.trim(),
          track_labels: {},
        },
      }),
    onSuccess: onFork,
  });
  return (
    <Panel
      title="같은 영상으로 비교 실행"
      subtitle="기존 기록을 유지하면서 새 구성과 상황으로 실행합니다."
      accessory={<Copy size={18} className="muted" />}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          fork.mutate();
        }}
      >
        <div className="fork-fields">
          <ProfileSelect
            profiles={profiles}
            value={profile}
            onChange={(value) => {
              setProfile(value);
              setIndexId("");
            }}
            id="fork-profile"
          />
          <label className="field">
            자료 묶음
            <select
              value={indexId}
              onChange={(event) => setIndexId(event.target.value)}
            >
              <option value="">근거 없이 실행</option>
              {indexes
                .filter(
                  (index) =>
                    index.status === "READY" &&
                    profiles.some(
                      (item) =>
                        item.name === profile &&
                        indexMatchesProfile(index.config, item.config),
                    ),
                )
                .map((index) => (
                  <option value={index.id} key={index.id}>
                    {profileNames[String(index.config.name)] ??
                      String(index.config.name)}{" "}
                    · {index.chunk_count}개 청크
                  </option>
                ))}
            </select>
          </label>
        </div>
        <label className="field">
          비교할 상황
          <textarea
            rows={3}
            required
            maxLength={10000}
            value={context}
            onChange={(event) => setContext(event.target.value)}
          />
        </label>
        {fixture && <FixtureNotice />}
        <ErrorNotice error={fork.error} />
        <div className="download-row">
          <button
            className="button primary"
            disabled={!context.trim() || fork.isPending}
          >
            <GitBranch size={15} />
            {fork.isPending ? "새 실행 시작 중…" : "비교 실행 시작"}
          </button>
          <button className="button subtle" type="button" onClick={onClose}>
            닫기
          </button>
        </div>
      </form>
    </Panel>
  );
}
