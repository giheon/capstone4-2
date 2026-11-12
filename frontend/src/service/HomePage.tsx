import { type DragEvent, type FormEvent, useId, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowRight, FileVideo, Upload } from "lucide-react";
import type { CaseDetail, CaseSummary, PrivacyMode } from "../api/contract";
import { useBackendConfig, useCases, useCreateCase, useProfiles, useUploadCase } from "../api/hooks";
import { STATUS_LABEL, bytes, needsUser, percent, relativeTime, seconds, usd } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { SceneView } from "./scene/SceneView";
import { type ProbeResult, probeVideo, rememberThumbnail } from "./upload";

type Source = { kind: "file"; file: File; probe: ProbeResult | null } | { kind: "sample" } | null;

const MODES: { value: PrivacyMode; title: string; detail: string; note: string }[] = [
  {
    value: "anonymized_upload",
    title: "얼굴을 익명화한 뒤 외부 모델로 장면 분석",
    detail: "얼굴 픽셀은 밖으로 보내지 않습니다. 장소·활동·글자 판단은 유지됩니다.",
    note: "기본 · 약 5분",
  },
  {
    value: "local_only",
    title: "외부로 보내지 않고 이 서버에서만 분석",
    detail: "데이터가 나가지 않는 대신 장면 설명의 정확도가 낮아질 수 있습니다.",
    note: "약 15분",
  },
];

export function HomePage() {
  const [source, setSource] = useState<Source>(null);
  const [memo, setMemo] = useState("");
  const [mode, setMode] = useState<PrivacyMode>("anonymized_upload");
  const [agreed, setAgreed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const config = useBackendConfig();
  const real = config.data?.backend === "real";
  const create = useCreateCase();
  const upload = useUploadCase();
  const navigate = useNavigate();
  const memoId = useId();
  const busy = create.isPending || upload.isPending;
  const failure = create.error ?? upload.error;

  const choose = (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith("video/") && !/\.(mov|mp4|mkv|m4v|webm)$/i.test(file.name)) {
      setFileError(`"${file.name}"은(는) 영상 파일이 아닙니다. MP4·MOV·MKV 파일을 골라 주세요.`);
      return;
    }
    setFileError(null);
    setSource({ kind: "file", file, probe: null });
    void probeVideo(file).then((probe) =>
      setSource((current) => (current?.kind === "file" && current.file === file ? { ...current, probe } : current)),
    );
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  };

  // the backend kind decides how the case is created, so wait for it
  const ready = !!source && agreed && !busy && !!config.data;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready || !source) return;
    const opened = (detail: CaseDetail) => {
      if (source.kind === "file" && source.probe?.thumbnail) rememberThumbnail(detail.id, source.probe.thumbnail);
      navigate(`/cases/${detail.id}`);
    };
    if (real) {
      create.reset();
      upload.mutate({ file: source.kind === "file" ? source.file : undefined, memo, mode, sample: source.kind === "sample" }, { onSuccess: opened });
      return;
    }
    const body =
      source.kind === "sample"
        ? { filename: "카페_인터뷰.mov", sizeBytes: 44_355_584, memo, mode, sample: true }
        : {
            filename: source.file.name,
            sizeBytes: source.file.size,
            durationSec: source.probe?.durationSec,
            width: source.probe?.width,
            height: source.probe?.height,
            memo,
            mode,
            sample: false,
          };
    upload.reset();
    create.mutate(body, { onSuccess: opened });
  };

  return (
    <main className="home">
      <section className="home-main" aria-labelledby="home-title">
        <div className="home-intro">
          <p className="eyebrow">새 작업</p>
          <h1 id="home-title">영상만 올리면 비식별화를 시작합니다</h1>
          <p className="lead">
            사람·글자·번호판을 찾고, 법령과 판례를 근거로 무엇을 모자이크하고 무엇을 그대로 둘지 정합니다. 미리 채울 양식은 없고, 판단에 꼭
            필요한 사실만 분석 도중에 묻습니다.
          </p>
        </div>

        <form className="new-case" onSubmit={submit} noValidate>
          {source === null ? (
            <label
              className={cx("dropzone", dragging && "is-dragging")}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <Upload size={22} aria-hidden="true" />
              <span className="dropzone-title">영상 파일을 끌어다 놓거나 눌러서 고르세요</span>
              <span className="dropzone-hint">MP4 · MOV · MKV, 5분 이하. 원본은 그대로 보존됩니다.</span>
              <input type="file" accept="video/*,.mov,.mkv" className="sr-only" onChange={(e) => choose(e.target.files?.[0])} />
            </label>
          ) : (
            <SourceCard source={source} real={real} locked={busy} onClear={() => setSource(null)} />
          )}
          {fileError ? <ErrorState error={new Error(fileError)} /> : null}
          {source === null && config.data?.sampleAvailable ? (
            <p className="sample-hint">
              올릴 영상이 없다면{" "}
              <button type="button" className="link-button" onClick={() => setSource({ kind: "sample" })}>
                {real ? "서버에 준비된 예시 영상" : "예시 영상(카페 인터뷰 · 01:12)"}
              </button>
              으로 진행해 보세요.
            </p>
          ) : null}

          <div className="field">
            <label className="field-label" htmlFor={memoId}>
              상황 메모 <span className="muted">(선택)</span>
            </label>
            <textarea
              id={memoId}
              className="textarea"
              rows={3}
              value={memo}
              onChange={(e) => setMemo(e.target.value)}
              placeholder="예: 유튜브에 올릴 카페 인터뷰입니다. 인터뷰한 분은 촬영에 동의했어요."
            />
            <p className="field-hint">적은 내용은 판단에 쓰는 사실로 정리되고, 검색어로는 쓰지 않습니다. 비워 두면 필요한 것만 나중에 묻습니다.</p>
          </div>

          <fieldset className="mode-choice">
            <legend className="field-label">장면 분석 방식</legend>
            {MODES.map((m) => (
              <label key={m.value} className={cx("mode-option", mode === m.value && "is-checked")}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value} onChange={() => setMode(m.value)} />
                <span className="mode-text">
                  <span className="mode-title">{m.title}</span>
                  <span className="mode-detail">{m.detail}</span>
                </span>
                <span className="mode-note">{m.note}</span>
              </label>
            ))}
          </fieldset>

          <label className="check">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            <span>
              출연자에게 동의를 받았거나 시험용 영상만 올립니다. 외부 AI 처리와 국외 이전 안내를 확인했습니다.
            </span>
          </label>

          {config.isError ? <ErrorState error={config.error} onRetry={() => void config.refetch()} /> : null}
          {failure ? <ErrorState error={failure} /> : null}

          <div className="submit-row">
            <button type="submit" className="btn btn-primary btn-lg" disabled={!ready} aria-disabled={!ready}>
              {busy ? <Spinner label="작업 만드는 중" /> : null}
              분석 시작
            </button>
            {real ? <CostNote mode={mode} /> : config.data ? <span className="submit-note num">예상 비용 약 $0.25 · 상한 $0.30</span> : null}
            {busy ? null : !source ? <span className="submit-note">영상을 먼저 고르세요.</span> : !agreed ? <span className="submit-note">안내 확인이 필요합니다.</span> : null}
          </div>
          {upload.isPending ? <UploadProgress fraction={upload.progress} sample={source?.kind === "sample"} /> : null}
        </form>
      </section>

      <aside className="home-aside">
        <RecentCases />
        <section className="how" aria-labelledby="how-title">
          <h2 id="how-title" className="aside-title">
            진행 순서
          </h2>
          <ol className="how-list">
            <li>
              <strong>분석</strong> 사람·글자·번호판과 장면을 찾습니다.
            </li>
            <li>
              <strong>질문</strong> 법적 판단에 필요한데 영상으로 알 수 없는 사실만 묻습니다.
            </li>
            <li>
              <strong>검토</strong> 근거와 유사 사례, 바꿨을 때의 위험을 보고 직접 고칩니다.
            </li>
            <li>
              <strong>감사</strong> {real ? "수정된 계획이 보호 요건을 모두 충족하는지 다시 검증합니다." : "다른 모델이 수정된 계획을 다시 검증합니다."}
            </li>
            <li>
              <strong>결과</strong> 처리한 곳 외에는 원본과 같은 영상과 초 단위 보고서를 받습니다.
            </li>
          </ol>
        </section>
      </aside>
    </main>
  );
}

/** real backend: the cost estimate and cap of the profile the service runs for this mode */
function CostNote({ mode }: { mode: PrivacyMode }) {
  const profiles = useProfiles();
  const profile = profiles.data?.find((p) => p.service.includes(mode === "local_only" ? "local_only" : "default"));
  if (!profile) return null;
  const cap = profile.params["case.cost_cap_usd"];
  return (
    <span className="submit-note num">
      1분 영상 기준 추정 비용 약 {usd(profile.estimate.costUsd)}
      {cap !== undefined ? ` · 상한 ${usd(cap)}` : ""}
    </span>
  );
}

function UploadProgress({ fraction, sample }: { fraction: number | null; sample: boolean }) {
  const sent = sample || (fraction ?? 0) >= 1;
  const value = sent ? 1 : (fraction ?? 0);
  return (
    <div className="upload-progress" role="status">
      <span className="upload-progress-label">
        {sample ? "예시 영상으로 작업을 만드는 중입니다." : sent ? "올렸습니다. 서버가 영상을 확인하고 있습니다." : "영상을 올리는 중입니다."}
        {sample ? null : <span className="num">{percent(value)}</span>}
      </span>
      <span className="meter tone-accent" role="progressbar" aria-label="업로드 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
        <span className={cx("meter-fill", sent && "is-indeterminate")} style={{ width: `${value * 100}%` }} />
      </span>
    </div>
  );
}

function SourceCard({ source, real, locked, onClear }: { source: Exclude<Source, null>; real: boolean; locked: boolean; onClear: () => void }) {
  if (source.kind === "sample" && real) {
    return (
      <div className="source-card">
        <div className="source-thumb">
          <span className="source-thumb-empty">
            <FileVideo size={22} aria-hidden="true" />
          </span>
        </div>
        <div className="source-meta">
          <span className="source-name">예시 영상</span>
          <span className="source-note">서버에 준비된 예시 영상을 실제 파이프라인으로 분석합니다.</span>
        </div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClear} disabled={locked}>
          바꾸기
        </button>
      </div>
    );
  }
  if (source.kind === "sample") {
    return (
      <div className="source-card">
        <div className="source-thumb">
          <SceneView compact t={12} mode="original" />
        </div>
        <div className="source-meta">
          <span className="source-name">카페_인터뷰.mov</span>
          <span className="source-specs num">01:12 · 1920×1080 · 29.97 fps(가변) · HEVC Main 10 · 42.3 MB</span>
          <span className="source-note">예시 영상 · 인터뷰 대상 1명, 직원 1명, 창밖 차량 1대</span>
        </div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClear}>
          바꾸기
        </button>
      </div>
    );
  }
  const { file, probe } = source;
  return (
    <div className="source-card">
      <div className="source-thumb">
        {probe?.thumbnail ? (
          <img src={probe.thumbnail} alt={`${file.name} 첫 장면`} />
        ) : (
          <span className="source-thumb-empty">{probe ? <FileVideo size={22} aria-hidden="true" /> : <Spinner label="미리보기 만드는 중" />}</span>
        )}
      </div>
      <div className="source-meta">
        <span className="source-name">{file.name}</span>
        <span className="source-specs num">
          {[probe?.durationSec ? seconds(probe.durationSec) : null, probe?.width ? `${probe.width}×${probe.height}` : null, bytes(file.size)]
            .filter(Boolean)
            .join(" · ")}
        </span>
        {probe?.error ? <span className="source-note">{real ? `${probe.error} 분석은 서버에서 그대로 진행됩니다.` : probe.error}</span> : null}
        {real ? null : <span className="source-note">모의 백엔드는 파일을 받지 않고, 예시 시나리오의 분석 결과를 보여 줍니다.</span>}
      </div>
      <button type="button" className="btn btn-sm btn-ghost" onClick={onClear} disabled={locked}>
        바꾸기
      </button>
    </div>
  );
}

function RecentCases() {
  const cases = useCases();
  return (
    <section className="recent" aria-labelledby="recent-title">
      <h2 id="recent-title" className="aside-title">
        최근 작업
      </h2>
      {cases.isLoading ? <Skeleton lines={3} /> : null}
      {cases.isError ? <ErrorState error={cases.error} onRetry={() => void cases.refetch()} /> : null}
      {cases.data && cases.data.length === 0 ? (
        <EmptyState title="아직 작업이 없습니다">새 작업을 시작하면 여기에서 진행 상황을 다시 열 수 있습니다.</EmptyState>
      ) : null}
      {cases.data && cases.data.length > 0 ? (
        <ul className="recent-list">
          {cases.data.slice(0, 8).map((c) => (
            <RecentItem key={c.id} c={c} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function RecentItem({ c }: { c: CaseSummary }) {
  const attention = needsUser(c.status) || c.status === "PAUSED";
  return (
    <li>
      <Link to={`/cases/${c.id}`} className={cx("recent-item", attention && "needs-user")}>
        <span className="recent-title">{c.upload?.filename ?? c.title}</span>
        <span className="recent-meta">
          <span className={cx("status-dot", `status-${c.status.toLowerCase()}`)} aria-hidden="true" />
          {c.stoppedAfter && c.status === "COMPLETED" ? "일부 구간만 실행" : STATUS_LABEL[c.status]}
          <span className="muted"> · {relativeTime(c.createdAt)}</span>
          <span className="muted num"> · {usd(c.costUsd)}</span>
        </span>
        {attention ? <ArrowRight size={15} aria-hidden="true" className="recent-go" /> : null}
      </Link>
    </li>
  );
}
