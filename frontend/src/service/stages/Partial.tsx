// A run that was asked to cover only part of the pipeline (a Lab section experiment) and ended there: its status
// is COMPLETED, but there is no output and no report, so there is no result to show. This says so and points
// to the Lab, where such runs are compared.
import { Link } from "react-router";
import { Activity, FlaskConical } from "lucide-react";
import type { CaseDetail } from "../../api/contract";
import { ActivityFeed, FactsList, FoundSummary } from "../parts";

/** the graph node a run ended after → the step of this screen it belongs to */
const ENDED_AFTER: Record<string, string> = {
  perceive: "영상 분석",
  legal_analyst: "법적 판단",
  review_guide: "처리 계획 수립",
  auditor: "계획 검토와 감사",
};

export function PartialStage({ c }: { c: CaseDetail }) {
  const until = c.stoppedAfter ? (ENDED_AFTER[c.stoppedAfter] ?? c.stoppedAfter) : null;
  const made = [
    c.targets.length ? `대상 ${c.targets.length}개` : "",
    c.requirements.length ? `보호 요건 ${c.requirements.length}건` : "",
    c.plan ? `결정 ${c.plan.decisions.length}건` : "",
  ].filter(Boolean);
  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="partial-title">
        <h2 id="partial-title" className="stage-title">
          요청한 구간까지만 실행한 실험용 실행입니다
        </h2>
        <p className="stage-lead">
          {until ? `${until}까지 실행하고 마쳤습니다.` : "파이프라인의 일부만 실행하고 마쳤습니다."} 출력 영상과 보고서는 만들지 않아서 결과 화면이 없습니다.
          {made.length ? ` 여기까지 만든 것은 ${made.join(", ")}입니다.` : ""}{" "}
          {c.experimentId ? "이 실행의 결과는 구간 실험에 들어갔고, 거기서 다른 조합 · 다른 영상의 실행과 비교됩니다." : "단계별 기록과 다른 조합과의 비교는 실험실에서 볼 수 있습니다."}
        </p>
        <div className="form-actions">
          {c.experimentId ? (
            <Link to={`/lab/experiments/${c.experimentId}`} className="btn btn-primary">
              <FlaskConical size={15} aria-hidden="true" />
              구간 실험으로 돌아가기
            </Link>
          ) : null}
          <Link to={`/lab/runs/${c.id}`} className={c.experimentId ? "btn btn-secondary" : "btn btn-primary"}>
            <Activity size={15} aria-hidden="true" />
            실험실에서 이 실행 보기
          </Link>
        </div>
        <h3 className="side-title">진행 기록</h3>
        <ActivityFeed items={c.activity} />
      </section>
      <aside className="stage-side">
        <FoundSummary c={c} />
        <FactsList facts={c.facts} />
      </aside>
    </div>
  );
}
