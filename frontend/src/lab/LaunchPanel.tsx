// One launch: the runs to add to a section experiment. Used when an experiment is made (its first launch) and by
// "실행 추가" inside one. The person chooses candidates per part and the videos; what will actually run — where
// each run starts, what it will ask, and why it cannot start — is the server's answer to `POST /preview`, shown
// before anything is started.
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { useQueries } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, TriangleAlert, X } from "lucide-react";
import type { AdapterKind, CatalogCandidate, CatalogParam, CatalogSlot, ProfileCheck, ProfileSummary } from "../api/contract";
import { profileCheckOptions, useCatalog } from "../api/hooks";
import { type Launch, type LaunchPlan, type LaunchTarget, type PipelineSection, type PlannedRun, type PlannedVariant, type VideoSet, useLaunchPlan, useVideoSets } from "../api/stage";
import { relativeTime } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { CAP_LABEL, KindBadge, SendTag, priceText } from "./pipelineParts";
import { VariantName, sectionLabel, secs, variantName } from "./stageParts";

/** why the catalog itself rules a candidate out, or null (what this backend cannot run comes from the profile check) */
function unavailable(slot: CatalogSlot, c: CatalogCandidate): string | null {
  if (c.status === "planned") return "구현 예정";
  const missing = c.adapter === "none" ? [] : slot.requires.filter((cap) => !(c.caps ?? []).includes(cap));
  return missing.length ? `${missing.map((m) => CAP_LABEL[m]).join(" · ")} 기능 없음` : null;
}

/** what a launch panel starts with: "이 조합으로 다른 영상", "이 영상으로 다른 조합", an empty cell of the grid */
export interface LaunchSeed {
  matrix?: Record<string, string[]>;
  paramMatrix?: Record<string, number[]>;
  items?: string[];
}

interface Props {
  target: LaunchTarget;
  /** the sections of the range, in order */
  range: PipelineSection[];
  /** every section of the pipeline, to name the one a run starts at */
  sections: PipelineSection[];
  /** the base profile: a part that is not chosen keeps its candidate */
  base: ProfileSummary;
  /** the range asks the user: one combination and one video at a time */
  interactive: boolean;
  seed?: LaunchSeed;
  /** changes when the runs a launch could continue change */
  revision?: string;
  submitLabel: (runs: number) => string;
  pending: boolean;
  error: unknown;
  onSubmit: (launch: Launch) => void;
  onCancel?: () => void;
  /** fields of the screen around the panel that belong next to the start button (an experiment's title and cost cap) */
  children?: ReactNode;
  /** what the screen around the panel will not start with */
  blocked?: string[];
  /** told every time the choice changes */
  onChange?: (seed: LaunchSeed) => void;
}

export function LaunchPanel({ target, range, sections, base, interactive, seed, revision, submitLabel, pending, error, onSubmit, onCancel, children, blocked = [], onChange }: Props) {
  const catalog = useCatalog();
  const sets = useVideoSets();
  // a part or value that is not listed here keeps the base profile's own
  const [picked, setPicked] = useState<Record<string, string[]>>(() => seed?.matrix ?? {});
  const [values, setValues] = useState<Record<string, number[]>>(() => seed?.paramMatrix ?? {});
  const [chosenVideos, setChosenVideos] = useState<string[] | null>(() => seed?.items ?? null);
  const [open, setOpen] = useState<string[]>(() => [...Object.keys(seed?.matrix ?? {}), ...Object.keys(seed?.paramMatrix ?? {})]);
  /** the run to continue, when the person chose another one than the server's */
  const [source, setSource] = useState<string | null>(null);

  const slots = useMemo(() => new Map((catalog.data?.stages ?? []).flatMap((s) => s.slots).map((s) => [s.id, s])), [catalog.data]);
  const params = useMemo(() => new Map((catalog.data?.params ?? []).map((p) => [p.id, p])), [catalog.data]);
  const kinds = useMemo(() => new Map((catalog.data?.adapters ?? []).map((a) => [a.id, a.kind])), [catalog.data]);

  const baseCandidate = (slot: CatalogSlot) => base.selections[slot.id] ?? slot.default;
  const baseValue = (param: CatalogParam) => base.params[param.id] ?? param.default;
  const chosenOf = (slot: CatalogSlot) => {
    const list = (picked[slot.id] ?? []).filter((id) => slot.candidates.some((c) => c.id === id));
    return list.length ? (interactive ? list.slice(0, 1) : list) : [baseCandidate(slot)];
  };
  const valuesOf = (param: CatalogParam) => {
    const list = (values[param.id] ?? []).filter((v) => v >= param.min && v <= param.max);
    return list.length ? (interactive ? list.slice(0, 1) : list) : [baseValue(param)];
  };

  const setList = sets.data ?? [];
  const usable = setList.filter((s) => s.items.some((i) => !i.missing));
  const allVideos = usable.flatMap((set) => set.items.filter((i) => !i.missing).map((item) => ({ key: `${set.id}/${item.id}`, set, item })));
  // nothing chosen yet and only one video to choose: that one
  const wanted = chosenVideos ?? (allVideos.length === 1 ? [allVideos[0].key] : []);
  const videos = allVideos.filter((v) => wanted.includes(v.key)).slice(0, interactive ? 1 : undefined);

  const matrix: Record<string, string[]> = {};
  const paramMatrix: Record<string, number[]> = {};
  for (const section of range) {
    for (const id of section.slots) {
      const slot = slots.get(id);
      if (!slot) continue;
      const list = chosenOf(slot);
      if (list.length !== 1 || list[0] !== baseCandidate(slot)) matrix[id] = list;
    }
    for (const id of section.params) {
      const param = params.get(id);
      if (!param) continue;
      const list = valuesOf(param);
      if (list.length !== 1 || list[0] !== baseValue(param)) paramMatrix[id] = list;
    }
  }
  const choice: Launch = { matrix, paramMatrix, datasets: [...new Set(videos.map((v) => v.set.id))], items: videos.map((v) => v.key) };
  const choiceKey = JSON.stringify(choice);
  const launch: Launch = source ? { ...choice, source } : choice;
  const { plan, stale, error: planError, refetch } = useLaunchPlan(catalog.data && sets.data ? target : null, launch, revision);

  // another choice: the server picks the run to continue again
  useEffect(() => {
    setSource(null);
    onChange?.({ matrix, paramMatrix, items: videos.map((v) => v.key) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choiceKey]);

  if (catalog.isLoading) return <Skeleton lines={6} />;
  if (catalog.isError) return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />;

  const toggleOpen = (id: string) => setOpen(open.includes(id) ? open.filter((x) => x !== id) : [...open, id]);
  const problems = [...blocked, ...(plan && !stale ? plan.problems : [])];
  const ready = !!plan && !stale && !planError && problems.length === 0;

  return (
    <div className="editor sx-launch">
      <div className="editor-main">
        <section className="editor-section" aria-labelledby="sx-videos-title">
          <div className="editor-stage-head">
            <h3 id="sx-videos-title" className="lab-section-title">
              영상
            </h3>
            <p className="lab-note">{interactive ? "실행할 영상 하나를 고릅니다." : "고른 조합이 고른 영상마다 한 번씩 실행됩니다. 데이터셋을 고르면 그 안의 영상이 모두 들어갑니다."}</p>
          </div>
          {sets.isLoading ? <Skeleton lines={2} /> : null}
          {sets.isError ? <ErrorState error={sets.error} onRetry={() => void sets.refetch()} /> : null}
          {sets.data && usable.length === 0 ? (
            <EmptyState
              title="영상이 든 데이터셋이 없습니다"
              action={
                <Link to="/lab/datasets" className="btn btn-sm btn-secondary">
                  영상 데이터셋 만들기
                </Link>
              }
            >
              영상을 올리거나 기존 실행의 영상을 데이터셋에 넣으면 여기에서 고를 수 있습니다.
            </EmptyState>
          ) : null}
          {usable.length ? (
            <ul className="pick-list">
              {usable.map((set) => (
                <SetPick
                  key={set.id}
                  set={set}
                  chosen={videos.map((v) => v.key)}
                  single={interactive}
                  open={interactive || open.includes(`set:${set.id}`)}
                  onToggle={() => toggleOpen(`set:${set.id}`)}
                  onChange={setChosenVideos}
                />
              ))}
            </ul>
          ) : null}
        </section>

        {range.map((section) => (
          <section key={section.id} className="editor-section" aria-labelledby={`sx-parts-${section.id}`}>
            <div className="editor-stage-head">
              <h3 id={`sx-parts-${section.id}`} className="lab-section-title">
                {section.label} — 부품
              </h3>
              <p className="lab-note">
                {section.slots.length + section.params.length === 0
                  ? "이 구간에는 바꿀 수 있는 부품이 없습니다. 기준 프로필 그대로 실행합니다."
                  : interactive
                    ? "부품마다 후보 하나를 고릅니다. 고르지 않은 부품은 기준 프로필의 것을 씁니다."
                    : "부품마다 실행할 후보를 고릅니다. 기준 프로필의 후보가 미리 골라져 있고, 여러 개를 고르면 고른 후보끼리 곱한 조합이 모두 실행됩니다."}
              </p>
            </div>
            {section.slots.some((id) => slots.has(id)) ? (
              <ul className="pick-list">
                {section.slots.map((id) => {
                  const slot = slots.get(id);
                  if (!slot) return null;
                  return (
                    <SlotPick
                      key={id}
                      slot={slot}
                      base={base}
                      baseId={baseCandidate(slot)}
                      chosen={chosenOf(slot)}
                      single={interactive}
                      kinds={kinds}
                      open={open.includes(id)}
                      onToggle={() => toggleOpen(id)}
                      onChange={(ids) => setPicked({ ...picked, [id]: ids })}
                    />
                  );
                })}
              </ul>
            ) : null}
            {section.params.some((id) => params.has(id)) ? (
              <>
                <h4 className="lab-subtitle">설정값</h4>
                <ul className="pick-list">
                  {section.params.map((id) => {
                    const param = params.get(id);
                    if (!param) return null;
                    return (
                      <ParamPick
                        key={id}
                        param={param}
                        baseValue={baseValue(param)}
                        chosen={valuesOf(param)}
                        single={interactive}
                        open={open.includes(id)}
                        onToggle={() => toggleOpen(id)}
                        onChange={(list) => setValues({ ...values, [id]: list })}
                      />
                    );
                  })}
                </ul>
              </>
            ) : null}
          </section>
        ))}
      </div>

      <aside className="editor-side" aria-label="실행 계획">
        <div className="editor-summary sx-plan" aria-live="polite" aria-busy={stale}>
          <p className="summary-label">실행 계획</p>
          <p className="sx-plan-count num" data-testid="sx-run-count">
            실행 {plan ? plan.runs.length : "…"}건
            {stale && !planError ? <Spinner size={14} label="계획을 다시 계산하는 중" /> : null}
          </p>
          {plan ? (
            <p className="sx-plan-sub num">
              조합 {plan.variants.length}개 × 영상 {new Set(plan.runs.map((r) => r.item)).size}개
            </p>
          ) : null}

          {planError ? <ErrorState error={planError} onRetry={() => void refetch()} /> : null}
          {plan ? <PlanRuns plan={plan} sections={sections} baseLeaves={base.estimate.framesLeaveDevice} source={source} onSource={setSource} /> : planError ? null : <Skeleton lines={3} />}

          {children ? <div className="sx-plan-fields">{children}</div> : null}

          <div className="summary-actions">
            <button type="button" className="btn btn-primary" disabled={!ready || pending} onClick={() => onSubmit(launch)}>
              {pending ? <Spinner /> : null}
              {submitLabel(plan?.runs.length ?? 0)}
            </button>
            {onCancel ? (
              <button type="button" className="btn btn-ghost" onClick={onCancel}>
                취소
              </button>
            ) : null}
          </div>
          {problems.length ? (
            <ul className="sx-problems" aria-label="시작할 수 없는 이유">
              {problems.map((p) => (
                <li key={p}>
                  <TriangleAlert size={13} aria-hidden="true" />
                  <span>{p}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {error ? <ErrorState error={error} /> : null}
        </div>
      </aside>
    </div>
  );
}

/* ---------- the plan: the runs a launch will create ---------- */

function PlanRuns({
  plan,
  sections,
  baseLeaves,
  source,
  onSource,
}: {
  plan: LaunchPlan;
  sections: PipelineSection[];
  /** the base profile sends frames to cloud models */
  baseLeaves: boolean;
  /** the run to continue that the person chose, when it is not the server's first choice */
  source: string | null;
  onSource: (caseId: string | null) => void;
}) {
  const label = (id: string | null) => sectionLabel(sections, id);
  const variant = (key: string) => plan.variants.find((v) => v.key === key);
  const kinds = new Map(sections.flatMap((s) => s.interactions.map((i) => [i.kind, i.label] as const)));
  /** what a run that can start will ask, in order */
  const asked = (run: PlannedRun) => (run.asks.length && !run.problem ? run.asks.map((kind) => kinds.get(kind) ?? kind).join(" → ") : null);
  if (!plan.runs.length) return <p className="muted small">영상을 고르면 실행할 목록이 여기에 나타납니다.</p>;
  /** a combination that differs from the base profile in whether frames leave this machine says so */
  const frames = (v: { framesLeaveDevice: boolean } | undefined) =>
    v && v.framesLeaveDevice !== baseLeaves ? <span className="cell-note">{v.framesLeaveDevice ? "기준과 달리 프레임을 외부 모델로 보냄" : "기준과 달리 프레임을 밖으로 보내지 않음"}</span> : null;
  /**
   * The steps of the range a combination computes again because it differs from the base profile, by name and in
   * pipeline order: said once for every combination this launch adds to the experiment.
   */
  const steps = sections.flatMap((s) => (plan.sections.includes(s.id) ? s.steps : []));
  const again = (v: PlannedVariant | undefined) => {
    if (!v || v.base || v.id !== null || !v.recomputes.length) return null;
    const names = steps.filter((step) => v.recomputes.includes(step.node)).map((step) => step.label);
    return names.length === steps.length && steps.length > 3 ? "구간의 모든 단계" : names.join(" · ");
  };
  /** the run it continues went through a section that asks: those answers come with its checkpoint */
  const answered = (run: PlannedRun) => sections.slice(0, sections.findIndex((s) => s.id === run.startSection)).some((s) => s.interactions.length > 0);

  const start = (run: PlannedRun) =>
    run.start === "fork" && run.source ? (
      <>
        &lsquo;{label(run.startSection)}&rsquo; 앞에서 이어 감{" "}
        <span className="sx-plan-source">
          ←{" "}
          <Link to={`/lab/runs/${run.source.caseId}`} className="mono" title={run.source.title}>
            {run.source.caseId}
          </Link>{" "}
          <span className="muted">{relativeTime(run.source.startedAt)}</span>
        </span>
      </>
    ) : (
      "처음부터"
    );

  // one run: everything about it, and the choice of the run to continue
  if (plan.runs.length === 1) {
    const run = plan.runs[0];
    const v = variant(run.variant);
    const offered = run.candidates;
    const best = offered[0]?.caseId;
    const taken = source ?? run.source?.caseId ?? best;
    return (
      <dl className="sx-plan-one" data-start={run.start}>
        <div>
          <dt>영상</dt>
          <dd>{run.itemTitle}</dd>
        </div>
        <div>
          <dt>조합</dt>
          <dd>
            {v ? <VariantName variant={v} /> : run.variant}
            {again(v) ? <span className="cell-note sx-plan-again">새 조합 · 기준과 달라 다시 계산: {again(v)}</span> : null}
            {frames(v)}
          </dd>
        </div>
        <div>
          <dt>시작</dt>
          <dd>
            {start(run)}
            {run.start === "fork" ? (
              <span className="cell-note">{answered(run) ? "그 앞 단계는 다시 실행하지 않고, 그때 사용자가 한 답을 그대로 씁니다. 다시 묻지 않습니다." : "그 앞 단계는 다시 실행하지 않습니다."}</span>
            ) : null}
          </dd>
        </div>
        {offered.length > 1 ? (
          <div>
            <dt>
              <label htmlFor="sx-source">이어 갈 실행</label>
            </dt>
            <dd>
              <select id="sx-source" className="select" value={taken} onChange={(e) => onSource(e.target.value === best ? null : e.target.value)}>
                {offered.map((c, i) => (
                  <option key={c.caseId} value={c.caseId}>
                    {c.caseId} · {relativeTime(c.startedAt)} · ‘{label(c.startSection)}’ 앞에서{i === 0 ? " (추천)" : ""}
                  </option>
                ))}
              </select>
              <span className="cell-note">{offered.find((c) => c.caseId === taken)?.title}</span>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>사용자에게 묻는 것</dt>
          <dd>
            {asked(run) ?? <span className="muted">{run.problem ? "—" : "없음 — 끝까지 혼자 실행됩니다"}</span>}
            {asked(run) ? <span className="cell-note">묻는 지점마다 멈춥니다. 서비스 화면에서 직접 답해야 이어집니다.</span> : null}
          </dd>
        </div>
        {run.problem ? (
          <div className="sx-plan-problem">
            <dt>시작할 수 없음</dt>
            <dd>{run.problem}</dd>
          </div>
        ) : null}
      </dl>
    );
  }

  const items = [...new Set(plan.runs.map((r) => r.item))];
  const asks = plan.runs.some((r) => asked(r) !== null);
  const added = plan.variants.filter((v) => again(v));
  return (
    <>
      {added.length ? (
        <ul className="sx-plan-new" aria-label="이 실행으로 더해지는 조합">
          {added.map((v) => (
            <li key={v.key}>
              <b>{variantName(v)}</b> — 기준과 달라 다시 계산: {again(v)}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="sx-plan-scroll">
        <table className="table sx-plan-table">
          <thead>
            <tr>
              <th scope="col">조합</th>
              <th scope="col">시작</th>
              {asks ? <th scope="col">묻는 것</th> : null}
            </tr>
          </thead>
          {items.map((item) => {
            const runs = plan.runs.filter((r) => r.item === item);
            return (
              <tbody key={item} data-item={item}>
                <tr className="sx-plan-video">
                  <th scope="rowgroup" colSpan={asks ? 3 : 2}>
                    {runs[0].itemTitle}
                  </th>
                </tr>
                {runs.map((run, index) => {
                  const v = variant(run.variant);
                  // the same reason is written out once per video
                  const repeated = !!run.problem && runs.slice(0, index).some((r) => r.problem === run.problem);
                  return (
                    <tr key={run.variant} className={cx(run.problem && "is-problem")} data-start={run.start}>
                      <td>
                        {v ? <VariantName variant={v} /> : run.variant}
                        {frames(v)}
                      </td>
                      <td>
                        {run.problem ? (
                          <span className="sx-plan-refused">
                            <TriangleAlert size={12} aria-hidden="true" /> 시작할 수 없음
                            <span className="cell-note">{repeated ? "위 조합과 같은 이유" : run.problem}</span>
                          </span>
                        ) : (
                          start(run)
                        )}
                      </td>
                      {asks ? <td>{asked(run) ?? <span className="muted">없음</span>}</td> : null}
                    </tr>
                  );
                })}
              </tbody>
            );
          })}
        </table>
      </div>
    </>
  );
}

/* ---------- one part: which of its candidates the launch runs ---------- */

function SlotPick({
  slot,
  base,
  baseId,
  chosen,
  single,
  kinds,
  open,
  onToggle,
  onChange,
}: {
  slot: CatalogSlot;
  base: ProfileSummary;
  baseId: string;
  chosen: string[];
  single: boolean;
  kinds: Map<string, AdapterKind>;
  open: boolean;
  onToggle: () => void;
  onChange: (ids: string[]) => void;
}) {
  const others = slot.candidates.filter((c) => c.id !== baseId);
  const panel = `pick-${slot.id.replace(/\./g, "-")}`;
  const label = (c: CatalogCandidate | undefined, id: string) => c?.short ?? c?.label ?? id;
  const onlyBase = chosen.length === 1 && chosen[0] === baseId;
  // Whether this backend can run a candidate (an adapter that is not there yet, a key that is missing) is not in
  // the catalog: the profile check says it. Asked once the row is opened, for the base profile with that one swap.
  const probes = useQueries({
    queries: slot.candidates.map((c) => ({
      ...profileCheckOptions({ selections: { ...base.selections, [slot.id]: c.id }, params: base.params }),
      enabled: open && c.id !== baseId && !unavailable(slot, c),
    })),
  });
  // an error the base profile does not have itself comes from the swap, whichever part the check files it under
  const own = new Set(base.issues.filter((i) => i.level === "error").map((i) => i.text));
  const refusal = (index: number) => (probes[index]?.data as ProfileCheck | undefined)?.issues.find((i) => i.level === "error" && !own.has(i.text))?.text ?? null;
  return (
    <li className={cx("pick", !onlyBase && "is-on", open && "is-open")}>
      <button type="button" className="pick-head" aria-expanded={open} aria-controls={panel} onClick={onToggle} disabled={others.length === 0}>
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <span className="pick-name">{slot.label}</span>
        <span className="pick-state">
          {others.length === 0 ? (
            <span className="muted">{label(slot.candidates.find((c) => c.id === baseId), baseId)} · 다른 후보 없음</span>
          ) : onlyBase ? (
            <span className="muted">기준 그대로 · {label(slot.candidates.find((c) => c.id === baseId), baseId)}</span>
          ) : (
            chosen.map((id) => (
              <span key={id} className={cx("pick-chip", id === baseId && "is-base")}>
                {id === baseId ? "기준 " : ""}
                {label(slot.candidates.find((c) => c.id === id), id)}
              </span>
            ))
          )}
        </span>
      </button>
      {open ? (
        <div id={panel} className="pick-panel">
          <ul className="pick-options" role={single ? "radiogroup" : undefined} aria-label={single ? slot.label : undefined}>
            {slot.candidates.map((c, index) => {
              const isBase = c.id === baseId;
              const on = chosen.includes(c.id);
              const reason = isBase ? null : (unavailable(slot, c) ?? refusal(index));
              const kind = kinds.get(c.adapter);
              // the last one chosen stays: a part always runs with something
              const last = on && chosen.length === 1;
              return (
                <li key={c.id} className={cx("pick-option", !!reason && "is-off")}>
                  <label className="check">
                    {single ? (
                      <input type="radio" name={panel} checked={on} disabled={!!reason && !on} onChange={() => onChange([c.id])} />
                    ) : (
                      <input
                        type="checkbox"
                        checked={on}
                        // one that was chosen before the check answered can still be taken out
                        disabled={(!!reason && !on) || (last && isBase)}
                        onChange={(e) => onChange(e.target.checked ? [...chosen, c.id] : last ? [baseId] : chosen.filter((x) => x !== c.id))}
                      />
                    )}
                    <span>{c.label}</span>
                  </label>
                  {isBase ? <span className="tag tag-base">기준 프로필 그대로</span> : null}
                  {kind ? <KindBadge kind={kind} /> : null}
                  {c.price ? <span className="mono muted small">{priceText(c.price)}</span> : null}
                  {reason ? <span className="pick-reason">{reason}</span> : null}
                  {reason && c.note ? <span className="pick-note">{c.note}</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

function ParamPick({
  param,
  baseValue,
  chosen,
  single,
  open,
  onToggle,
  onChange,
}: {
  param: CatalogParam;
  baseValue: number;
  chosen: number[];
  single: boolean;
  open: boolean;
  onToggle: () => void;
  onChange: (values: number[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const panel = `pick-${param.id.replace(/\./g, "-")}`;
  const value = Number(draft);
  const valid = draft !== "" && Number.isFinite(value) && value >= param.min && value <= param.max && !chosen.includes(value);
  const onlyBase = chosen.length === 1 && chosen[0] === baseValue;
  const add = () => {
    if (!valid) return;
    onChange(single ? [value] : [...chosen, value].sort((a, b) => a - b));
    setDraft("");
  };
  return (
    <li className={cx("pick", !onlyBase && "is-on", open && "is-open")}>
      <button type="button" className="pick-head" aria-expanded={open} aria-controls={panel} onClick={onToggle}>
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <span className="pick-name">{param.label}</span>
        <span className="pick-state">
          {onlyBase ? (
            <span className="muted num">
              기준 그대로 · {baseValue} {param.unit}
            </span>
          ) : (
            chosen.map((v) => (
              <span key={v} className={cx("pick-chip num", v === baseValue && "is-base")}>
                {v === baseValue ? "기준 " : ""}
                {v}
              </span>
            ))
          )}
        </span>
      </button>
      {open ? (
        <div id={panel} className="pick-panel">
          <div className="pick-values">
            {chosen.map((v) => (
              <span key={v} className="pick-value num">
                {v} {param.unit}
                {v === baseValue ? <span className="tag tag-base">기준 프로필 그대로</span> : null}
                {chosen.length > 1 || v !== baseValue ? (
                  <button
                    type="button"
                    className="pick-remove"
                    onClick={() => onChange(chosen.length > 1 ? chosen.filter((x) => x !== v) : [baseValue])}
                    aria-label={`${param.label} ${v} 빼기`}
                  >
                    <X size={12} aria-hidden="true" />
                  </button>
                ) : null}
              </span>
            ))}
            <span className="pick-add">
              <input
                className="input num"
                type="number"
                min={param.min}
                max={param.max}
                step={param.step}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    add();
                  }
                }}
                aria-label={single ? `${param.label}에 쓸 값` : `${param.label}에 더할 값`}
                placeholder={`${param.min}~${param.max}`}
              />
              <button type="button" className="btn btn-sm btn-secondary" onClick={add} disabled={!valid}>
                {single ? "이 값으로" : "값 추가"}
              </button>
            </span>
            {!single && !chosen.includes(baseValue) ? (
              <button type="button" className="link-button small" onClick={() => onChange([...chosen, baseValue].sort((a, b) => a - b))}>
                기준값 {baseValue}도 실행
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

/* ---------- one dataset: all of its videos, or some ---------- */

function SetPick({
  set,
  chosen,
  single,
  open,
  onToggle,
  onChange,
}: {
  set: VideoSet;
  chosen: string[];
  single: boolean;
  open: boolean;
  onToggle: () => void;
  onChange: (keys: string[]) => void;
}) {
  const items = set.items.filter((i) => !i.missing);
  const keys = items.map((i) => `${set.id}/${i.id}`);
  const used = keys.filter((k) => chosen.includes(k));
  const panel = `pick-set-${set.id}`;
  const all = used.length === items.length;
  return (
    <li className={cx("pick", used.length > 0 && "is-on", open && "is-open")}>
      <div className="pick-head pick-head-set">
        {single ? (
          <span className="pick-name">{set.name}</span>
        ) : (
          <label className="check">
            <input
              type="checkbox"
              checked={all}
              ref={(el) => {
                if (el) el.indeterminate = used.length > 0 && !all;
              }}
              onChange={(e) => onChange(e.target.checked ? [...new Set([...chosen, ...keys])] : chosen.filter((k) => !keys.includes(k)))}
            />
            <span className="pick-name">{set.name}</span>
          </label>
        )}
        <SendTag sendable={set.sendable} />
        <span className="pick-state num">
          {used.length > 0 && !all ? `영상 ${used.length} / ${items.length}개` : `영상 ${items.length}개`}
          {!single && items.length > 1 ? (
            <button type="button" className="link-button" aria-expanded={open} aria-controls={panel} onClick={onToggle}>
              {open ? "접기" : "영상 고르기"}
            </button>
          ) : null}
        </span>
      </div>
      {open ? (
        <div id={panel} className="pick-panel">
          <ul className="pick-options" role={single ? "radiogroup" : undefined} aria-label={single ? `${set.name}의 영상` : undefined}>
            {items.map((item) => {
              const key = `${set.id}/${item.id}`;
              const on = chosen.includes(key);
              return (
                <li key={item.id} className="pick-option">
                  <label className="check">
                    {single ? (
                      <input type="radio" name="sx-video" checked={on} onChange={() => onChange([key])} />
                    ) : (
                      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked ? [...chosen, key] : chosen.filter((k) => k !== key))} />
                    )}
                    <span>{item.title}</span>
                  </label>
                  <span className="muted small num">{secs(item.durationSec)}</span>
                  {item.expected ? <span className="tag">정답 있음</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </li>
  );
}
