import { useState } from "react";
import type { CaseDetail } from "../../api/contract";
import { useAnswerIdentity } from "../../api/hooks";
import { timecode } from "../../lib/format";
import { ErrorState, Spinner, cx } from "../../ui";
import { CaseStill } from "../media";
import { ActivityFeed } from "../parts";

export function IdentityStage({ c }: { c: CaseDetail }) {
  const q = c.identity;
  const [choice, setChoice] = useState("");
  const answer = useAnswerIdentity(c.id);
  if (!q) return null;
  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="identity-title">
        <h2 id="identity-title" className="stage-title">
          {q.prompt}
        </h2>
        <p className="stage-lead">{q.detail}</p>
        <div className="compare">
          {q.candidates.map((cand) => (
            <figure key={cand.label} className="frame-card">
              <div className="frame-thumb">
                <CaseStill c={c} t={cand.at} focus={[{ targetId: cand.target, label: c.media ? cand.label : cand.label.split(" ")[0] }]} />
              </div>
              <figcaption>
                <span className="frame-caption-title">{cand.label}</span>
                <span className="muted mono num">
                  {timecode(cand.at)} · {cand.note}
                </span>
              </figcaption>
            </figure>
          ))}
        </div>
        <form
          className="answer-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (choice) answer.mutate({ questionId: q.id, value: choice });
          }}
        >
          <fieldset className="choice-list">
            <legend className="sr-only">같은 사람인가요</legend>
            {q.options.map((o) => (
              <label key={o.value} className={cx("choice", choice === o.value && "is-checked")}>
                <input type="radio" name="identity" value={o.value} checked={choice === o.value} onChange={() => setChoice(o.value)} />
                <span className="choice-text">
                  <span className="choice-title">{o.label}</span>
                  {o.detail ? <span className="choice-detail">{o.detail}</span> : null}
                </span>
              </label>
            ))}
          </fieldset>
          {answer.isError ? <ErrorState error={answer.error} /> : null}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={!choice || answer.isPending}>
              {answer.isPending ? <Spinner /> : null}
              확인하고 계속
            </button>
            <span className="muted small">본인·제3자·동의 같은 법적 판단은 여기서 묻지 않습니다.</span>
          </div>
        </form>
      </section>
      <aside className="stage-side">
        <section className="side-block">
          <h2 className="side-title">찾은 대상</h2>
          <ul className="target-list">
            {c.targets.map((t) => (
              <li key={t.id} className={cx("target-row", !t.personal && "is-dim")}>
                <span className="target-name">
                  {t.label}
                  {t.kind === "metadata" ? <span className="tag tag-file">파일 정보</span> : null}
                </span>
                <span className="target-detail">{t.detail}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="side-block">
          <h2 className="side-title">진행 기록</h2>
          <ActivityFeed items={c.activity} limit={5} />
        </section>
      </aside>
    </div>
  );
}
