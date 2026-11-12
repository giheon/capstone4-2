import { AlertTriangle, CheckCircle2, CircleDot, Clock3 } from "lucide-react";
import type { Activity, CaseDetail, Fact } from "../api/contract";
import { clock } from "../lib/format";
import { ProvenanceTag, cx } from "../ui";

const TONE_ICON = {
  info: CircleDot,
  done: CheckCircle2,
  wait: Clock3,
  warn: AlertTriangle,
} as const;

export function ActivityFeed({ items, limit = 12 }: { items: Activity[]; limit?: number }) {
  const shown = [...items].reverse().slice(0, limit);
  if (shown.length === 0) return <p className="muted">작업을 시작하는 중입니다.</p>;
  return (
    <ol className="feed" aria-label="진행 기록" aria-live="polite">
      {shown.map((item, i) => {
        const Icon = TONE_ICON[item.tone];
        return (
          <li key={item.seq} className={cx("feed-item", `tone-${item.tone}`, i === 0 && "is-latest")}>
            <Icon size={15} aria-hidden="true" className="feed-icon" />
            <span className="feed-text">{item.text}</span>
            <span className="feed-time mono num">{clock(item.t)}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function FoundSummary({ c }: { c: CaseDetail }) {
  const p = c.perception;
  // found in the file itself, not in the picture (the shooting-location tag)
  const inFile = c.targets.filter((target) => target.kind === "metadata");
  return (
    <section className="side-block" aria-labelledby="found-title">
      <h2 id="found-title" className="side-title">
        지금까지 찾은 것
      </h2>
      {p ? (
        <dl className="found-grid">
          <div>
            <dt>사람</dt>
            <dd className="num">{p.people}</dd>
          </div>
          <div>
            <dt>번호판</dt>
            <dd className="num">{p.plates}</dd>
          </div>
          <div>
            <dt>글자</dt>
            <dd className="num">{p.texts}</dd>
          </div>
          <div>
            <dt>장면</dt>
            <dd className="num">{p.scenes}</dd>
          </div>
        </dl>
      ) : (
        <p className="muted small">영상을 분석하면 여기에 정리됩니다.</p>
      )}
      {inFile.length ? (
        <p className="found-file">
          <span className="tag tag-file">파일 정보</span> 화면이 아니라 파일에 기록된 것: {inFile.map((target) => target.label).join(", ")}
        </p>
      ) : null}
    </section>
  );
}

export function FactsList({ facts, title = "알고 있는 사실" }: { facts: Fact[]; title?: string }) {
  if (facts.length === 0) return null;
  return (
    <section className="side-block" aria-label={title}>
      <h2 className="side-title">{title}</h2>
      <ul className="facts">
        {facts.map((f) => (
          <li key={f.key} className="fact">
            <span className="fact-label">{f.label}</span>
            <span className="fact-value">{f.valueLabel}</span>
            <ProvenanceTag provenance={f.provenance} confidence={f.confidence} source={f.source} />
          </li>
        ))}
      </ul>
    </section>
  );
}
