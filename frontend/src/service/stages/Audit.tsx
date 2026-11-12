import { useState } from "react";
import type { CaseDetail, Finding } from "../../api/contract";
import { useSubmitAudit } from "../../api/hooks";
import { rangeText, timecode } from "../../lib/format";
import { CitationList, ErrorState, SeverityTag, Spinner, cx } from "../../ui";
import { CaseStill } from "../media";

const TYPE_LABEL: Record<Finding["type"], string> = {
  residual_identifier: "남은 식별 단서",
  requirement_unmet: "요건 미충족",
  over_redaction: "과잉 처리",
  inconsistency: "불일치",
};

/** the finding is about this requirement: its title names the id as a whole (REQ-1 is not part of REQ-10) */
function namesRequirement(f: Finding, id: string): boolean {
  const whole = new RegExp(`(^|[^\\w-])${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`);
  return whole.test(f.title) || whole.test(f.detail);
}

interface Resolution {
  action?: "apply" | "reject";
  reason: string;
}

export function AuditStage({ c }: { c: CaseDetail }) {
  const findings = c.findings;
  const [res, setRes] = useState<Record<string, Resolution>>({});
  const submit = useSubmitAudit(c.id);
  const set = (id: string, value: Partial<Resolution>) =>
    setRes((current) => ({ ...current, [id]: { ...(current[id] ?? { reason: "" }), ...value } }));
  const unresolved = findings.filter((f) => {
    const r = res[f.id];
    return !r?.action || (r.action === "reject" && r.reason.trim().length < 4);
  });
  const counts = { high: 0, medium: 0, low: 0 };
  for (const f of findings) counts[f.severity] += 1;

  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="audit-title">
        <h2 id="audit-title" className="stage-title">
          감사에서 확인할 사항 {findings.length}건
        </h2>
        <p className="stage-lead">
          {c.media
            ? "검토에서 바뀐 계획이 보호 요건을 여전히 모두 충족하는지 다시 대조했습니다."
            : "분석과 계획을 만든 모델과 다른 모델군(Gemini 3.1 Pro)이 수정된 계획을 요건과 관찰에 대조해 다시 검증했습니다."}{" "}
          권고를 거부하면 사유와 함께 &lsquo;위험 수용&rsquo;으로 보고서에 남습니다.
        </p>
        <p className="round-meta num">
          심각 {counts.high} · 보통 {counts.medium} · 낮음 {counts.low}
        </p>
        <div className="finding-list">
          {findings.map((f) => (
            <FindingCard key={f.id} c={c} f={f} value={res[f.id]} onChange={(v) => set(f.id, v)} />
          ))}
        </div>
        {submit.isError ? <ErrorState error={submit.error} /> : null}
        <div className="form-actions sticky-actions">
          <button
            type="button"
            className="btn btn-primary btn-lg"
            disabled={unresolved.length > 0 || submit.isPending}
            onClick={() =>
              submit.mutate({
                resolutions: findings.map((f) => ({ findingId: f.id, action: res[f.id].action as "apply" | "reject", reason: res[f.id].reason || undefined })),
              })
            }
          >
            {submit.isPending ? <Spinner /> : null}
            결정 반영하고 출력 영상 만들기
          </button>
          {c.canReturnToReview !== false ? (
            <button type="button" className="btn btn-secondary" disabled={submit.isPending} onClick={() => submit.mutate({ resolutions: [], backToReview: true })}>
              계획으로 돌아가 직접 고치기
            </button>
          ) : null}
          {unresolved.length ? <span className="muted small">{unresolved.length}건을 더 정해 주세요. 거부할 때는 사유가 필요합니다.</span> : null}
        </div>
      </section>
      <aside className="stage-side">
        <section className="side-block">
          <h2 className="side-title">요건 충족 점검</h2>
          <ul className="req-check">
            {c.requirements.map((r) => {
              const hit = c.media
                ? findings.find((f) => f.type === "requirement_unmet" && namesRequirement(f, r.id))
                : findings.find((f) => f.ranges.length && (f.id === `F-${r.id}` || (f.id === "F-2" && r.id === "REQ-3") || (f.id === "F-1" && r.id === "REQ-1")));
              return (
                <li key={r.id} className={cx("req-row", hit ? `is-${hit.severity}` : "is-ok")}>
                  <span className="mono">{r.id}</span>
                  <span className="req-statement">{r.statement}</span>
                  <span className="req-state">{hit ? `${hit.id} 확인 필요` : "충족"}</span>
                </li>
              );
            })}
          </ul>
        </section>
      </aside>
    </div>
  );
}

function FindingCard({ c, f, value, onChange }: { c: CaseDetail; f: Finding; value?: Resolution; onChange: (v: Partial<Resolution>) => void }) {
  const at = f.ranges[0] ? f.ranges[0][0] + Math.min(1.2, (f.ranges[0][1] - f.ranges[0][0]) / 2) : 0;
  // a finding about information in the file (the location tag) has no moment and no place in the picture
  const inFile = c.targets.find((target) => target.id === f.targetId)?.kind === "metadata";
  const name = `finding-${f.id}`;
  return (
    <article className={cx("fcard", `sev-${f.severity}`)} aria-labelledby={`${name}-title`}>
      <header className="fcard-head">
        <SeverityTag severity={f.severity} />
        <span className="mono">{f.id}</span>
        <span className="fcard-type">{TYPE_LABEL[f.type]}</span>
        {inFile ? <span className="tag tag-file">파일 정보</span> : <span className="muted small mono num">{f.ranges.map(rangeText).join(", ")}</span>}
      </header>
      <div className={cx("fcard-body", inFile && "is-file")}>
        <div className="fcard-text">
          <h3 id={`${name}-title`} className="fcard-title">
            {f.title}
          </h3>
          <p>{f.detail}</p>
          <CitationList items={f.citations} />
          {inFile ? <p className="muted small">화면이 아니라 파일에 기록된 정보라서 보여 줄 장면이 없습니다. 출력 파일 전체에 해당합니다.</p> : null}
          <p className="fcard-rec">
            <strong>권고</strong> {f.recommendation}
          </p>
        </div>
        {inFile ? null : (
          <figure className="fcard-frame">
            <div className="frame-thumb">
              <CaseStill c={c} t={at} focus={[{ targetId: f.targetId, label: f.id }]} />
            </div>
            <figcaption className="muted small mono num">{timecode(at)}</figcaption>
          </figure>
        )}
      </div>
      <fieldset className="fcard-actions">
        <legend className="sr-only">{f.id} 처리</legend>
        <label className={cx("choice choice-inline", value?.action === "apply" && "is-checked")}>
          <input type="radio" name={name} checked={value?.action === "apply"} onChange={() => onChange({ action: "apply" })} />
          <span className="choice-title">권고 적용</span>
        </label>
        <label className={cx("choice choice-inline", value?.action === "reject" && "is-checked")}>
          <input type="radio" name={name} checked={value?.action === "reject"} onChange={() => onChange({ action: "reject" })} />
          <span className="choice-title">거부하고 위험 수용</span>
        </label>
      </fieldset>
      {value?.action === "reject" ? (
        <label className="field reject-reason">
          <span className="field-label">거부 사유 (보고서에 기록)</span>
          <textarea className="textarea" rows={2} value={value.reason} onChange={(e) => onChange({ reason: e.target.value })} placeholder="예: 손짓이 인터뷰 맥락상 중요해 형태를 바꾸지 않음" />
        </label>
      ) : null}
    </article>
  );
}
