import { useState } from "react";
import type { CaseDetail, Question } from "../../api/contract";
import { useAnswerQuestions } from "../../api/hooks";
import { timecode } from "../../lib/format";
import { CitationList, ErrorState, Spinner, cx } from "../../ui";
import { CaseStill, shortTargetLabel } from "../media";
import { FactsList } from "../parts";

const TARGET_SHORT: Record<string, string> = {
  "person-2": "인터뷰 대상",
  "person-3": "직원",
  "text-1": "명찰",
  "plate-1": "번호판",
};

export function QuestionsStage({ c }: { c: CaseDetail }) {
  const pending = c.questions.filter((q) => q.status === "pending");
  const removed = c.questions.filter((q) => q.status === "removed");
  const [values, setValues] = useState<Record<string, string>>({});
  const submit = useAnswerQuestions(c.id);
  const answered = pending.filter((q) => values[q.id]).length;
  const round = pending[0]?.round ?? 1;

  const send = () =>
    submit.mutate({
      answers: pending.map((q) => ({ questionId: q.id, value: values[q.id] ?? "later" })),
    });

  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="questions-title">
        <h2 id="questions-title" className="stage-title">
          판단에 필요한 사실 {pending.length}가지를 여쭙니다
        </h2>
        <p className="stage-lead">
          영상과 메모만으로는 알 수 없는데 결과를 바꾸는 사실만 골랐습니다. 질문마다 묻는 이유와 답에 따라 달라지는 처리를 함께 적었습니다.
          모르면 &lsquo;모름&rsquo;을 고르세요. 안전한 쪽으로 가정해 진행합니다.
        </p>
        <p className="round-meta num">
          {c.media ? `라운드 ${round} · 질문 ${pending.length}개` : `라운드 ${round} / 2 · 질문 ${pending.length}개 (한 번에 최대 6개)`}
        </p>
        <div className="question-list">
          {pending.map((q, i) => (
            <QuestionCard key={q.id} c={c} q={q} index={i} value={values[q.id]} onChange={(v) => setValues((prev) => ({ ...prev, [q.id]: v }))} />
          ))}
        </div>
        {submit.isError ? <ErrorState error={submit.error} /> : null}
        <div className="form-actions sticky-actions">
          <button type="button" className="btn btn-primary btn-lg" onClick={send} disabled={submit.isPending}>
            {submit.isPending ? <Spinner /> : null}
            {answered === pending.length ? "답변 보내기" : `답변 보내기 · ${pending.length - answered}개는 나중에`}
          </button>
          <span className="muted small">답하지 않은 질문은 검토 화면에서 다시 답할 수 있습니다.</span>
        </div>
      </section>
      <aside className="stage-side">
        <FactsList facts={c.facts} title="이미 알고 있어서 묻지 않는 사실" />
        {removed.length ? (
          <section className="side-block">
            <h2 className="side-title">질문 게이트가 뺀 질문</h2>
            <ul className="removed-list">
              {removed.map((q) => (
                <li key={q.id}>
                  <span className="removed-prompt">{q.prompt}</span>
                  <span className="muted small">{q.removedReason}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

function QuestionCard({ c, q, index, value, onChange }: { c: CaseDetail; q: Question; index: number; value?: string; onChange: (v: string) => void }) {
  const name = `q-${q.id}`;
  // information in the file (the location tag) cannot be pointed at on the frame: it is named instead
  const inFile = c.targets.filter((target) => target.kind === "metadata" && q.targets.includes(target.id));
  const extra = [
    { value: "unknown", label: "모름", detail: "안전한 쪽으로 가정합니다" },
    { value: "later", label: "나중에 답하기", detail: "검토 화면에서 답할 수 있습니다" },
  ];
  return (
    <article className="qcard" aria-labelledby={`${name}-title`}>
      <header className="qcard-head">
        <span className="qcard-id mono">{q.id}</span>
        <span className="qcard-key mono">{q.factKey}</span>
      </header>
      <h3 id={`${name}-title`} className="qcard-title">
        {q.prompt}
      </h3>
      {q.note ? <p className="qcard-note">{q.note}</p> : null}
      <div className="qcard-body">
        <fieldset className="choice-list">
          <legend className="sr-only">{q.prompt}</legend>
          {q.options.map((o) => (
            <label key={o.value} className={cx("choice", value === o.value && "is-checked")}>
              <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
              <span className="choice-text">
                <span className="choice-title">{o.label}</span>
                {o.detail ? <span className="choice-detail">{o.detail}</span> : null}
              </span>
            </label>
          ))}
          <div className="choice-extra">
            {extra.map((o) => (
              <label key={o.value} className={cx("choice choice-quiet", value === o.value && "is-checked")}>
                <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
                <span className="choice-text">
                  <span className="choice-title">{o.label}</span>
                  <span className="choice-detail">{o.detail}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {q.frameAt !== undefined ? (
          <figure className="qcard-frame">
            <div className="frame-thumb">
              <CaseStill
                c={c}
                t={q.frameAt}
                focus={q.targets
                  .filter((id) => !inFile.some((target) => target.id === id))
                  .map((id) => ({
                    targetId: id,
                    label: c.media ? shortTargetLabel(c.targets.find((target) => target.id === id), id) : (TARGET_SHORT[id] ?? id),
                  }))}
              />
            </div>
            <figcaption className="muted small mono num">관련 장면 {timecode(q.frameAt)}</figcaption>
          </figure>
        ) : null}
      </div>
      {inFile.length ? (
        <p className="qcard-file">
          <span className="tag tag-file">파일 정보</span> 이 답은 화면에 보이지 않는 {inFile.map((target) => target.label).join(", ")}의 처리에도 쓰입니다.
        </p>
      ) : null}
      <details className="qcard-why" open={index === 0}>
        <summary>왜 묻나요 · 답에 따라 달라지는 처리</summary>
        <div className="qcard-why-body">
          <p>{q.basis}</p>
          <CitationList items={q.citations} />
          {q.impact.length ? (
            <table className="impact">
              <caption className="sr-only">답변별로 달라지는 처리</caption>
              <thead>
                <tr>
                  <th scope="col">답</th>
                  <th scope="col">달라지는 처리</th>
                </tr>
              </thead>
              <tbody>
                {q.impact.map((row) => (
                  <tr key={row.option}>
                    <th scope="row">{q.options.find((o) => o.value === row.option)?.label ?? row.option}</th>
                    <td>{row.effect}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      </details>
    </article>
  );
}
