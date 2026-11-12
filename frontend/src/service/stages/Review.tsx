import { useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import type { CaseDetail, Decision, Method, Question, Range, Requirement, Shape, Target } from "../../api/contract";
import { useAnswerQuestions, useGeometry, useSubmitReview, useUpdateFact } from "../../api/hooks";
import { METHOD_LABEL, SHAPE_LABEL, methodLabel, rangeText } from "../../lib/format";
import { CitationList, ErrorState, ProvenanceTag, RiskTag, Spinner, cx } from "../../ui";
import { CasePlayer, caseDuration } from "../media";
import { isOnScreen } from "../media/geometry";
import { DecisionTimeline } from "../scene/Player";

type Patch = Partial<Pick<Decision, "method" | "shape" | "ranges">>;
type Tab = "decisions" | "facts" | "questions";

const RISK_ORDER = { high: 0, medium: 1, uncertain: 2, low: 3 } as const;
/** something in the picture is kept or covered with the mosaic; information in the file is kept or removed */
const ON_SCREEN_METHODS: Method[] = ["keep", "mosaic"];
const IN_FILE_METHODS: Method[] = ["keep", "remove"];
const METHOD_HINT: Record<Method, string> = {
  keep: "그대로 둡니다",
  mosaic: "알아볼 수 없는 고정 크기의 모자이크로 덮습니다",
  remove: "출력 파일에서 이 정보를 지웁니다",
};
const SHAPES: Shape[] = ["ellipse", "box", "outline"];

/** where the preview opens: the mock's illustrated scene has a fixed good moment; a real video opens inside the riskiest decision that is on screen */
function openingTime(c: CaseDetail, duration: number): number {
  if (!c.media) return 12;
  const first = [...(c.plan?.decisions ?? [])].filter((d) => d.ranges.length > 0).sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk] || a.id.localeCompare(b.id))[0];
  const range = first?.ranges[0];
  if (!range) return Math.min(1, duration / 3);
  return Math.min(duration, range[0] + Math.min(0.6, (range[1] - range[0]) / 2));
}

const sameRanges = (a: Range[], b: Range[]) => a.length === b.length && a.every((r, i) => Math.abs(r[0] - b[i][0]) < 0.0005 && Math.abs(r[1] - b[i][1]) < 0.0005);

export function ReviewStage({ c }: { c: CaseDetail }) {
  const plan = c.plan;
  const duration = caseDuration(c);
  const [t, setT] = useState(() => openingTime(c, duration));
  const [selected, setSelected] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, Patch>>({});
  const [tab, setTab] = useState<Tab>("decisions");
  const [notice, setNotice] = useState<string | null>(null);
  const submit = useSubmitReview(c.id);
  const lastPlan = useRef(plan);

  // When the server re-evaluates (late answer or fact edit), drop local edits on decisions it changed.
  useEffect(() => {
    const prev = lastPlan.current;
    lastPlan.current = plan;
    if (!prev || !plan || prev.version === plan.version) return;
    // a new plan numbers its decisions again, so an id can now name another target: an edit follows its target, not the id
    const changed = plan.decisions.filter((d) => {
      const before = prev.decisions.find((x) => x.id === d.id);
      return !before || before.method !== d.method || before.targetId !== d.targetId || before.scope !== d.scope;
    });
    const gone = prev.decisions.filter((d) => !plan.decisions.some((x) => x.id === d.id));
    if (changed.length || gone.length) {
      setEdits((current) => {
        const next = { ...current };
        for (const d of [...changed, ...gone]) delete next[d.id];
        return next;
      });
    }
    if (changed.length) {
      setNotice(`답변을 반영해 결정 ${changed.map((d) => d.id).join(", ")}이(가) 바뀌었습니다.`);
    } else {
      setNotice("다시 평가했습니다. 결정은 그대로입니다.");
    }
  }, [plan]);

  const decisions = useMemo(
    () =>
      (plan?.decisions ?? [])
        .map((d) => (edits[d.id] ? { ...d, ...edits[d.id], edited: true } : d))
        .sort((a, b) => RISK_ORDER[a.risk] - RISK_ORDER[b.risk] || a.id.localeCompare(b.id)),
    [plan, edits],
  );
  // the same request the player makes: its answer also says which ranges the renderer will really use
  const geometry = useGeometry(c, decisions);

  if (!plan) return null;
  const editedCount = Object.keys(edits).length;
  const deferred = c.questions.filter((q) => q.status === "deferred");
  const focus = selected ?? decisions[0]?.id ?? null;

  const patch = (id: string, value: Patch) => {
    const original = plan.decisions.find((d) => d.id === id);
    setEdits((current) => {
      const merged = { ...current[id], ...value };
      const same =
        original &&
        (merged.method ?? original.method) === original.method &&
        (merged.shape ?? original.shape) === original.shape &&
        JSON.stringify(merged.ranges ?? original.ranges) === JSON.stringify(original.ranges);
      const next = { ...current };
      if (same) delete next[id];
      else next[id] = merged;
      return next;
    });
  };

  const seekTo = (d: Decision) => {
    setSelected(d.id);
    const range = d.ranges[0];
    if (range) setT(Math.min(duration, range[0] + (c.media ? Math.min(0.6, (range[1] - range[0]) / 2) : 0.6)));
  };

  return (
    <div className="review">
      <div className="review-media">
        <CasePlayer c={c} t={t} onTime={setT} decisions={decisions} mode="review" highlight={focus} />
        <DecisionTimeline t={t} segments={c.segments} decisions={decisions} selected={focus} onSeek={setT} onSelect={setSelected} duration={duration} />
        <p className="review-legend muted small">
          {c.media
            ? "지금 계획대로 모자이크를 입힌 미리보기입니다. 고른 결정은 테두리로 표시합니다(점선 모자이크 · 실선 유지)."
            : "미리보기의 점선은 모자이크할 영역, 실선은 그대로 두는 대상입니다."}{" "}
          모자이크는 대상 종류마다 알아볼 수 없는 고정 크기로 적용하며 강도는 조절하지 않습니다.
        </p>
      </div>

      <div className="review-panel">
        <div className="tabs" role="tablist" aria-label="검토 항목">
          <TabButton id="decisions" tab={tab} setTab={setTab} label="결정" count={decisions.length} />
          <TabButton id="facts" tab={tab} setTab={setTab} label="사실" count={c.facts.length} />
          <TabButton id="questions" tab={tab} setTab={setTab} label="미답 질문" count={deferred.length} attention={deferred.length > 0} />
        </div>
        {notice ? (
          <p className="notice" role="status">
            {notice}
            <button type="button" className="link-button" onClick={() => setNotice(null)}>
              닫기
            </button>
          </p>
        ) : null}

        {tab === "decisions" ? (
          <div className="decision-list" role="tabpanel" aria-label="결정">
            {decisions.map((d) => (
              <DecisionCard
                key={d.id}
                d={d}
                original={plan.decisions.find((x) => x.id === d.id) as Decision}
                target={c.targets.find((target) => target.id === d.targetId)}
                requirements={c.requirements}
                applied={geometry.settled ? geometry.data?.decisions[d.id]?.ranges : undefined}
                max={duration}
                selected={focus === d.id}
                onSelect={() => setSelected(d.id)}
                onSeek={() => seekTo(d)}
                onPatch={(value) => patch(d.id, value)}
                onReset={() => patch(d.id, plan.decisions.find((x) => x.id === d.id) as Patch)}
              />
            ))}
          </div>
        ) : null}
        {tab === "facts" ? <FactsEditor c={c} /> : null}
        {tab === "questions" ? <DeferredQuestions c={c} questions={deferred} /> : null}
      </div>

      <div className="review-footer">
        <span className="review-summary">
          {editedCount ? <strong>수정 {editedCount}건</strong> : "수정 없음"}
          {deferred.length ? <span> · 미답 질문 {deferred.length}건 (보수적 가정 적용 중)</span> : null}
        </span>
        {submit.isError ? <ErrorState error={submit.error} /> : null}
        {editedCount ? (
          <button type="button" className="btn btn-ghost" onClick={() => setEdits({})}>
            <RotateCcw size={14} aria-hidden="true" />
            모두 되돌리기
          </button>
        ) : null}
        <button type="button" className="btn btn-primary btn-lg" disabled={submit.isPending} onClick={() => submit.mutate({ decisions })}>
          {submit.isPending ? <Spinner /> : null}
          감사 요청
        </button>
      </div>
    </div>
  );
}

function TabButton({ id, tab, setTab, label, count, attention }: { id: Tab; tab: Tab; setTab: (t: Tab) => void; label: string; count: number; attention?: boolean }) {
  return (
    <button type="button" role="tab" aria-selected={tab === id} className={cx("tab", tab === id && "is-active")} onClick={() => setTab(id)}>
      {label} <span className={cx("tab-count num", attention && "is-attention")}>{count}</span>
    </button>
  );
}

function DecisionCard({
  d,
  original,
  target,
  requirements,
  applied,
  max,
  selected,
  onSelect,
  onSeek,
  onPatch,
  onReset,
}: {
  d: Decision;
  original: Decision;
  target: Target | undefined;
  requirements: Requirement[];
  /** the ranges the renderer will use for this decision as it is now, once the server has worked them out */
  applied: Range[] | undefined;
  /** end of the timeline in seconds */
  max: number;
  selected: boolean;
  onSelect: () => void;
  onSeek: () => void;
  onPatch: (p: Patch) => void;
  onReset: () => void;
}) {
  const reqs = requirements.filter((r) => d.satisfies.includes(r.id));
  const turnedOff = original.method !== "keep" && d.method === "keep";
  const onScreen = isOnScreen(d);
  const methods = onScreen ? ON_SCREEN_METHODS : IN_FILE_METHODS;
  const name = `method-${d.id}`;
  return (
    <article className={cx("dcard", selected && "is-selected", d.edited && "is-edited")}>
      <header className="dcard-head">
        <RiskTag risk={d.risk} />
        <span className="mono dcard-id">{d.id}</span>
        <h3 className="dcard-title">
          <button type="button" className="dcard-title-btn" onClick={onSelect} aria-expanded={selected}>
            {d.targetLabel}
          </button>
        </h3>
        {onScreen ? null : <span className="tag tag-file">파일 정보</span>}
        {d.edited ? <span className="tag tag-edited">수정됨</span> : null}
        {d.source === "auditor" ? <span className="tag">감사 반영</span> : null}
      </header>

      {onScreen ? null : (
        <p className="dcard-file">
          {target?.detail ? `${target.detail}. ` : ""}
          화면이 아니라 파일에 기록된 정보라서 미리보기와 타임라인에는 나타나지 않습니다. 삭제를 고르면 출력 파일에서 지웁니다.
        </p>
      )}

      <div className="dcard-controls">
        <div className="segmented" role="radiogroup" aria-label={`${d.id} 처리 방식`}>
          {methods.map((m) => (
            <label key={m} title={METHOD_HINT[m]}>
              <input type="radio" name={name} value={m} checked={d.method === m} onChange={() => onPatch({ method: m })} />
              <span className={cx("seg-swatch", `swatch-${m}`)} aria-hidden="true" />
              {METHOD_LABEL[m]}
            </label>
          ))}
        </div>
        {onScreen ? (
          <>
            <label className="inline-field">
              <span>형태</span>
              <select className="select" value={d.shape ?? "box"} onChange={(e) => onPatch({ shape: e.target.value as Shape })} disabled={d.method === "keep"}>
                {SHAPES.map((s) => (
                  <option key={s} value={s}>
                    {SHAPE_LABEL[s]}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn btn-sm btn-ghost" onClick={onSeek}>
              장면 보기
            </button>
          </>
        ) : null}
        {d.edited ? (
          <button type="button" className="btn btn-sm btn-ghost" onClick={onReset}>
            되돌리기
          </button>
        ) : null}
      </div>

      {methods.includes(d.method) ? null : (
        <p className="muted small">
          저장된 처리 방식 &lsquo;{methodLabel(d.method)}&rsquo;은(는) 이제 고를 수 없습니다. {methods.map((m) => METHOD_LABEL[m]).join(" 또는 ")} 중에서 정해 주세요.
        </p>
      )}

      {turnedOff ? (
        <p className="dcard-warning" role="alert">
          {d.riskIfChanged} 감사 단계에서 요건 미충족으로 다시 확인합니다.
        </p>
      ) : null}

      {onScreen ? <RangeEditor ranges={d.ranges} applied={d.method === "keep" ? undefined : applied} max={max} disabled={d.method === "keep"} onChange={(ranges) => onPatch({ ranges })} /> : null}

      <div className={cx("dcard-body", !selected && "is-collapsed")}>
        <section>
          <h4>근거</h4>
          {reqs.length ? (
            reqs.map((r) => (
              <p key={r.id} className="dcard-req">
                <span className="mono">{r.id}</span> {r.statement}
              </p>
            ))
          ) : (
            <p className="dcard-req">{d.rationale}</p>
          )}
          <CitationList items={d.citations} />
        </section>
        {selected ? (
          <>
            <section>
              <h4>유사 사례</h4>
              <CitationList items={d.similarCases} empty="코퍼스에서 찾은 유사 사례가 없습니다." />
            </section>
            <section>
              <h4>바꾸면 생기는 위험</h4>
              <p>{d.riskIfChanged}</p>
            </section>
          </>
        ) : null}
      </div>
    </article>
  );
}

function RangeEditor({ ranges, applied, max, disabled, onChange }: { ranges: Range[]; applied: Range[] | undefined; max: number; disabled: boolean; onChange: (r: Range[]) => void }) {
  const set = (i: number, k: 0 | 1, v: number) => {
    const next = ranges.map((r) => [...r] as Range);
    next[i][k] = Math.max(0, Math.min(max, Number.isFinite(v) ? v : 0));
    if (next[i][0] > next[i][1]) next[i] = [next[i][1], next[i][0]];
    onChange(next);
  };
  // the renderer moves a boundary next to where the target appears or disappears onto that moment, and fits every range to whole frames
  const adjusted = applied && !sameRanges(applied, ranges) ? applied : null;
  return (
    <div className="ranges">
      <span className="ranges-label">구간</span>
      {ranges.map(([s, e], i) => (
        <span key={i} className="range">
          <label>
            <span className="sr-only">구간 {i + 1} 시작 (초)</span>
            <input className="input range-input num" type="number" step={0.1} min={0} max={max} value={s} disabled={disabled} onChange={(ev) => set(i, 0, Number(ev.target.value))} />
          </label>
          <span aria-hidden="true">–</span>
          <label>
            <span className="sr-only">구간 {i + 1} 끝 (초)</span>
            <input className="input range-input num" type="number" step={0.1} min={0} max={max} value={e} disabled={disabled} onChange={(ev) => set(i, 1, Number(ev.target.value))} />
          </label>
          <span className="muted small mono num">{rangeText([s, e])}</span>
        </span>
      ))}
      {adjusted ? (
        <p className="ranges-applied" role="status">
          실제 적용 구간 <span className="mono num">{adjusted.length ? adjusted.map(rangeText).join(", ") : "없음"}</span>
          <span className="muted"> · 대상이 나타나고 사라지는 순간과 프레임에 맞춘 값입니다.</span>
        </p>
      ) : null}
    </div>
  );
}

function FactsEditor({ c }: { c: CaseDetail }) {
  const update = useUpdateFact(c.id);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const version = c.plan?.version;
  useEffect(() => setPendingKey(null), [version]);
  return (
    <div className="facts-editor" role="tabpanel" aria-label="사실">
      <p className="muted small">사실을 고치면 그 사실에 기대는 분석만 다시 평가하고, 결과가 바뀌면 결정에 반영합니다.</p>
      <ul className="facts editable">
        {c.facts.map((f) => (
          <li key={f.key} className="fact">
            <span className="fact-label">{f.label}</span>
            {f.editable && f.options ? (
              <select
                className="select"
                value={f.value}
                aria-label={f.label}
                disabled={update.isPending || pendingKey !== null}
                onChange={(e) => {
                  setPendingKey(f.key);
                  update.mutate({ key: f.key, value: e.target.value });
                }}
              >
                {!f.options.some((o) => o.value === f.value) ? <option value={f.value}>{f.valueLabel}</option> : null}
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <span className="fact-value">{f.valueLabel}</span>
            )}
            <span className="fact-meta">
              <ProvenanceTag provenance={f.provenance} confidence={f.confidence} source={f.source} />
              {pendingKey === f.key ? (
                <span className="muted small">
                  <Spinner size={12} /> 다시 평가하는 중
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      {update.isError ? <ErrorState error={update.error} /> : null}
    </div>
  );
}

function DeferredQuestions({ c, questions }: { c: CaseDetail; questions: Question[] }) {
  const answer = useAnswerQuestions(c.id);
  const [values, setValues] = useState<Record<string, string>>({});
  if (questions.length === 0) {
    return (
      <div role="tabpanel" aria-label="미답 질문" className="muted small panel-pad">
        남은 질문이 없습니다.
      </div>
    );
  }
  return (
    <div className="deferred" role="tabpanel" aria-label="미답 질문">
      <p className="muted small">답하지 않은 질문은 안전한 쪽으로 가정해 두었습니다. 지금 답하면 관련 판단만 다시 평가합니다.</p>
      {questions.map((q) => (
        <form
          key={q.id}
          className="deferred-q"
          onSubmit={(e) => {
            e.preventDefault();
            if (values[q.id]) answer.mutate({ answers: [{ questionId: q.id, value: values[q.id] }] });
          }}
        >
          <p className="deferred-prompt">
            <span className="mono">{q.id}</span> {q.prompt}
          </p>
          <p className="muted small">{q.basis}</p>
          <div className="choice-list compact">
            {q.options.map((o) => (
              <label key={o.value} className={cx("choice", values[q.id] === o.value && "is-checked")}>
                <input type="radio" name={`deferred-${q.id}`} value={o.value} checked={values[q.id] === o.value} onChange={() => setValues((v) => ({ ...v, [q.id]: o.value }))} />
                <span className="choice-title">{o.label}</span>
              </label>
            ))}
          </div>
          <button type="submit" className="btn btn-sm btn-primary" disabled={!values[q.id] || answer.isPending}>
            답하고 다시 평가
          </button>
        </form>
      ))}
      {answer.isError ? <ErrorState error={answer.error} /> : null}
    </div>
  );
}
