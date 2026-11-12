// `/lab/experiments/:experimentId`: one section experiment — a set of runs over one range that keeps growing.
// In this order: the runs that wait for a person, how the combinations compare (over all videos, then per
// dataset), which combination ran on which video, and every run in the order it was added.
import { Fragment, type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import { ArrowLeft, ChevronDown, ChevronRight, CornerDownRight, Pencil, Plus } from "lucide-react";
import { isNotFound } from "../api/client";
import { useProfiles } from "../api/hooks";
import {
  type DatasetSummary,
  type PipelineSection,
  type RunMetrics,
  type ScoreAggregate,
  type ScoreDirection,
  type StageExperiment,
  type StageItem,
  type StageRun,
  type StageVariant,
  type SummaryMeans,
  useAddStageRuns,
  useDeleteStageExperiment,
  useDeleteStageRun,
  useEditStageExperiment,
  useSections,
  useStageExperiment,
  useStageLimits,
  useStopStageExperiment,
} from "../api/stage";
import { bytes, relativeTime } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { LaunchPanel, type LaunchSeed } from "./LaunchPanel";
import { AsksMark, ExperimentStatus, ProgressBar, RangeGlyph, RunStatusMark, VariantName, directionText, interactionText, money, rangeLabel, rangeOf, scoreText, sectionLabel, secs, share, variantName } from "./stageParts";
import { ANSWER_LABEL } from "./useRunView";

const EPSILON = 1e-9;

export function StageExperimentView({ id }: { id: string }) {
  const q = useStageExperiment(id);
  const sections = useSections();
  // arriving from the bottom of the builder (its start button): the answer starts at the top
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [id]);
  if (q.isLoading || sections.isLoading) {
    return (
      <main className="lab-page">
        <Skeleton lines={6} />
      </main>
    );
  }
  if (q.isError || !q.data) {
    return (
      <main className="lab-page">
        <Link to="/lab/experiments" className="back-link">
          <ArrowLeft size={14} aria-hidden="true" />
          실험
        </Link>
        <ErrorState error={q.error} onRetry={isNotFound(q.error) ? undefined : () => void q.refetch()} />
      </main>
    );
  }
  return <Experiment x={q.data} sections={sections.data ?? []} />;
}

/** what "실행 추가" opens with; `nonce` makes the same request a new one */
interface LaunchRequest {
  seed: LaunchSeed;
  nonce: number;
}

/** the launch that repeats a combination: every part and value it changed, as the only choice */
function seedOf(variant: StageVariant): LaunchSeed {
  return {
    matrix: Object.fromEntries(Object.entries(variant.overrides).map(([slot, candidate]) => [slot, [candidate]])),
    paramMatrix: Object.fromEntries(Object.entries(variant.params).map(([param, value]) => [param, [value]])),
  };
}

function Experiment({ x, sections }: { x: StageExperiment; sections: PipelineSection[] }) {
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  // "기존 실험에 추가" of the builder arrives with `?add=1` and what was chosen there
  const [launch, setLaunch] = useState<LaunchRequest | null>(() =>
    search.get("add") ? { seed: (location.state as { launch?: LaunchSeed } | null)?.launch ?? {}, nonce: 0 } : null,
  );
  const panel = useRef<HTMLElement>(null);
  const add = (seed: LaunchSeed = {}) => setLaunch({ seed, nonce: Date.now() });
  useEffect(() => {
    if (search.get("add")) setSearch({}, { replace: true, state: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (launch?.nonce) panel.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [launch?.nonce]);

  const waiting = x.runs.filter((r) => r.status === "waiting");
  return (
    <main className="lab-page">
      <header className="lab-page-head sx-head">
        <div>
          <Link to="/lab/experiments" className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            실험
          </Link>
          <p className="lab-eyebrow mono">{x.id}</p>
          <Title x={x} />
          <dl className="sx-facts">
            <div>
              <dt>상태</dt>
              <dd>
                <ExperimentStatus status={x.status} waiting={x.progress.waiting} total={x.progress.total} />
              </dd>
            </div>
            <div>
              <dt>구간</dt>
              <dd>
                <span className="sx-range">
                  <RangeGlyph sections={sections} from={x.from} to={x.to} />
                  {rangeLabel(sections, x.from, x.to)}
                  {x.interactive ? <AsksMark title={interactionText(sections, x.interactions)} /> : null}
                </span>
              </dd>
            </div>
            <div>
              <dt>기준 프로필</dt>
              <dd>
                <Link to={`/lab/profiles/${x.baseProfile.id}`} className="mono">
                  {x.baseProfile.name}
                </Link>
              </dd>
            </div>
            <div>
              <dt>실행</dt>
              <dd className="num">
                {x.progress.done} / {x.progress.total}건 끝남
                <span className="muted">
                  {" "}
                  · 조합 {x.variants.length}개 · 영상 {x.items.length}개
                </span>
              </dd>
            </div>
            <div>
              <dt>비용</dt>
              <dd>
                <CostCap x={x} />
              </dd>
            </div>
            <div>
              <dt>최근 변경</dt>
              <dd>{relativeTime(x.updatedAt)}</dd>
            </div>
          </dl>
        </div>
        <Actions x={x} onAdd={() => add()} adding={!!launch} />
      </header>

      {x.status === "running" ? (
        <p className="sx-live" role="status">
          <ProgressBar done={x.progress.done} total={x.progress.total} label="실험 진행" />
          <span className="num">
            실행 {x.progress.done} / {x.progress.total} — 아래 값은 끝난 실행만으로 계산한 것이며 계속 바뀝니다.
          </span>
        </p>
      ) : null}
      {x.interactive ? (
        <p className="lab-note" data-testid="sx-interactive-note">
          이 구간은 사용자에게 묻습니다 ({interactionText(sections, x.interactions)}). 실행은 한 번에 1건씩 추가하고, 묻는 지점에서 멈춘 실행은 서비스 화면에서 직접 답해야 이어집니다. 자동으로
          답하지 않습니다.
        </p>
      ) : null}

      {launch ? <AddRuns key={launch.nonce} x={x} sections={sections} seed={launch.seed} onClose={() => setLaunch(null)} anchor={panel} /> : null}

      {waiting.length ? <Waiting x={x} runs={waiting} /> : null}
      {x.runs.length === 0 ? (
        <EmptyState
          title="이 실험에는 아직 실행이 없습니다"
          action={
            <button type="button" className="btn btn-sm btn-primary" onClick={() => add()}>
              실행 추가
            </button>
          }
        >
          조합과 영상을 골라 실행을 추가하면 여기에 쌓이고, 조합끼리 비교됩니다.
        </EmptyState>
      ) : (
        <>
          <Comparison x={x} sections={sections} />
          <Grid x={x} onAdd={add} />
          <RunList x={x} sections={sections} onAdd={add} />
        </>
      )}
    </main>
  );
}

/* ---------- header: name, cost cap, stop, delete ---------- */

function Title({ x }: { x: StageExperiment }) {
  const edit = useEditStageExperiment(x.id);
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <div className="sx-title">
        <h1 className="lab-title">{x.title}</h1>
        <button type="button" className="btn btn-sm btn-ghost btn-icon" onClick={() => setDraft(x.title)} aria-label="실험 이름 바꾸기" title="이름 바꾸기">
          <Pencil size={14} aria-hidden="true" />
        </button>
      </div>
    );
  }
  return (
    <form
      className="sx-title"
      aria-label="실험 이름 바꾸기"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) edit.mutate({ title: draft.trim() }, { onSuccess: () => setDraft(null) });
      }}
    >
      <input className="input sx-title-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={80} aria-label="실험 이름" autoFocus />
      <button type="submit" className="btn btn-sm btn-primary" disabled={!draft.trim() || edit.isPending}>
        {edit.isPending ? <Spinner /> : null}
        저장
      </button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setDraft(null)}>
        취소
      </button>
      {edit.isError ? <ErrorState error={edit.error} /> : null}
    </form>
  );
}

function CostCap({ x }: { x: StageExperiment }) {
  const edit = useEditStageExperiment(x.id);
  const limits = useStageLimits();
  const [draft, setDraft] = useState<string | null>(null);
  const max = limits.data?.costCapUsdMax ?? 100;
  if (draft === null) {
    return (
      <span className="num sx-cap">
        {money(x.costUsd)} <span className="muted">/ 상한 {money(x.costCapUsd)}</span>
        <button type="button" className="link-button small" onClick={() => setDraft(String(x.costCapUsd))}>
          상한 바꾸기
        </button>
      </span>
    );
  }
  const value = Number(draft);
  const valid = value > 0 && value <= max;
  return (
    <form
      className="sx-cap"
      aria-label="비용 상한 바꾸기"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) edit.mutate({ costCapUsd: value }, { onSuccess: () => setDraft(null) });
      }}
    >
      <input className="input num sx-cap-input" type="number" min={0.1} max={max} step={0.1} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="비용 상한 (USD)" autoFocus />
      <span className="muted small">USD</span>
      <button type="submit" className="btn btn-sm btn-primary" disabled={!valid || edit.isPending}>
        저장
      </button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setDraft(null)}>
        취소
      </button>
      {!valid ? <span className="sx-cap-hint">0보다 크고 {max} USD 이하</span> : null}
      {edit.isError ? <ErrorState error={edit.error} /> : null}
    </form>
  );
}

function Actions({ x, onAdd, adding }: { x: StageExperiment; onAdd: () => void; adding: boolean }) {
  const stop = useStopStageExperiment(x.id);
  const remove = useDeleteStageExperiment();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="sx-actions">
      <button type="button" className="btn btn-primary" onClick={onAdd} aria-expanded={adding}>
        <Plus size={15} aria-hidden="true" />
        실행 추가
      </button>
      {x.status === "running" ? (
        <button type="button" className="btn btn-secondary" disabled={stop.isPending} onClick={() => stop.mutate()} title="대기 중이거나 실행 중인 실행을 멈춥니다. 응답을 기다리는 실행은 그대로 둡니다.">
          {stop.isPending ? <Spinner /> : null}
          실험 멈추기
        </button>
      ) : confirming ? (
        <span className="confirm" role="group" aria-label="실험 지우기 확인">
          <span>이 실험을 지웁니다. 실행은 &lsquo;실행&rsquo; 메뉴에 남습니다.</span>
          <button type="button" className="btn btn-sm btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(x.id, { onSuccess: () => navigate("/lab/experiments") })}>
            {remove.isPending ? <Spinner /> : null}
            지우기
          </button>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(false)}>
            취소
          </button>
        </span>
      ) : (
        <button type="button" className="btn btn-ghost" onClick={() => setConfirming(true)}>
          지우기
        </button>
      )}
      {stop.isError ? <ErrorState error={stop.error} /> : null}
      {remove.isError ? <ErrorState error={remove.error} /> : null}
    </div>
  );
}

/* ---------- adding runs ---------- */

function AddRuns({ x, sections, seed, onClose, anchor }: { x: StageExperiment; sections: PipelineSection[]; seed: LaunchSeed; onClose: () => void; anchor: RefObject<HTMLElement | null> }) {
  const profiles = useProfiles();
  const addRuns = useAddStageRuns(x.id);
  const base = profiles.data?.find((p) => p.id === x.baseProfile.id);
  return (
    <section className="lab-section sx-add" aria-labelledby="sx-add-title" ref={anchor}>
      <div className="section-bar">
        <h2 id="sx-add-title" className="lab-section-title">
          실행 추가
        </h2>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>
          닫기
        </button>
      </div>
      <p className="lab-note">
        {x.interactive
          ? "조합 하나와 영상 하나를 골라 실행 1건을 더합니다. 이미 있는 조합이면 그 조합에 실행이 더해집니다."
          : "조합과 영상을 골라 실행을 더합니다. 이미 있는 조합이면 그 조합에 실행이 더해지고, 같은 조합 · 같은 영상을 다시 실행할 수도 있습니다."}
      </p>
      {profiles.isLoading ? <Skeleton lines={4} /> : null}
      {profiles.isError ? <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} /> : null}
      {profiles.data && !base ? <ErrorState error={new Error(`기준 프로필 '${x.baseProfile.name}'이(가) 지워져 실행을 더할 수 없습니다.`)} /> : null}
      {base ? (
        <LaunchPanel
          target={{ experiment: x.id }}
          range={rangeOf(sections, x.from, x.to)}
          sections={sections}
          base={base}
          interactive={x.interactive}
          seed={seed}
          revision={x.updatedAt}
          submitLabel={(runs) => `실행 ${runs}건 추가`}
          pending={addRuns.isPending}
          error={addRuns.error}
          onSubmit={(launch) => addRuns.mutate(launch, { onSuccess: onClose })}
          onCancel={onClose}
        />
      ) : null}
    </section>
  );
}

/* ---------- runs that wait for a person ---------- */

function Waiting({ x, runs }: { x: StageExperiment; runs: StageRun[] }) {
  return (
    <section className="lab-section sx-waiting" aria-labelledby="sx-waiting-title">
      <h2 id="sx-waiting-title" className="lab-section-title">
        응답을 기다리는 실행 {runs.length}
      </h2>
      <p className="lab-note">아래 실행은 사용자에게 묻는 지점에서 멈춰 있습니다. 서비스 화면에서 답하면 이어서 진행하고, 이 구간의 끝에서 마칩니다.</p>
      <div className="table-wrap">
        <table className="table lab-table sx-waiting-table">
          <thead>
            <tr>
              <th scope="col">실행</th>
              <th scope="col">영상</th>
              <th scope="col">조합</th>
              <th scope="col">기다리는 것</th>
              <th scope="col">
                <span className="sr-only">할 일</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const variant = x.variants.find((v) => v.id === run.variant);
              return (
                <tr key={run.id} data-run={run.id}>
                  <th scope="row" className="mono">
                    {run.id}
                    <span className="cell-note">{relativeTime(run.startedAt ?? run.createdAt)} 시작</span>
                  </th>
                  <td>{x.items.find((i) => i.key === run.item)?.title ?? run.item}</td>
                  <td>{variant ? <VariantName variant={variant} /> : run.variant}</td>
                  <td>
                    <RunStatusMark status="waiting" waitingFor={run.waitingFor} />
                    {run.error ? <span className="cell-note">{run.error}</span> : null}
                  </td>
                  <td className="sx-row-actions">
                    {run.caseId ? (
                      <>
                        <Link to={`/cases/${run.caseId}`} className="btn btn-sm btn-primary">
                          {run.waitingFor ? ANSWER_LABEL[run.waitingFor] : "서비스 화면에서 응답"}
                        </Link>
                        <Link to={`/lab/runs/${run.caseId}`} className="btn btn-sm btn-secondary">
                          토폴로지
                        </Link>
                      </>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/* ---------- how the combinations compare ---------- */

interface MetricColumn {
  id: string;
  label: string;
  unit: string;
  direction: ScoreDirection;
}

/** every metric any combination has a score for, in the order they first appear: the screens know none of them by name */
function metricColumns(x: StageExperiment): MetricColumn[] {
  const found = new Map<string, MetricColumn>();
  const take = (scores: Record<string, ScoreAggregate>) => {
    for (const [id, s] of Object.entries(scores)) if (!found.has(id)) found.set(id, { id, label: s.label, unit: s.unit, direction: s.direction });
  };
  for (const summary of x.summary) {
    take(summary.scores);
    for (const dataset of summary.byDataset) take(dataset.scores);
  }
  return [...found.values()];
}

/** which of the values is best in the given direction; none when fewer than two have a value or all are equal */
function bestOf(values: (number | null | undefined)[], direction: ScoreDirection): number | null {
  const have = values.filter((v): v is number => typeof v === "number");
  if (!direction || have.length < 2) return null;
  const best = direction === "higher" ? Math.max(...have) : Math.min(...have);
  return have.every((v) => Math.abs(v - best) < EPSILON) ? null : best;
}

function Comparison({ x, sections }: { x: StageExperiment; sections: PipelineSection[] }) {
  const metrics = metricColumns(x);
  const summaryOf = (variant: string) => x.summary.find((s) => s.variant === variant);
  const base = x.variants.find((v) => v.base);
  const baseSummary = base ? summaryOf(base.id) : undefined;
  // Means are over the videos each combination completed. When those differ between combinations the means
  // compare videos as much as combinations: say so, and mark nothing as best. Otherwise combinations are compared
  // on what the range takes and costs with nothing reused: what a run actually spent depends on what was already
  // stored when it ran.
  const completed = x.variants.map((v) => new Set(x.runs.filter((r) => r.variant === v.id && r.status === "completed").map((r) => r.item)));
  const active = completed.filter((set) => set.size > 0);
  const uneven = active.some((set) => set.size !== active[0].size || [...set].some((item) => !active[0].has(item)));
  const most = Math.max(0, ...completed.map((set) => set.size));
  const fewer = x.variants.map((v, i) => ({ name: variantName(v), size: completed[i].size })).filter((c) => c.size < most);
  const datasets = [...new Map(x.items.map((i) => [i.dataset, i.datasetName]))];
  const fastest = uneven ? null : bestOf(x.summary.map((s) => s.fullSecMean), "lower");
  const cheapest = uneven ? null : bestOf(x.summary.map((s) => s.fullCostUsdMean), "lower");
  const bestScore = (id: string, direction: ScoreDirection) => (uneven ? null : bestOf(x.summary.map((s) => s.scores[id]?.mean), direction));
  const anyDone = active.length > 0;
  return (
    <section className="lab-section" aria-labelledby="sx-compare-title">
      <h2 id="sx-compare-title" className="lab-section-title">
        조합 비교
      </h2>
      {uneven ? (
        <p className="sx-basis" role="note">
          조합마다 완료한 영상이 다릅니다
          {fewer.length ? ` (${fewer.map((c) => `‘${c.name}’ ${c.size}개`).join(", ")}${fewer.length < x.variants.length ? ` · 나머지 ${most}개` : ""})` : ""}. 아래 평균은 조합마다 자기가
          완료한 영상에서 낸 값이라 그대로 비교할 수 없습니다. 빈 칸은 &lsquo;조합 × 영상&rsquo;에서 채울 수 있습니다.
        </p>
      ) : null}
      <div className="table-wrap">
        <table className="table lab-table sx-compare">
          <thead>
            <tr>
              <th scope="col">조합</th>
              <th scope="col" className="num">
                완료한 영상
              </th>
              <MeanHeads />
              {metrics.map((m) => (
                <MetricHead key={m.id} metric={m} />
              ))}
            </tr>
          </thead>
          <tbody>
            {x.variants.map((variant) => {
              const s = summaryOf(variant.id);
              const first = variant.firstChanged ? sections.find((sec) => sec.id === variant.firstChanged) : undefined;
              return (
                <tr key={variant.id} className={cx(variant.base && "is-base")} data-variant={variant.id}>
                  <th scope="row">
                    <VariantName variant={variant} />
                    <span className="cell-note">
                      {variant.base ? "기준 프로필 그대로" : first ? `‘${first.label}’부터 기준과 다름` : ""}
                      {variant.framesLeaveDevice && !base?.framesLeaveDevice ? " · 프레임을 외부 모델로 보냄" : ""}
                    </span>
                  </th>
                  <td className="num">
                    {s ? `${s.items} / ${x.items.length}개` : "—"}
                    {s ? (
                      <span className="cell-note">
                        실행 {s.completed} / {s.runs}건 완료
                      </span>
                    ) : null}
                    {s?.waiting ? <span className="cell-note sx-count-wait">응답 대기 {s.waiting}</span> : null}
                    {s?.failed ? <span className="cell-note sx-count-bad">실패 {s.failed}</span> : null}
                    {s?.skipped ? <span className="cell-note">건너뜀 {s.skipped}</span> : null}
                  </td>
                  <MeanCells s={s} base={variant.base || uneven ? undefined : baseSummary} isBase={variant.base} fastest={fastest} cheapest={cheapest} />
                  {metrics.map((m) => (
                    <ScoreCell key={m.id} score={s?.scores[m.id]} videos={s?.items ?? 0} best={bestScore(m.id, m.direction)} />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="lab-note">
        {anyDone ? "" : "아직 완료된 실행이 없습니다. "}
        시간 · 비용 · 일치 · 점수는 영상 평균입니다: 같은 영상을 여러 번 실행했으면 그 영상의 평균을 먼저 내고, 그 값을 영상에 걸쳐 평균합니다. &lsquo;재사용 없이&rsquo;는{" "}
        {rangeLabel(sections, x.from, x.to)} 구간을 아무것도 재사용하지 않고 계산했다면 걸렸을 시간과 들었을 비용(실제로 쓴 것 + 재사용으로 아낀 것)이고, 조합끼리는 이 값으로 비교합니다.
        &lsquo;실제로 쓴&rsquo; 값은 저장된 단계 결과와 앞 실행의 체크포인트를 쓰고 난 나머지라, 어떤 실행이 먼저 돌았는지에 따라 달라집니다. 사용자를 기다린 시간은 어느 쪽에도 넣지 않았습니다.
        &lsquo;기준과 일치&rsquo;는 같은 영상의 가장 최근 기준 조합 실행과 결과가 얼마나 같은지입니다.
        {metrics.length ? " 점수 열은 실행에 저장된 점수를 지표마다 평균한 것입니다." : " 이 실험의 실행에는 저장된 점수가 없습니다."}
      </p>

      {datasets.length > 1 ? (
        <>
          <h3 className="lab-subtitle" id="sx-datasets-title">
            데이터셋별
          </h3>
          <div className="table-wrap">
            <table className="table lab-table sx-compare sx-by-dataset" aria-labelledby="sx-datasets-title">
              <thead>
                <tr>
                  <th scope="col">조합</th>
                  <th scope="col" className="num">
                    완료한 영상
                  </th>
                  <MeanHeads />
                  {metrics.map((m) => (
                    <MetricHead key={m.id} metric={m} />
                  ))}
                </tr>
              </thead>
              {datasets.map(([dataset, name]) => {
                const total = x.items.filter((i) => i.dataset === dataset).length;
                const rows = x.variants.map((variant) => ({ variant, d: summaryOf(variant.id)?.byDataset.find((b) => b.dataset === dataset) }));
                const baseRow = rows.find((r) => r.variant.base)?.d;
                return (
                  <tbody key={dataset} data-dataset={dataset}>
                    <tr className="sx-group-row">
                      <th scope="rowgroup" colSpan={7 + metrics.length}>
                        {name} <span className="muted">· 영상 {total}개</span>
                      </th>
                    </tr>
                    {rows.map(({ variant, d }) => (
                      <tr key={variant.id} className={cx(variant.base && "is-base")} data-variant={variant.id}>
                        <th scope="row">
                          <VariantName variant={variant} />
                        </th>
                        <td className="num">{d ? `${d.items} / ${total}개` : <span className="muted">실행 없음</span>}</td>
                        <MeanCells s={d} base={variant.base || uneven ? undefined : baseRow} isBase={variant.base} fastest={null} cheapest={null} />
                        {metrics.map((m) => (
                          <ScoreCell key={m.id} score={d?.scores[m.id]} videos={d?.items ?? 0} best={null} />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                );
              })}
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}

function MetricHead({ metric }: { metric: MetricColumn }) {
  const direction = directionText(metric.direction);
  return (
    <th scope="col" className="num" data-metric={metric.id} title={metric.id}>
      {metric.label}
      {direction ? <span className="cell-note">{direction}</span> : null}
    </th>
  );
}

/** the headers of `MeanCells` */
function MeanHeads() {
  return (
    <>
      <th scope="col" className="num sx-wrap" data-column="full-sec">
        재사용 없이 걸렸을 시간
      </th>
      <th scope="col" className="num sx-wrap" data-column="spent-sec">
        실제로 쓴 시간
      </th>
      <th scope="col" className="num sx-wrap" data-column="full-cost">
        재사용 없이 들었을 비용
      </th>
      <th scope="col" className="num sx-wrap" data-column="spent-cost">
        실제로 쓴 비용
      </th>
      <th scope="col" className="num">
        기준과 일치
      </th>
    </>
  );
}

/**
 * Time, cost and agreement of one row. Combinations are compared on what the range takes and costs with nothing
 * reused (best marks, difference from the base combination); what the runs actually spent stands beside it.
 */
function MeanCells({ s, base, isBase, fastest, cheapest }: { s: SummaryMeans | DatasetSummary | undefined; base: SummaryMeans | undefined; isBase: boolean; fastest: number | null; cheapest: number | null }) {
  const is = (value: number | null | undefined, best: number | null) => typeof value === "number" && best !== null && Math.abs(value - best) < EPSILON;
  return (
    <>
      <td className="num" data-column="full-sec">
        {secs(s?.fullSecMean)} {is(s?.fullSecMean, fastest) ? <span className="sx-best">최단</span> : null}
        <Delta value={s?.fullSecMean} base={base?.fullSecMean} format={secs} />
      </td>
      <td className="num sx-spent" data-column="spent-sec">
        {secs(s?.sectionSecMean)}
      </td>
      <td className="num" data-column="full-cost">
        {money(s?.fullCostUsdMean)} {is(s?.fullCostUsdMean, cheapest) ? <span className="sx-best">최저</span> : null}
        <Delta value={s?.fullCostUsdMean} base={base?.fullCostUsdMean} format={money} />
      </td>
      <td className="num sx-spent" data-column="spent-cost">
        {money(s?.costUsdMean)}
      </td>
      <td className="num">
        {isBase ? (
          <span className="muted">기준</span>
        ) : typeof s?.agreementMean === "number" ? (
          <span className={cx(s.agreementMean < 1 - EPSILON && "sx-differs")}>{share(s.agreementMean)}</span>
        ) : (
          "—"
        )}
      </td>
    </>
  );
}

function ScoreCell({ score, videos, best }: { score: ScoreAggregate | undefined; videos: number; best: number | null }) {
  if (!score) return <td className="num muted">—</td>;
  return (
    <td className="num">
      {scoreText(score.mean, score.unit)} {best !== null && Math.abs(score.mean - best) < EPSILON ? <span className="sx-best">최고</span> : null}
      {score.n < videos ? <span className="cell-note">영상 {score.n}개에서</span> : null}
    </td>
  );
}

/** the difference from the base combination; lower is better for both time and cost */
function Delta({ value, base, format }: { value: number | null | undefined; base: number | null | undefined; format: (v: number) => string }) {
  if (typeof value !== "number" || typeof base !== "number" || Math.abs(value - base) < 0.005 * Math.max(1e-6, Math.abs(base))) return null;
  const up = value > base;
  return (
    <span className={cx("delta sx-delta", up ? "is-bad" : "is-good")}>
      {up ? "+" : "−"}
      {format(Math.abs(value - base))}
      <span className="sr-only">{up ? "기준보다 많음" : "기준보다 적음"}</span>
    </span>
  );
}

/* ---------- combination × video ---------- */

/** the result of the last section a run produced, in one line */
function keyLine(metrics: RunMetrics | null, sectionIds: string[]): string | null {
  if (!metrics) return null;
  for (const id of [...sectionIds].reverse()) {
    if (id === "render" && metrics.render) return `검증 ${metrics.render.passed} / ${metrics.render.rows} 통과`;
    if (id === "review" && metrics.review) return `수정 ${metrics.review.edits} · 감사 발견 ${metrics.review.findings} · 가림 ${metrics.review.redacted.length}`;
    if (id === "plan" && metrics.plan) {
      return `결정 ${metrics.plan.decisions} · 가림 ${metrics.plan.redacted.length} · 유지 ${metrics.plan.kept.length}${metrics.plan.unsatisfied ? ` · 미충족 ${metrics.plan.unsatisfied}` : ""}`;
    }
    if (id === "analysis" && metrics.analysis) return `보호 요건 ${metrics.analysis.requirements} · 질문 ${metrics.analysis.questions} · 검색 ${metrics.analysis.searches}`;
    if (id === "perception" && metrics.perception) {
      const p = metrics.perception;
      return `글자 ${p.texts.length} · 인물 ${p.people} · 번호판 ${p.plates} · 장면 ${p.scenes}`;
    }
  }
  return null;
}

function Grid({ x, onAdd }: { x: StageExperiment; onAdd: (seed: LaunchSeed) => void }) {
  const runsOf = (variant: string, item: string) => x.runs.filter((r) => r.variant === variant && r.item === item);
  return (
    <section className="lab-section" aria-labelledby="sx-grid-title">
      <h2 id="sx-grid-title" className="lab-section-title">
        조합 × 영상
      </h2>
      <p className="lab-note">칸에는 그 조합 · 그 영상의 실행이 모두 들어 있고, 가장 최근 실행의 결과를 보여 줍니다. 실행 번호를 누르면 그 실행의 토폴로지가 열립니다.</p>
      <div className="table-wrap">
        <table className="table lab-table results">
          <thead>
            <tr>
              <th scope="col">조합</th>
              {x.items.map((item) => (
                <th key={item.key} scope="col" data-item={item.key}>
                  {item.title}
                  <span className="cell-note">
                    {item.datasetName} · {secs(item.durationSec)}
                    {item.sendable ? "" : " · 외부 전송 금지"}
                    {item.expected ? " · 정답 있음" : ""}
                  </span>
                  <button type="button" className="link-button small" onClick={() => onAdd({ items: [item.key] })}>
                    이 영상으로 다른 조합
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {x.variants.map((variant) => (
              <tr key={variant.id} className={cx(variant.base && "is-base")} data-variant={variant.id}>
                <th scope="row">
                  <VariantName variant={variant} />
                  <button type="button" className="link-button small" onClick={() => onAdd(seedOf(variant))}>
                    이 조합으로 다른 영상
                  </button>
                </th>
                {x.items.map((item) => {
                  const runs = runsOf(variant.id, item.key);
                  const latest = runs[runs.length - 1];
                  return (
                    <td key={item.key} className={cx("result-cell", !latest && "is-empty")} data-item={item.key}>
                      {latest ? (
                        <GridCell runs={runs} sections={x.sections} />
                      ) : (
                        <button type="button" className="btn btn-sm btn-ghost sx-cell-add" onClick={() => onAdd({ ...seedOf(variant), items: [item.key] })} aria-label={`${variantName(variant)} · ${item.title} 실행 추가`}>
                          <Plus size={13} aria-hidden="true" />
                          실행 추가
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function GridCell({ runs, sections }: { runs: StageRun[]; sections: string[] }) {
  const latest = runs[runs.length - 1];
  const m = latest.metrics;
  const line = keyLine(m, sections);
  const differs = m?.vsBase && m.vsBase.agreement < 1 - EPSILON;
  return (
    <>
      <RunStatusMark status={latest.status} waitingFor={latest.waitingFor} />
      {line ? <span className="result-line num">{line}</span> : null}
      {m?.vsBase ? (
        <span className="result-marks">
          <span className={cx("result-mark", differs && "is-differs")}>기준과 {share(m.vsBase.agreement)}</span>
        </span>
      ) : null}
      {latest.error && latest.status !== "waiting" ? <span className="result-error">{latest.error}</span> : null}
      <span className="sx-cell-runs">
        {runs.length > 1 ? <span className="muted">실행 {runs.length}건</span> : null}
        {runs.map((run) =>
          run.caseId ? (
            <Link key={run.id} to={`/lab/runs/${run.caseId}`} className="mono" title={`${run.id} 토폴로지`}>
              {run.id}
            </Link>
          ) : (
            <span key={run.id} className="mono muted">
              {run.id}
            </span>
          ),
        )}
      </span>
    </>
  );
}

/* ---------- every run, newest first ---------- */

function RunList({ x, sections, onAdd }: { x: StageExperiment; sections: PipelineSection[]; onAdd: (seed: LaunchSeed) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const runs = [...x.runs].reverse();
  const baseId = x.variants.find((v) => v.base)?.id;
  // what a run is compared with: the most recent completed run of the base combination on the same video
  const baseOf = (run: StageRun) => (run.variant === baseId ? undefined : [...x.runs].reverse().find((r) => r.variant === baseId && r.item === run.item && r.status === "completed"));
  const scored = x.runs.some((r) => r.scores.length > 0 || r.evaluationErrors?.length);
  const columns = scored ? 9 : 8;
  return (
    <section className="lab-section" aria-labelledby="sx-runs-title">
      <h2 id="sx-runs-title" className="lab-section-title">
        실행 {x.runs.length}
      </h2>
      <p className="lab-note">추가한 순서의 반대로, 가장 최근 실행이 위에 있습니다. 실행마다 &lsquo;실행&rsquo; 메뉴에 따로 남아 있어 토폴로지를 열 수 있습니다.</p>
      <div className="table-wrap">
        <table className="table lab-table sx-runs">
          <thead>
            <tr>
              <th scope="col">실행</th>
              <th scope="col">상태</th>
              <th scope="col">영상 · 조합</th>
              <th scope="col">시작 지점</th>
              <th scope="col" className="num">
                구간 시간
              </th>
              <th scope="col" className="num">
                비용
              </th>
              <th scope="col">결과</th>
              {scored ? <th scope="col">점수</th> : null}
              <th scope="col">
                <span className="sr-only">할 일</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const variant = x.variants.find((v) => v.id === run.variant);
              const item = x.items.find((i) => i.key === run.item);
              const isOpen = open === run.id;
              const m = run.metrics;
              const source = run.sourceCaseId ? x.runs.find((r) => r.caseId === run.sourceCaseId) : undefined;
              const differs = m?.vsBase && m.vsBase.agreement < 1 - EPSILON;
              return (
                <Fragment key={run.id}>
                  <tr data-run={run.id} data-status={run.status} className={cx(isOpen && "is-open")}>
                    <th scope="row">
                      <button type="button" className="result-toggle mono" aria-expanded={isOpen} aria-label={`${run.id} 자세히`} onClick={() => setOpen(isOpen ? null : run.id)}>
                        {isOpen ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
                        {run.id}
                      </button>
                      <span className="cell-note">{relativeTime(run.startedAt ?? run.createdAt)}</span>
                    </th>
                    <td>
                      <RunStatusMark status={run.status} waitingFor={run.waitingFor} />
                      {run.autoAnswered ? <span className="tag sx-auto">자동 응답 (이전 방식)</span> : null}
                      {run.error ? <span className="result-error">{run.error}</span> : null}
                    </td>
                    <td className="sx-run-what">
                      {item?.title ?? run.item}
                      <span className="cell-note">{variant ? variantName(variant) : run.variant}</span>
                    </td>
                    <td className="sx-run-start">
                      {run.start === "fork" && run.sourceCaseId ? (
                        <>
                          &lsquo;{sectionLabel(sections, run.startSection)}&rsquo; 앞에서 이어 감
                          <span className="cell-note">
                            <CornerDownRight size={11} aria-hidden="true" />{" "}
                            <Link to={`/lab/runs/${run.sourceCaseId}`} className="mono" title="이어 간 실행의 토폴로지">
                              {source ? `${source.id} · ` : ""}
                              {run.sourceCaseId}
                            </Link>
                          </span>
                        </>
                      ) : run.start === "scratch" ? (
                        "처음부터"
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="num">
                      {secs(m?.sectionSec)}
                      {m && m.waitSec > 0 ? <span className="cell-note">응답 대기 {secs(m.waitSec)} 제외</span> : null}
                      {m && m.savedSec > 0 ? <span className="cell-note">{secs(m.savedSec)} 아낌</span> : null}
                    </td>
                    <td className="num">
                      {money(m?.costUsd)}
                      {m && m.savedCostUsd > 0 ? <span className="cell-note">{money(m.savedCostUsd)} 아낌</span> : null}
                    </td>
                    <td>
                      {keyLine(m, x.sections) ?? <span className="muted">—</span>}
                      {m?.vsBase ? <span className={cx("result-mark sx-inline-mark", differs && "is-differs")}>기준과 {share(m.vsBase.agreement)}</span> : null}
                    </td>
                    {scored ? (
                      <td>
                        {run.scores.length ? (
                          <span className="sx-scores">
                            {run.scores.slice(0, 4).map((s) => (
                              <span key={`${s.evaluator}:${s.metric}`} className="sx-score" title={s.detail || undefined}>
                                {s.label} <b className="num">{scoreText(s.value, s.unit)}</b>
                              </span>
                            ))}
                            {run.scores.length > 4 ? <span className="muted">외 {run.scores.length - 4}개 (펼치면 모두)</span> : null}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                        {run.evaluationErrors?.length ? <span className="cell-note sx-count-bad">평가기 {run.evaluationErrors.length}개 실패</span> : null}
                      </td>
                    ) : null}
                    <td className="sx-row-actions">
                      <RunActions x={x} run={run} />
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr className="result-detail-row">
                      <td colSpan={columns}>
                        <RunDetail run={run} base={baseOf(run)} item={item} variant={variant} sections={sections} range={x.sections} onAdd={onAdd} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RunActions({ x, run }: { x: StageExperiment; run: StageRun }) {
  const remove = useDeleteStageRun(x.id);
  const [confirming, setConfirming] = useState(false);
  const removable = run.status !== "queued" && run.status !== "running";
  if (confirming) {
    return (
      <span className="confirm" role="group" aria-label={`${run.id} 지우기 확인`}>
        <span>이 실험에서 뺍니다. 실행은 &lsquo;실행&rsquo; 메뉴에 남습니다.</span>
        <button type="button" className="btn btn-sm btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(run.id, { onSuccess: () => setConfirming(false) })}>
          {remove.isPending ? <Spinner /> : null}
          빼기
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(false)}>
          취소
        </button>
        {remove.isError ? <ErrorState error={remove.error} /> : null}
      </span>
    );
  }
  return (
    <>
      {run.caseId ? (
        <Link to={`/lab/runs/${run.caseId}`} className="btn btn-sm btn-secondary">
          토폴로지
        </Link>
      ) : null}
      {removable ? (
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(true)} aria-label={`${run.id} 실험에서 빼기`}>
          빼기
        </button>
      ) : null}
    </>
  );
}

/* ---------- one run: what it spent, what it reused, what it found ---------- */

type Row = { label: string; value: string };

function resultRows(m: RunMetrics | null | undefined, section: string): Row[] {
  if (!m) return [];
  const list = (items: (string | number | null)[]) => (items.length ? items.map((i) => (i === null || i === "" ? "—" : String(i))).join(" / ") : "없음");
  if (section === "perception" && m.perception) {
    const p = m.perception;
    return [
      { label: "글자", value: list(p.texts) },
      { label: "인물 수", value: String(p.people) },
      { label: "번호판 수", value: String(p.plates) },
      { label: "장면 수", value: String(p.scenes) },
      { label: "장면 설명", value: list(p.sceneLabels) },
      { label: "장소 유형", value: list(p.placeTypes) },
      { label: "장면별 인원", value: list(p.peopleCounts) },
      { label: "추적", value: `얼굴 ${p.faceTracks} · 전신 ${p.bodyTracks}` },
      { label: "설명이 붙은 것", value: `장면 ${p.describedScenes} · 인물 ${p.describedPersons}` },
    ];
  }
  if (section === "analysis" && m.analysis) {
    const a = m.analysis;
    return [
      { label: "보호 요건", value: `${a.requirements}건` },
      { label: "요건의 대상", value: list(a.protected) },
      { label: "판단", value: `적용 ${a.applies} · 불확실 ${a.uncertain} · 미적용 ${a.notApplies}` },
      { label: "질문", value: `${a.questions}개` },
      { label: "검색 · 도구 호출", value: `${a.searches}회 · ${a.toolCalls}회` },
      { label: "인용", value: `${a.citations}건 (미해결 ${a.unresolved})` },
    ];
  }
  if (section === "plan" && m.plan) {
    const p = m.plan;
    return [
      { label: "결정", value: `${p.decisions}건` },
      { label: "가리는 대상", value: list(p.redacted) },
      { label: "유지하는 대상", value: list(p.kept) },
      { label: "충족 못 한 요건", value: `${p.unsatisfied}건` },
      { label: "계획 생성", value: `${p.attempts}회` },
    ];
  }
  if (section === "review" && m.review) {
    const r = m.review;
    return [
      { label: "사용자 수정", value: `${r.edits}건` },
      { label: "감사 발견", value: `${r.findings}건` },
      { label: "감사 라운드", value: `${r.auditRounds}회` },
      { label: "검토 뒤 가리는 대상", value: list(r.redacted) },
    ];
  }
  if (section === "render" && m.render) {
    const r = m.render;
    return [
      { label: "출력 검증", value: `${r.passed} / ${r.rows} 통과` },
      { label: "실패한 검증", value: list(r.failed) },
      { label: "출력 크기", value: r.outputBytes ? bytes(r.outputBytes) : "—" },
      { label: "수정된 프레임", value: r.modifiedFrames === null ? "—" : r.modifiedFrames.toLocaleString() },
      { label: "수정 라운드", value: `${r.repairRounds}회` },
    ];
  }
  return [];
}

/** the longest bar of the step list */
const BAR_PX = 120;

function RunDetail({
  run,
  base,
  item,
  variant,
  sections,
  range,
  onAdd,
}: {
  run: StageRun;
  base: StageRun | undefined;
  item: StageItem | undefined;
  variant: StageVariant | undefined;
  sections: PipelineSection[];
  range: string[];
  onAdd: (seed: LaunchSeed) => void;
}) {
  const m = run.metrics;
  const name = (id: string) => sectionLabel(sections, id);
  // what reuse saved inside the range: the range's sections in front of the checkpoint, and stored step results
  const stored = m ? m.steps.reduce((sum, s) => sum + (s.reused ? s.savedSec : 0), 0) : 0;
  const before = m?.reuse.checkpointSavedSec ?? 0;
  const longest = Math.max(0, ...(m?.steps ?? []).map((s) => (s.reused ? s.savedSec : s.sec)));
  const width = (value: number) => (longest > 0 && value > 0 ? `${Math.max(2, Math.round((value / longest) * BAR_PX))}px` : "0");
  const facts: [string, ReactNode][] = m
    ? [
        ["구간 시간", secs(m.sectionSec)],
        ["실행 전체", secs(m.elapsedSec)],
        ["응답을 기다린 시간", m.waitSec > 0 ? secs(m.waitSec) : "없음"],
        [
          "재사용으로 아낀 시간",
          <>
            {secs(m.savedSec)}
            <span className="muted"> {[before > 0 ? `체크포인트 앞 ${secs(before)}` : "", stored > 0 ? `단계 재사용 ${secs(stored)}` : ""].filter(Boolean).join(" + ")}</span>
          </>,
        ],
        [
          "비용",
          <>
            {money(m.costUsd)}
            {m.savedCostUsd > 0 ? <span className="muted"> 재사용으로 {money(m.savedCostUsd)} 아낌</span> : null}
          </>,
        ],
        ["LLM · 도구 호출", `${m.llmCalls} · ${m.toolCalls}`],
        ["토큰", `입력 ${m.tokensIn.toLocaleString()} · 출력 ${m.tokensOut.toLocaleString()}`],
      ]
    : [];
  return (
    <div className="run-detail">
      <div className="run-detail-head">
        <h3 className="lab-subtitle">
          {run.id} <span className="muted">· {item?.title ?? run.item} · {variant ? variantName(variant) : run.variant}</span>
        </h3>
        {variant ? (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => onAdd(seedOf(variant))}>
            이 조합으로 다른 영상
          </button>
        ) : null}
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => onAdd({ items: [run.item] })}>
          이 영상으로 다른 조합
        </button>
      </div>
      {run.autoAnswered ? (
        <p className="small muted">2026-11-12 이전 방식으로 만든 실행입니다. 구간 안의 질문에 사람이 아니라 자동 응답이 답했습니다. 지금은 이런 실행의 답을 다른 실행이 이어 쓰지 않습니다.</p>
      ) : null}
      {run.error ? (
        <p className="run-detail-error" role="note">
          {run.status === "skipped" ? "건너뛴 이유" : run.status === "waiting" ? "멈춘 이유" : "실패한 이유"}: {run.error}
        </p>
      ) : null}
      {!m ? (
        run.error ? null : (
          <p className="muted small">
            {run.status === "queued"
              ? "아직 차례가 오지 않았습니다."
              : run.status === "waiting"
                ? "사용자의 응답을 기다리고 있습니다. 서비스 화면에서 답하면 이어서 진행하고, 끝나면 결과가 여기에 나타납니다. "
                : "실행 중입니다. 끝나면 결과가 여기에 나타납니다."}
            {run.status === "waiting" && run.caseId ? <Link to={`/cases/${run.caseId}`}>{run.waitingFor ? ANSWER_LABEL[run.waitingFor] : "서비스 화면에서 응답"}</Link> : null}
          </p>
        )
      ) : (
        <>
          <dl className="run-numbers num">
            {facts.map(([term, value]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>

          {m.reuse.before.length ? (
            <p className="small muted">
              구간 앞 단계 {m.reuse.before.length}개({m.reuse.before.map((node) => node.split("/").pop()).join(", ")})도 저장된 결과를 써서 다시 계산하지 않았습니다. 비교에는 넣지 않습니다.
            </p>
          ) : null}

          <div className="run-detail-grid">
            {m.steps.length ? (
              <div className="run-block">
                <h4 className="run-block-title">
                  단계별 실행 <span className="muted">· 다시 계산한 단계와 저장된 결과를 쓴 단계</span>
                </h4>
                <table className="result-rows sx-steps">
                  <tbody>
                    {m.steps.map((s) => (
                      <tr key={s.node} data-step={s.node} data-fate={s.reused ? "store" : "run"}>
                        <th scope="row" title={s.node}>
                          {s.label}
                        </th>
                        <td className="sx-step-fate">{s.reused ? "재사용" : "실행"}</td>
                        <td>
                          <span className={cx("strip-bar", s.reused ? "is-store" : "is-run")} style={{ width: width(s.reused ? s.savedSec : s.sec) }} aria-hidden="true" />
                          <span className="num">
                            {s.reused ? `${secs(s.savedSec)}${s.savedCostUsd > 0 ? ` · ${money(s.savedCostUsd)}` : ""} 아낌` : `${secs(s.sec)}${s.costUsd > 0 ? ` · ${money(s.costUsd)}` : ""}`}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="small muted">재사용한 단계의 막대는 그 결과를 처음 계산할 때 든 시간, 곧 이번에 쓰지 않은 시간입니다.</p>
              </div>
            ) : null}

            {m.vsBase ? (
              <div className="run-block">
                <h4 className="run-block-title">
                  기준과 다른 점 <span className="muted">· {m.vsBase.basis} · {share(m.vsBase.agreement)} 일치</span>
                </h4>
                {m.vsBase.onlyBase.length + m.vsBase.onlyHere.length + (m.vsBase.different?.length ?? 0) === 0 ? (
                  <p className="small muted">이 기준으로는 기준 조합의 실행과 같습니다.</p>
                ) : (
                  <ul className="diff-list">
                    {m.vsBase.onlyBase.map((t) => (
                      <li key={`b-${t}`} className="is-lost">
                        <span className="diff-side">기준에만</span> {t}
                      </li>
                    ))}
                    {m.vsBase.onlyHere.map((t) => (
                      <li key={`h-${t}`} className="is-new">
                        <span className="diff-side">이 조합에만</span> {t}
                      </li>
                    ))}
                    {(m.vsBase.different ?? []).map((t) => (
                      <li key={`d-${t}`} className="is-changed">
                        <span className="diff-side">값이 다름</span> {t}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}

            {run.scores.length || run.evaluationErrors?.length ? (
              <div className="run-block">
                <h4 className="run-block-title">점수</h4>
                <table className="result-rows sx-score-rows">
                  <tbody>
                    {run.scores.map((s) => (
                      <tr key={`${s.evaluator}:${s.metric}`} data-metric={s.metric}>
                        <th scope="row" title={s.metric}>
                          {s.label}
                        </th>
                        <td className="num">{scoreText(s.value, s.unit)}</td>
                        <td className="result-base">
                          {[s.detail, s.section ? name(s.section) : "", s.evaluator].filter(Boolean).join(" · ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {run.evaluationErrors?.map((e) => (
                  <p key={e} className="small sx-count-bad">
                    평가기 실패 — {e}
                  </p>
                ))}
              </div>
            ) : null}
          </div>

          {range.map((section) => {
            const mine = resultRows(m, section);
            if (!mine.length) return null;
            const theirs = new Map(resultRows(base?.metrics, section).map((r) => [r.label, r.value]));
            return (
              <div key={section} className="run-block">
                <h4 className="run-block-title">{name(section)} 결과</h4>
                <table className="result-rows">
                  <tbody>
                    {mine.map((r) => {
                      const other = theirs.get(r.label);
                      const differs = other !== undefined && other !== r.value;
                      return (
                        <tr key={r.label} className={cx(differs && "is-diff")}>
                          <th scope="row">{r.label}</th>
                          <td>{r.value}</td>
                          <td className="result-base">{differs ? <>기준: {other}</> : null}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
