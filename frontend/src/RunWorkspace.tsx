import { useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Image,
  RefreshCw,
  ScanFace,
  SlidersHorizontal,
  Video,
} from "lucide-react";
import { artifactUrl, externalUrl, post, saveOverrides } from "./api";
import IdentityReview, { FaceThumbnail } from "./IdentityReview";
import { useInputOptions } from "./InputContext";
import {
  Badge,
  Empty,
  ErrorNotice,
  formatDate,
  Loading,
  Panel,
  seconds,
} from "./components";
import { actionNames, busyStatuses, consentNames, labelNames } from "./types";
import type {
  Action,
  Evidence,
  IdentityGroup,
  Run,
  Track,
  Treatment,
  VideoRecord,
} from "./types";

const reviewStatuses = new Set(["REVIEW_READY", "REVIEW_REQUIRED"]);
const actionList: Action[] = ["KEEP", "BLUR", "MOSAIC", "MASK"];

export default function RunWorkspace({
  run,
  video,
  onRun,
}: {
  run: Run;
  video?: VideoRecord;
  onRun: (run: Run) => void;
}) {
  const client = useQueryClient();
  const options = useInputOptions();
  const update = (value: Run) => {
    client.setQueryData(["run", value.id], value);
    void client.invalidateQueries({ queryKey: ["runs"] });
  };
  const retry = useMutation({
    mutationFn: () =>
      post<Run>(`/runs/${run.id}/retry`, {
        stage: run.stages?.at(-1)?.stage ?? "analyze",
      }),
    onSuccess: update,
  });
  const revise = useMutation({
    mutationFn: () => post<Run>(`/runs/${run.id}/revise`),
    onSuccess: onRun,
  });
  const report = useMutation({
    mutationFn: () => post<Run>(`/runs/${run.id}/report`),
    onSuccess: update,
  });
  const reusesReview = Boolean(
    run.reused_stages?.analyze && run.reused_stages?.decide,
  );
  const step =
    run.status === "COMPLETED" || run.status === "RENDERING"
      ? 3
      : reviewStatuses.has(run.status ?? "") || run.status === "DECIDING"
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
            <h2>{video?.filename ?? "영상 처리"}</h2>
            <span>
              {formatDate(run.created_at)}
              {reusesReview ? " · 처리 수정" : ""}
            </span>
          </div>
        </div>
        <Badge status={run.status} />
      </div>
      {reusesReview && (
        <div className="notice info">
          <span>
            <strong>이전 분석과 판단을 불러왔습니다.</strong>
            <br />
            처리 방법과 강도를 수정해 새 영상을 만들 수 있습니다. 이전 결과도
            보존됩니다.
          </span>
        </div>
      )}
      <div className="progress-strip" aria-label="처리 진행 단계">
        {["영상 분석", "인물 확인", "처리 검토", "영상 저장"].map(
          (title, index) => (
            <div
              key={title}
              className={`${index < step || run.status === "COMPLETED" ? "done" : ""} ${index === step ? "current" : ""}`}
            >
              <span>{index + 1}</span>
              <strong>{title}</strong>
              {index < 3 && <ChevronRight size={16} />}
            </div>
          ),
        )}
      </div>
      {run.status === "FAILED" && (
        <div className="failure-panel">
          <p role="alert">
            처리를 완료하지 못했습니다. 잠시 후 실패한 단계부터 다시 시도해
            주세요.
          </p>
          <button
            className="button secondary"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
          >
            <RefreshCw size={16} />
            실패 단계 재시도
          </button>
          <ErrorNotice error={retry.error} />
        </div>
      )}
      {run.status === "PAUSED" && (
        <div className="notice info">
          작업이 일시 정지되었습니다. 실행 상태를 확인하고 있습니다.
        </div>
      )}
      {busyStatuses.has(run.status ?? "") && (
        <div className="working-panel" aria-live="polite">
          <Loading
            text={
              run.status === "RENDERING"
                ? "보호된 영상을 만들고 있습니다."
                : run.status === "DECIDING"
                  ? "근거를 확인하고 처리 방법을 제안하고 있습니다."
                  : "얼굴과 등장 구간을 분석하고 있습니다."
            }
          />
          <p>
            화면을 벗어나도 처리는 계속됩니다. 작업 내역에서 다시 확인하세요.
          </p>
        </div>
      )}
      <div className="two-column run-layout">
        <div className="stack">
          <Panel title="이용 정보">
            <dl className="facts-list">
              {run.scenario.use_purpose && (
                <div>
                  <dt>이용 목적</dt>
                  <dd>
                    {options?.use_purpose.find(
                      (option) => option.value === run.scenario.use_purpose,
                    )?.label ?? run.scenario.use_purpose}
                  </dd>
                </div>
              )}
              {run.scenario.audience_scope && (
                <div>
                  <dt>공개·제공 범위</dt>
                  <dd>
                    {options?.audience_scope.find(
                      (option) => option.value === run.scenario.audience_scope,
                    )?.label ?? run.scenario.audience_scope}
                  </dd>
                </div>
              )}
            </dl>
            {run.scenario.context && (
              <p className="context-text">{run.scenario.context}</p>
            )}
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
          </Panel>
          {run.observation &&
            (run.identity && run.status === "AWAITING_LABELS" ? (
              <IdentityReview key={run.id} run={run} update={update} />
            ) : (
              <TrackReview
                key={`${run.id}:${run.status}`}
                run={run}
                update={update}
              />
            ))}
          {run.render && (
            <Panel
              title="출력 결과"
              subtitle={
                run.manual_output
                  ? "직접 지정한 처리 방법을 적용한 영상입니다."
                  : "처리 결과를 확인하고 저장하세요."
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
                <span>{seconds(run.observation?.duration ?? 0)}</span>
                <span>
                  {run.render.has_audio ? "오디오 포함" : "오디오 없음"}
                </span>
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
                    href={artifactUrl(run.reports.html)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <FileText size={16} />
                    보고서 보기
                  </a>
                )}
              </div>
              {run.status === "COMPLETED" && (
                <div className="notice info">
                  <button
                    className="button secondary"
                    disabled={revise.isPending}
                    onClick={() => revise.mutate()}
                  >
                    <SlidersHorizontal size={16} />
                    {revise.isPending
                      ? "수정 준비 중…"
                      : "처리만 수정해 다시 출력"}
                  </button>
                </div>
              )}
              <ErrorNotice error={revise.error} />
            </Panel>
          )}
        </div>
        <div className="stack">
          <EvidencePanel run={run} />
          {!run.reports?.html &&
            ["COMPLETED", "REVIEW_READY", "REVIEW_REQUIRED"].includes(
              run.status ?? "",
            ) && (
              <Panel title="판단 보고서">
                <p>
                  입력한 정보와 처리 제안, 사용한 근거를 보고서로 확인할 수
                  있습니다.
                </p>
                <button
                  className="button secondary"
                  disabled={report.isPending}
                  onClick={() => report.mutate()}
                >
                  보고서 만들기
                </button>
                <ErrorNotice error={report.error} />
              </Panel>
            )}
          {!run.render && run.reports?.html && (
            <a
              className="button secondary"
              href={artifactUrl(run.reports.html)}
              target="_blank"
              rel="noreferrer"
            >
              보고서 보기
            </a>
          )}
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
  const renderTrack = (track: Track) => {
    const decision = plan?.decisions.find((item) => item.track_id === track.id);
    const override = overrides[track.id];
    return (
      <TrackCard key={track.id} track={track}>
        {!run.identity && (
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
        )}
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
                          action === "KEEP" ? null : (override?.strength ?? 2),
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
  };
  return (
    <Panel
      title={
        run.status === "AWAITING_LABELS"
          ? "검출된 얼굴을 확인하세요"
          : run.identity
            ? "인물별 처리 검토"
            : "얼굴별 처리 검토"
      }
      subtitle={
        run.identity
          ? `${run.identity.groups?.length ?? 0}명 · 인물별로 적용하거나 등장 구간을 펼쳐 개별 트랙의 처리를 수정하세요.`
          : `${tracks.length}개 얼굴 · 썸네일과 등장 구간으로 대상을 확인하세요.`
      }
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
              : "모든 얼굴의 처리 방법을 직접 지정하면 영상을 만들 수 있습니다. 원래 검토 사유는 보고서에 남습니다."}
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
              검출 결과를 확인했고, 원본 그대로 저장합니다.
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
      {run.identity ? (
        <div className="identity-grid">
          {(run.identity.groups ?? []).map((group, index) => (
            <IdentityTreatments
              key={group.id}
              run={run}
              group={group}
              position={index + 1}
              editable={editable}
              overrides={overrides}
              renderTrack={renderTrack}
              apply={(treatment) =>
                setOverrides((current) => ({
                  ...current,
                  ...Object.fromEntries(
                    group.track_ids.map((id) => [id, { ...treatment }]),
                  ),
                }))
              }
            />
          ))}
          <div className="track-grid">
            {tracks
              .filter(
                (track) =>
                  !run.identity!.groups?.some((group) =>
                    group.track_ids.includes(track.id),
                  ),
              )
              .map(renderTrack)}
          </div>
        </div>
      ) : (
        <div className="track-grid">{tracks.map(renderTrack)}</div>
      )}
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
                ? "사용자 지정으로 영상 생성"
                : "비식별 영상 생성"}
            <ArrowRight size={17} />
          </button>
        </div>
      )}
    </Panel>
  );
}

function IdentityTreatments({
  run,
  group,
  position,
  editable,
  overrides,
  apply,
  renderTrack,
}: {
  run: Run;
  group: IdentityGroup;
  position: number;
  editable: boolean;
  overrides: Record<string, Treatment>;
  apply: (treatment: Treatment) => void;
  renderTrack: (track: Track) => ReactNode;
}) {
  const [action, setAction] = useState<Action>("MASK");
  const [strength, setStrength] = useState(3);
  const tracks = run.observation!.tracks.filter((track) =>
    group.track_ids.includes(track.id),
  );
  const person = run.scenario.persons?.[group.id];
  const name = `인물 ${position}`;
  const treatments = [
    ...new Set(
      tracks.map((track) => {
        const treatment =
          overrides[track.id] ??
          run.effective_treatments?.[track.id] ??
          run.decision?.plan?.decisions.find(
            (decision) => decision.track_id === track.id,
          )?.treatment;
        return treatment
          ? `${actionNames[treatment.action]}${treatment.strength ? ` · 강도 ${treatment.strength}` : ""}`
          : "처리 미지정";
      }),
    ),
  ];
  return (
    <article className="identity-card" aria-label={`${name} 처리`}>
      <div className="identity-heading">
        <div className="identity-portrait">
          <FaceThumbnail
            artifact={group.thumbnail ?? tracks[0]?.thumbnail}
            label={`${name} 대표 얼굴`}
          />
        </div>
        <div>
          <h3>{name}</h3>
          <span>{tracks.length}개 등장 트랙</span>
        </div>
      </div>
      <p className="identity-person-facts">
        {labelNames[person?.relationship ?? "unknown"]} · 동의{" "}
        {consentNames[person?.consent ?? "unknown"]}
      </p>
      <p className="identity-person-facts">
        현재 처리: {treatments.join(" / ")}
      </p>
      {editable && (
        <div className="bulk-toolbar">
          <span>이 인물의 모든 등장 구간</span>
          <select
            aria-label={`${name} 처리 방법`}
            value={action}
            onChange={(event) => setAction(event.target.value as Action)}
          >
            {actionList.map((value) => (
              <option key={value} value={value}>
                {actionNames[value]}
              </option>
            ))}
          </select>
          <select
            aria-label={`${name} 처리 강도`}
            disabled={action === "KEEP"}
            value={strength}
            onChange={(event) => setStrength(Number(event.target.value))}
          >
            {[1, 2, 3].map((value) => (
              <option key={value} value={value}>
                강도 {value}
              </option>
            ))}
          </select>
          <button
            className="button secondary small"
            onClick={() =>
              apply({ action, strength: action === "KEEP" ? null : strength })
            }
          >
            {name} 전체 적용
          </button>
        </div>
      )}
      <details className="identity-treatments">
        <summary>
          {name} 등장 구간별 처리 · {tracks.length}개 트랙
        </summary>
        <div className="track-grid">{tracks.map(renderTrack)}</div>
      </details>
    </article>
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
        <span className="track-id">등장 구간</span>
      </div>
      <div className="track-body">
        <div className="track-time">
          <span>
            {seconds(track.start)} — {seconds(track.end)}
          </span>
        </div>
        {children}
      </div>
    </article>
  );
}

function EvidencePanel({ run }: { run: Run }) {
  const cited = new Set(
    run.decision?.plan?.decisions.flatMap(
      (decision) => decision.evidence_ids,
    ) ?? [],
  );
  const sources = (run.evidence?.provided ?? []).filter((item) =>
    cited.has(item.id),
  );
  return (
    <Panel
      title="판단에 사용한 근거"
      subtitle="처리 제안에 연결된 자료의 내용과 출처를 확인하세요."
      accessory={<FileText size={19} className="muted" />}
    >
      {sources.length ? (
        <div className="evidence-list">
          {sources.map((item) => (
            <EvidenceCard key={item.id} evidence={item} />
          ))}
        </div>
      ) : (
        <Empty>
          {run.decision
            ? "확인 가능한 인용 근거가 없습니다."
            : "처리 제안이 준비되면 사용한 근거가 표시됩니다."}
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
      </div>
    </details>
  );
}
