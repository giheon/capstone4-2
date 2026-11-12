import type { CaseDetail } from "../../api/contract";
import { STATUS_LABEL } from "../../lib/format";
import { Spinner } from "../../ui";
import { ActivityFeed, FactsList, FoundSummary } from "../parts";

const HEADLINE: Partial<Record<CaseDetail["status"], string>> = {
  QUEUED: "작업을 대기열에 올렸습니다",
  ANALYZING: "영상에서 사람·글자·번호판과 장면을 찾고 있습니다",
  ANALYZING_LEGAL: "법령과 판례를 찾아 무엇을 가려야 하는지 판단하고 있습니다",
  PLANNING: "보호 요건을 구체적인 처리 계획으로 바꾸고 있습니다",
  AUDITING: "다른 모델이 수정된 계획을 다시 검증하고 있습니다",
};

export function AnalyzingStage({ c }: { c: CaseDetail }) {
  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="working-title">
        <div className="working">
          <Spinner size={18} />
          <h2 id="working-title">{(c.media && c.status === "AUDITING" ? "수정된 계획이 보호 요건을 모두 충족하는지 다시 검증하고 있습니다" : HEADLINE[c.status]) ?? STATUS_LABEL[c.status]}</h2>
        </div>
        <p className="stage-lead">
          답이 필요한 질문이 생기면 이 화면이 바로 질문으로 바뀝니다. 창을 닫아도 작업은 계속되고, 최근 작업에서 다시 열 수 있습니다.
        </p>
        <ActivityFeed items={c.activity} />
      </section>
      <aside className="stage-side">
        <FoundSummary c={c} />
        <FactsList facts={c.facts} />
      </aside>
    </div>
  );
}
