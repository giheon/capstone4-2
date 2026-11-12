import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { ArrowDownRight, ArrowLeft, ArrowUpRight, Check, FlaskConical, X } from "lucide-react";
import { isNotFound } from "../api/client";
import type { ComputedExperiment, ExperimentReport, ProfileMetrics, StrategyResult } from "../api/contract";
import { useBackendKind, useExperiment, useExperiments, useProfiles, useStartExperiment } from "../api/hooks";
import { STATUS_LABEL, percent, relativeTime, seconds, usd } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { GateChip } from "./pipelineParts";

// The whole-pipeline experiments of the later milestone (M5): profiles run to the end on labelled scenarios and
// are scored against the gold plan. Only the M2 mock has data for these screens; the real backend lists none.
// Section experiments, which the real backend runs today, are in ExperimentsPage · StageExperimentView.
const PIPELINE = "/lab/experiments/pipeline";

type Metric = {
  key: keyof StrategyResult;
  label: string;
  format: (v: number) => string;
  better: "high" | "low";
  max: (rows: StrategyResult[]) => number;
};

const METRICS: Metric[] = [
  { key: "policyF1", label: "정책 F1", format: (v) => v.toFixed(2), better: "high", max: () => 1 },
  { key: "missRate", label: "누락률", format: (v) => percent(v, 1), better: "low", max: (r) => Math.max(...r.map((x) => x.missRate)) },
  { key: "overRate", label: "과잉 처리율", format: (v) => percent(v, 1), better: "low", max: (r) => Math.max(...r.map((x) => x.overRate)) },
  { key: "questions", label: "질문 수", format: (v) => v.toFixed(1), better: "low", max: (r) => Math.max(...r.map((x) => x.questions)) },
  { key: "counterfactualChange", label: "근거 반영률", format: (v) => v.toFixed(2), better: "high", max: () => 1 },
  { key: "costUsd", label: "비용", format: (v) => usd(v), better: "low", max: (r) => Math.max(...r.map((x) => x.costUsd)) },
  { key: "latencySec", label: "지연", format: (v) => `${Math.round(v)}s`, better: "low", max: (r) => Math.max(...r.map((x) => x.latencySec)) },
];

/** `/lab/experiments/pipeline` and `/lab/experiments/pipeline/:experimentId` */
export function PipelineExperimentsPage() {
  const { experimentId } = useParams();
  return experimentId ? <ExperimentView id={experimentId} /> : <ExperimentList />;
}

/* ---------- list + new experiment ---------- */

function ExperimentList() {
  const list = useExperiments();
  const [search] = useSearchParams();
  const preset = (search.get("profiles") ?? "").split(",").filter(Boolean);
  const [open, setOpen] = useState(preset.length > 0);
  // scoring the whole pipeline against labelled scenarios is the next milestone: the real backend lists none and cannot start one
  const real = useBackendKind() === "real";
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <Link to="/lab/experiments" className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            실험
          </Link>
          <h1 className="lab-title">전체 파이프라인 평가 실험</h1>
          <p className="lab-lead">
            프로필 여러 개를 정답이 있는 시나리오로 끝까지 실행하고 정답 계획과 비교합니다. 회귀 게이트를 통과한 프로필만 서비스에 적용할 수 있습니다.
          </p>
        </div>
        {real ? null : (
          <button type="button" className="btn btn-primary" aria-expanded={open} onClick={() => setOpen(!open)}>
            <FlaskConical size={15} aria-hidden="true" />새 평가 실험
          </button>
        )}
      </header>
      {real ? (
        <ExperimentsUnavailable />
      ) : (
        <p className="example-banner" role="note">
          다음 마일스톤(M5)에 실제 파이프라인으로 연결되는 화면입니다. 지금은 <strong>모의 백엔드의 예시</strong>로만 동작합니다. 부품 조합을 실제로 비교하려면{" "}
          <Link to="/lab/experiments">구간 실험</Link>을 쓰세요.
        </p>
      )}
      {real ? null : open ? <NewExperiment preset={preset} onCancel={() => setOpen(false)} /> : null}
      {real && list.data?.length === 0 ? null : (
      <section className="lab-section" aria-labelledby="exp-list-title">
        <h2 id="exp-list-title" className="lab-section-title">
          평가 실험 {list.data?.length ?? "…"}
        </h2>
        {list.isLoading ? <Skeleton lines={4} /> : null}
        {list.isError ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
        {list.data && list.data.length === 0 ? <EmptyState title="아직 평가 실험이 없습니다">새 평가 실험을 시작하면 여기에 나타납니다.</EmptyState> : null}
        {list.data && list.data.length > 0 ? (
          <div className="table-wrap">
            <table className="table lab-table">
              <thead>
                <tr>
                  <th scope="col">실험</th>
                  <th scope="col">프로필과 게이트</th>
                  <th scope="col">데이터셋</th>
                  <th scope="col" className="num">
                    실행
                  </th>
                  <th scope="col">만든 시각</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((x) => (
                  <tr key={x.id}>
                    <th scope="row">
                      <Link to={`${PIPELINE}/${x.id}`} className="exp-link">
                        {x.title}
                      </Link>
                      <span className="cell-note mono">
                        {x.id}
                        {x.kind === "example" ? " · 예시 (M5 형식)" : ""}
                      </span>
                    </th>
                    <td>
                      {x.kind === "example" ? (
                        <span className="muted small">전략 5종 · 예시 값</span>
                      ) : (
                        <span className="exp-profiles-cell">
                          {x.profiles.map((p) => (
                            <span key={p} className={cx("exp-pass", x.passed?.[p] ? "is-pass" : "is-fail")}>
                              {x.passed?.[p] ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : <X size={12} strokeWidth={3} aria-hidden="true" />}
                              <span className="mono">{p}</span>
                              <span className="sr-only">{x.passed?.[p] ? "게이트 통과" : "게이트 미달"}</span>
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className="small">{x.dataset}</td>
                    <td className="num">{x.runs || "—"}</td>
                    <td className="muted">{relativeTime(x.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
      )}
    </main>
  );
}

function ExperimentsUnavailable() {
  return (
    <section className="lab-unavailable" aria-labelledby="exp-unavailable-title">
      <h2 id="exp-unavailable-title" className="lab-unavailable-title">
        정답 시나리오로 채점하는 평가 실험은 아직 연결되지 않았습니다
      </h2>
      <p>
        프로필을 정답이 있는 시나리오로 끝까지 돌려 정답 계획과 비교하고 회귀 게이트를 매기는 실험은 다음 마일스톤(M5)에서 연결됩니다. 그때까지 프로필의 게이트는
        &lsquo;평가 전&rsquo;으로 남고, 서비스에 새로 적용할 수 없습니다.
      </p>
      <p>부품 조합을 구간별로 비교하는 구간 실험은 지금 실제 파이프라인에서 돌릴 수 있습니다.</p>
      <div className="lab-unavailable-actions">
        <Link to="/lab/experiments" className="btn btn-sm btn-secondary">
          구간 실험으로
        </Link>
        <Link to="/lab/profiles" className="btn btn-sm btn-ghost">
          프로필 보기
        </Link>
      </div>
    </section>
  );
}

function NewExperiment({ preset, onCancel }: { preset: string[]; onCancel: () => void }) {
  const profiles = useProfiles();
  const start = useStartExperiment();
  const navigate = useNavigate();
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [baseline, setBaseline] = useState<string>("");
  const [reps, setReps] = useState(2);
  const [title, setTitle] = useState("");
  const list = profiles.data ?? [];
  const service = list.find((p) => p.service.includes("default"));

  useEffect(() => {
    if (chosen || !profiles.data) return;
    const ids = [...new Set([service?.id, ...preset].filter((x): x is string => !!x && list.some((p) => p.id === x)))].slice(0, 4);
    setChosen(ids);
    setBaseline(service?.id ?? ids[0] ?? "");
  }, [chosen, profiles.data, preset, service, list]);

  const picked = chosen ?? [];
  const toggle = (id: string, on: boolean) => {
    const next = on ? [...picked, id] : picked.filter((x) => x !== id);
    setChosen(next);
    if (!next.includes(baseline)) setBaseline(next[0] ?? "");
  };

  return (
    <form
      className="lab-section new-exp"
      aria-labelledby="new-exp-title"
      onSubmit={(e) => {
        e.preventDefault();
        start.mutate(
          { profiles: picked, baseline, dataset: "scenarios-v3", repetitions: reps, title },
          { onSuccess: (x) => navigate(`${PIPELINE}/${x.id}`) },
        );
      }}
    >
      <h2 id="new-exp-title" className="lab-section-title">
        새 평가 실험
      </h2>
      {profiles.isLoading ? <Skeleton lines={3} /> : null}
      <fieldset className="exp-fieldset">
        <legend className="field-label">비교할 프로필 · 최대 4개, 기준 1개</legend>
        <ul className="exp-pick">
          {list.map((p) => {
            const on = picked.includes(p.id);
            const errors = p.issues.filter((i) => i.level === "error").length;
            return (
              <li key={p.id} className={cx("exp-pick-row", on && "is-on")}>
                <label className="check">
                  <input type="checkbox" checked={on} disabled={(!on && picked.length >= 4) || errors > 0} onChange={(e) => toggle(p.id, e.target.checked)} />
                  <span className="mono">{p.name}</span>
                </label>
                <GateChip gate={p.gate} />
                <span className="muted small num">≈ {usd(p.estimate.costUsd, 3)} / 건</span>
                {errors ? <span className="small exp-pick-error">오류가 있어 실행할 수 없음</span> : null}
                {on ? (
                  <label className="check exp-baseline">
                    <input type="radio" name="baseline" checked={baseline === p.id} onChange={() => setBaseline(p.id)} />
                    기준
                  </label>
                ) : null}
              </li>
            );
          })}
        </ul>
      </fieldset>
      <div className="exp-options">
        <div className="field">
          <label className="field-label" htmlFor="exp-dataset">
            데이터셋
          </label>
          <select id="exp-dataset" className="select" value="scenarios-v3" onChange={() => undefined} aria-describedby="exp-dataset-hint">
            <option value="scenarios-v3">scenarios-v3 · 시나리오 13개</option>
          </select>
          <span id="exp-dataset-hint" className="field-hint">
            M2 모의 엔진은 S01 카페 인터뷰만 실행합니다.
          </span>
        </div>
        <fieldset className="field">
          <legend className="field-label">반복</legend>
          <div className="segmented" role="radiogroup" aria-label="반복 횟수">
            {[1, 2, 3].map((n) => (
              <label key={n}>
                <input type="radio" name="reps" checked={reps === n} onChange={() => setReps(n)} />
                {n}회
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field">
          <span className="field-label">제목 (선택)</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: 경량 분석가 비교" />
        </label>
      </div>
      <div className="exp-actions">
        <button type="submit" className="btn btn-primary" disabled={picked.length === 0 || start.isPending}>
          {start.isPending ? <Spinner /> : null}
          실험 시작 · 실행 {picked.length * reps}건
        </button>
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          취소
        </button>
      </div>
      {start.isError ? <ErrorState error={start.error} /> : null}
    </form>
  );
}

/* ---------- one experiment ---------- */

function ExperimentView({ id }: { id: string }) {
  const q = useExperiment(id);
  if (q.isLoading) return <main className="lab-page"><Skeleton lines={6} /></main>;
  if (q.isError || !q.data) {
    return (
      <main className="lab-page">
        <Link to={PIPELINE} className="back-link">
          <ArrowLeft size={14} aria-hidden="true" />
          평가 실험 목록
        </Link>
        <ErrorState error={q.error} onRetry={isNotFound(q.error) ? undefined : () => void q.refetch()} />
      </main>
    );
  }
  return q.data.kind === "example" ? <ExampleExperiment x={q.data} /> : <ComputedExperimentView x={q.data} />;
}

type Measure = {
  key: keyof ProfileMetrics;
  label: string;
  better: "high" | "low";
  format: (v: number) => string;
  delta: (v: number) => string;
};

const MEASURES: Measure[] = [
  { key: "policyF1", label: "정책 F1", better: "high", format: (v) => v.toFixed(2), delta: (v) => Math.abs(v).toFixed(2) },
  { key: "missRate", label: "누락률", better: "low", format: (v) => percent(v), delta: (v) => percent(Math.abs(v)) },
  { key: "overRate", label: "과잉 처리율", better: "low", format: (v) => percent(v), delta: (v) => percent(Math.abs(v)) },
  { key: "requirementRecall", label: "요건 재현율", better: "high", format: (v) => v.toFixed(2), delta: (v) => Math.abs(v).toFixed(2) },
  { key: "citationValidity", label: "인용 유효율", better: "high", format: (v) => percent(v), delta: (v) => percent(Math.abs(v)) },
  { key: "questions", label: "질문 수", better: "low", format: (v) => v.toFixed(1), delta: (v) => Math.abs(v).toFixed(1) },
  { key: "toolCalls", label: "분석가 도구 호출", better: "low", format: (v) => v.toFixed(1), delta: (v) => Math.abs(v).toFixed(1) },
  { key: "costUsd", label: "비용", better: "low", format: (v) => usd(v, 3), delta: (v) => `$${Math.abs(v).toFixed(3)}` },
  { key: "latencySec", label: "지연 (사람 대기 제외)", better: "low", format: (v) => `${v.toFixed(1)}s`, delta: (v) => `${Math.abs(v).toFixed(1)}s` },
];

const DIFF_KIND = { omission: "누락", over: "과잉 처리", range: "구간 오류" } as const;

function ComputedExperimentView({ x }: { x: ComputedExperiment }) {
  const base = x.metrics.find((m) => m.profileId === x.baseline) ?? x.metrics[0];
  const gateNames = base?.gate ?? [];
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <Link to={PIPELINE} className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            평가 실험 목록
          </Link>
          <p className="lab-eyebrow mono">{x.id}</p>
          <h1 className="lab-title">{x.title}</h1>
          <p className="lab-lead">
            {x.dataset} · 반복 {x.repetitions}회 · 기준 <span className="mono">{base?.name}</span> · {relativeTime(x.createdAt)}
          </p>
        </div>
      </header>
      <p className="example-banner" role="note">
        모의 실행으로 계산한 값입니다. 프로필 선택은 아래 <a href="#rules">모의 실행 규칙</a>대로 실행을 바꾸고, 점수는 S01의 정답 계획과 비교해 매깁니다. M5에서는 같은 평가기가 시나리오 13개로 LangSmith에서 돌아갑니다.
      </p>

      <section className="lab-section" aria-labelledby="gate-title">
        <h2 id="gate-title" className="lab-section-title">
          회귀 게이트 — 서비스 적용 조건
        </h2>
        <div className="table-wrap">
          <table className="table lab-table gate-matrix">
            <thead>
              <tr>
                <th scope="col">조건</th>
                {x.metrics.map((m) => (
                  <th key={m.profileId} scope="col">
                    <Link to={`/lab/profiles/${m.profileId}`} className="mono">
                      {m.name}
                    </Link>
                    {m.profileId === x.baseline ? <span className="metric-dir">기준</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {gateNames.map((g, i) => (
                <tr key={g.metric}>
                  <th scope="row">
                    {g.metric} <span className="muted small">{g.threshold}</span>
                  </th>
                  {x.metrics.map((m) => {
                    const cell = m.gate[i];
                    return (
                      <td key={m.profileId} className={cx("gate-cell", cell.passed ? "is-pass" : "is-fail")}>
                        {cell.passed ? <Check size={13} strokeWidth={3} aria-hidden="true" /> : <X size={13} strokeWidth={3} aria-hidden="true" />}
                        <span className="num">{cell.value}</span>
                        <span className="sr-only">{cell.passed ? "통과" : "미달"}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr className="gate-total">
                <th scope="row">판정</th>
                {x.metrics.map((m) => (
                  <td key={m.profileId} className={cx("gate-cell", m.passed ? "is-pass" : "is-fail")}>
                    {m.passed ? "통과 · 적용 가능" : "미달"}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="lab-section" aria-labelledby="measure-title">
        <h2 id="measure-title" className="lab-section-title">
          지표 비교
        </h2>
        <div className="table-wrap">
          <table className="table lab-table measure-table">
            <thead>
              <tr>
                <th scope="col">프로필</th>
                {MEASURES.map((m) => (
                  <th key={m.key} scope="col">
                    {m.label}
                    <span className="metric-dir">{m.better === "high" ? "높을수록 좋음" : "낮을수록 좋음"}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {x.metrics.map((m) => (
                <tr key={m.profileId} className={cx(m.profileId === x.baseline && "is-proposed")}>
                  <th scope="row">
                    <Link to={`/lab/profiles/${m.profileId}`} className="mono">
                      {m.name}
                    </Link>
                    <span className="cell-note mono">
                      {m.configHash} · 실행 {m.runs}
                    </span>
                  </th>
                  {MEASURES.map((k) => {
                    const value = m[k.key] as number;
                    const diff = base && m.profileId !== base.profileId ? value - (base[k.key] as number) : 0;
                    return (
                      <td key={k.key}>
                        <span className="num">{k.format(value)}</span>
                        {Math.abs(diff) > 1e-9 ? <MeasureDelta diff={diff} better={k.better} text={k.delta(diff)} /> : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="lab-note">Δ는 기준 프로필과의 차이입니다. 반복 실행의 평균이며, 결정은 반복마다 같고 지연과 토큰만 조금씩 다릅니다.</p>
      </section>

      <section className="lab-section" aria-labelledby="diag-title">
        <h2 id="diag-title" className="lab-section-title">
          결정 차이 — 정답 계획과 다른 대상
        </h2>
        {x.diffs.length === 0 ? (
          <EmptyState title="모든 프로필의 계획이 정답과 같습니다" />
        ) : (
          <div className="table-wrap">
            <table className="table lab-table">
              <thead>
                <tr>
                  <th scope="col">프로필</th>
                  <th scope="col">대상</th>
                  <th scope="col">종류</th>
                  <th scope="col">정답 → 결과</th>
                  <th scope="col">원인 (실행 기록)</th>
                </tr>
              </thead>
              <tbody>
                {x.diffs.map((d, i) => (
                  <tr key={`${d.profileId}-${d.target}-${i}`}>
                    <td className="mono">{x.metrics.find((m) => m.profileId === d.profileId)?.name ?? d.profileId}</td>
                    <th scope="row">{d.target}</th>
                    <td>
                      <span className={cx("diff-kind", `diff-${d.kind}`)}>{DIFF_KIND[d.kind]}</span>
                    </td>
                    <td>
                      {d.expected} → {d.actual}
                    </td>
                    <td className="small">{d.cause}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="lab-two">
        <section className="lab-section" aria-labelledby="scn-title">
          <h2 id="scn-title" className="lab-section-title">
            시나리오 {x.scenarios.filter((s) => s.executed).length} / {x.scenarios.length} 실행
          </h2>
          <ul className="scenario-list">
            {x.scenarios.map((s) => (
              <li key={s.id} className={cx(s.executed ? "is-run" : "is-pending")}>
                <span className="mono">{s.id}</span> {s.title}
                <span className="muted small">{s.executed ? "실행함" : "M4 실제 파이프라인에서"}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="lab-section" id="rules" aria-labelledby="rules-title">
          <h2 id="rules-title" className="lab-section-title">
            모의 실행 규칙
          </h2>
          <ol className="rule-list">
            {x.rules.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ol>
        </section>
      </div>

      <section className="lab-section" aria-labelledby="exp-runs-title">
        <h2 id="exp-runs-title" className="lab-section-title">
          실행 {x.runs.length}
        </h2>
        <div className="table-wrap">
          <table className="table lab-table">
            <thead>
              <tr>
                <th scope="col">실행</th>
                <th scope="col">프로필</th>
                <th scope="col">상태</th>
                <th scope="col" className="num">
                  정책 F1
                </th>
                <th scope="col" className="num">
                  비용
                </th>
                <th scope="col" className="num">
                  경과
                </th>
                <th scope="col">
                  <span className="sr-only">열기</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {x.runs.map((r) => (
                <tr key={r.id}>
                  <th scope="row">
                    <span className="run-title">{r.title}</span>
                    <span className="cell-note mono">{r.id}</span>
                  </th>
                  <td className="mono">{r.profile}</td>
                  <td>{STATUS_LABEL[r.status]}</td>
                  <td className="num">{r.policyF1 !== undefined ? r.policyF1.toFixed(2) : "—"}</td>
                  <td className="num">{usd(r.costUsd, 3)}</td>
                  <td className="num">{seconds(r.elapsedSec)}</td>
                  <td>
                    <Link to={`/lab/runs/${r.id}`} className="btn btn-sm btn-secondary">
                      토폴로지
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function MeasureDelta({ diff, better, text }: { diff: number; better: "high" | "low"; text: string }) {
  const up = diff > 0;
  const good = (up && better === "high") || (!up && better === "low");
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cx("delta", good ? "is-good" : "is-bad")}>
      <Icon size={12} aria-hidden="true" />
      {up ? "+" : "−"}
      {text}
      <span className="sr-only">{good ? "개선" : "악화"}</span>
    </span>
  );
}

/* ---------- the M5-format example ---------- */

function ExampleExperiment({ x }: { x: ExperimentReport }) {
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <Link to={PIPELINE} className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            평가 실험 목록
          </Link>
          <p className="lab-eyebrow mono">{x.id} · 예시</p>
          <h1 className="lab-title">{x.title}</h1>
          <p className="lab-lead">
            {x.dataset} · 반복 {x.repetitions}회 · 같은 데이터셋에서 계획 전략만 바꿔 비교합니다. LangSmith 실험 비교와 같은 기준입니다.
          </p>
        </div>
        <a className="btn btn-secondary" href="https://smith.langchain.com/" target="_blank" rel="noreferrer">
          LangSmith에서 비교 열기
        </a>
      </header>
      <p className="example-banner" role="note">
        아래 수치는 M5 실험의 화면 형식을 보여 주는 <strong>예시 값</strong>입니다. 전략 비교 · 반사실 · 기능 제거는 M5에서 팀 시나리오 13종으로 측정해 바꿉니다. 지금 프로필로 직접 돌린 실험은 실험 목록에 있습니다.
      </p>

      <section className="lab-section" aria-labelledby="gate-title">
        <h2 id="gate-title" className="lab-section-title">
          회귀 게이트 — 프로필 승격 조건
        </h2>
        <ul className="gate">
          {x.gate.map((g) => (
            <li key={g.metric} className={cx("gate-item", g.passed ? "is-pass" : "is-fail")}>
              {g.passed ? <Check size={14} strokeWidth={3} aria-hidden="true" /> : <X size={14} strokeWidth={3} aria-hidden="true" />}
              <span className="gate-metric">{g.metric}</span>
              <span className="mono num">
                {g.value} <span className="muted">기준 {g.threshold}</span>
              </span>
              <span className="sr-only">{g.passed ? "통과" : "실패"}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="lab-section" aria-labelledby="strategy-title">
        <h2 id="strategy-title" className="lab-section-title">
          전략 비교
        </h2>
        <StrategyTable rows={x.strategies} />
        <p className="lab-note">근거 반영률: 반사실 코퍼스에서 근거를 바꿨을 때 결정이 기대대로 바뀐(또는 유지된) 비율. 막대는 지표별 최댓값 기준이며 굵은 행이 제안 구조입니다.</p>
      </section>

      <section className="lab-section" aria-labelledby="scenario-title">
        <h2 id="scenario-title" className="lab-section-title">
          시나리오별 정책 F1
        </h2>
        <ScenarioHeat x={x} />
      </section>

      <div className="lab-two">
        <section className="lab-section" aria-labelledby="cf-title">
          <h2 id="cf-title" className="lab-section-title">
            반사실 코퍼스 — 근거가 결정을 바꾸는가
          </h2>
          <div className="table-wrap">
            <table className="table lab-table">
              <thead>
                <tr>
                  <th scope="col">바꾼 근거</th>
                  <th scope="col">기대</th>
                  <th scope="col" className="num">
                    관측
                  </th>
                  <th scope="col">판정</th>
                </tr>
              </thead>
              <tbody>
                {x.counterfactuals.map((c) => (
                  <tr key={c.id}>
                    <th scope="row">{c.label}</th>
                    <td>{c.expected === "change" ? "결정 변경" : "결정 유지"}</td>
                    <td className="num">{percent(c.observed)}</td>
                    <td>
                      <span className={cx("verdict", c.passed ? "verdict-pass" : "verdict-fail")}>{c.passed ? "통과" : "미달"}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="lab-section" aria-labelledby="ab-title">
          <h2 id="ab-title" className="lab-section-title">
            기능 제거 실험 (ablation)
          </h2>
          <div className="table-wrap">
            <table className="table lab-table">
              <thead>
                <tr>
                  <th scope="col">변경</th>
                  <th scope="col" className="num">
                    Δ 정책 F1
                  </th>
                  <th scope="col" className="num">
                    Δ 질문
                  </th>
                  <th scope="col" className="num">
                    Δ 비용
                  </th>
                </tr>
              </thead>
              <tbody>
                {x.ablations.map((a) => (
                  <tr key={a.id}>
                    <th scope="row">
                      {a.label}
                      <span className="cell-note">{a.note}</span>
                    </th>
                    <td className="num">
                      <Delta value={a.deltaF1} digits={2} goodWhen="up" />
                    </td>
                    <td className="num">
                      <Delta value={a.deltaQuestions} digits={1} goodWhen="down" />
                    </td>
                    <td className="num">
                      <Delta value={a.deltaCost} digits={3} goodWhen="down" prefix="$" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}

function StrategyTable({ rows }: { rows: StrategyResult[] }) {
  return (
    <div className="table-wrap">
      <table className="table lab-table strategy-table">
        <thead>
          <tr>
            <th scope="col">전략</th>
            {METRICS.map((m) => (
              <th key={m.key} scope="col">
                {m.label}
                <span className="metric-dir">{m.better === "high" ? "높을수록 좋음" : "낮을수록 좋음"}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={cx(r.proposed && "is-proposed")}>
              <th scope="row">
                <span className="strategy-name">{r.label}</span>
                <span className="cell-note">{r.description}</span>
              </th>
              {METRICS.map((m) => {
                const value = r[m.key] as number;
                const max = m.max(rows) || 1;
                return (
                  <td key={m.key}>
                    <span className="bar-cell">
                      <span className="bar-value num">{m.format(value)}</span>
                      <span className="bar-track" aria-hidden="true">
                        <span className="bar-fill" style={{ width: `${Math.max(value > 0 ? 2 : 0, (value / max) * 100)}%` }} />
                      </span>
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// sequential single hue on the dark surface: low values recede into the surface, high values carry the accent
const STEPS = 8;
const RAMP = Array.from({ length: STEPS }, (_, i) => `rgba(79, 184, 208, ${(0.05 + (i / (STEPS - 1)) * 0.5).toFixed(3)})`);

function heatColor(v: number): { bg: string; fg: string } {
  const k = Math.min(1, Math.max(0, (v - 0.3) / 0.7));
  const i = Math.min(STEPS - 1, Math.floor(k * STEPS));
  return { bg: RAMP[i], fg: i >= 5 ? "#ffffff" : "#c9d1d7" };
}

function ScenarioHeat({ x }: { x: ExperimentReport }) {
  const strategies = x.strategies;
  return (
    <div className="heat-wrap">
      <div className="table-wrap">
        <table className="table lab-table heat">
          <thead>
            <tr>
              <th scope="col">시나리오</th>
              {strategies.map((s) => (
                <th key={s.id} scope="col" className={cx("num", s.proposed && "is-proposed")}>
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {x.scenarios.map((sc) => (
              <tr key={sc.id}>
                <th scope="row">
                  <span className="mono muted">{sc.id}</span> {sc.title}
                </th>
                {strategies.map((s) => {
                  const v = sc.scores[s.id];
                  const c = heatColor(v);
                  return (
                    <td key={s.id} className="heat-cell num" style={{ background: c.bg, color: c.fg }} title={`${sc.title} · ${s.label} · 정책 F1 ${v.toFixed(2)}`}>
                      {v.toFixed(2)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="heat-legend" aria-label="색 범례: 정책 F1">
        <span className="mono num">0.30</span>
        <span className="heat-ramp">
          {RAMP.map((c) => (
            <span key={c} style={{ background: c }} />
          ))}
        </span>
        <span className="mono num">1.00</span>
      </div>
    </div>
  );
}

function Delta({ value, digits, goodWhen, prefix = "" }: { value: number; digits: number; goodWhen: "up" | "down"; prefix?: string }) {
  if (value === 0) return <span className="delta">0</span>;
  const up = value > 0;
  const good = (up && goodWhen === "up") || (!up && goodWhen === "down");
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cx("delta", good ? "is-good" : "is-bad")}>
      <Icon size={13} aria-hidden="true" />
      {up ? "+" : "−"}
      {prefix}
      {Math.abs(value).toFixed(digits)}
      <span className="sr-only">{good ? "개선" : "악화"}</span>
    </span>
  );
}
