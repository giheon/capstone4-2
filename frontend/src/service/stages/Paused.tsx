import { type FormEvent, useState } from "react";
import { CirclePause, Play } from "lucide-react";
import type { CaseDetail, CaseStatus, PauseReason } from "../../api/contract";
import { useResumeCase } from "../../api/hooks";
import { STATUS_LABEL, usd } from "../../lib/format";
import { ErrorState, Spinner } from "../../ui";
import { ActivityFeed, FactsList, FoundSummary } from "../parts";

const MAX_CAP = 20;

const TITLE: Record<PauseReason, string> = {
  budget: "비용 상한에 닿아 멈췄습니다",
  user: "요청에 따라 멈췄습니다",
  restart: "서버가 다시 시작되어 멈췄습니다",
};

/** a cap a little above what is already spent, on a 5-cent step */
function suggestedCap(c: CaseDetail): number {
  return Math.min(MAX_CAP, Math.ceil((Math.max(c.costUsd, c.costCapUsd) + 0.1) * 20 - 1e-9) / 20);
}

/** `was`: the status the run had when it stopped, when the event stream knows it */
export function PausedStage({ c, was }: { c: CaseDetail; was: CaseStatus | null }) {
  const resume = useResumeCase(c.id);
  const reason: PauseReason = c.pauseReason ?? "user";
  const budget = reason === "budget";
  const [cap, setCap] = useState(() => suggestedCap(c).toFixed(2));
  const value = Number(cap);
  const capOk = !budget || (Number.isFinite(value) && value > c.costUsd && value <= MAX_CAP);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!capOk || resume.isPending) return;
    resume.mutate(budget ? value : undefined);
  };

  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="paused-title">
        <div className="pause-banner">
          <CirclePause size={20} aria-hidden="true" className="pause-icon" />
          <div className="pause-text">
            <h2 id="paused-title" className="pause-title">
              {TITLE[reason]}
            </h2>
            <p>
              {budget ? (
                <>
                  지금까지 <strong className="num">{usd(c.costUsd, 3)}</strong>를 썼고 상한은 <span className="num">{usd(c.costCapUsd)}</span>입니다. 상한을 쓴 금액보다
                  높게 올리면 멈춘 지점부터 이어서 진행합니다.
                </>
              ) : reason === "restart" ? (
                "마지막으로 저장된 지점까지의 결과는 그대로 있습니다. 계속을 누르면 그 지점부터 이어서 진행합니다."
              ) : (
                "지금까지의 결과는 그대로 있습니다. 계속을 누르면 멈춘 지점부터 이어서 진행합니다."
              )}
            </p>
            {was ? <p className="pause-was">멈춘 단계 · {STATUS_LABEL[was]}</p> : null}
          </div>
          <form className="pause-actions" onSubmit={submit} noValidate>
            {budget ? (
              <label className="pause-cap">
                <span className="field-label">새 비용 상한 (USD)</span>
                <input
                  className="input num"
                  type="number"
                  inputMode="decimal"
                  step={0.05}
                  min={0.05}
                  max={MAX_CAP}
                  value={cap}
                  onChange={(e) => setCap(e.target.value)}
                  aria-invalid={!capOk}
                  aria-describedby="pause-cap-hint"
                />
              </label>
            ) : null}
            <button type="submit" className="btn btn-primary" disabled={!capOk || resume.isPending}>
              {resume.isPending ? <Spinner /> : <Play size={15} aria-hidden="true" />}
              {budget ? "상한 올리고 계속" : "계속"}
            </button>
            {budget ? (
              <span id="pause-cap-hint" className={capOk ? "pause-hint" : "pause-hint is-error"}>
                {capOk ? `쓴 금액 ${usd(c.costUsd, 3)}보다 높게, 최대 ${usd(MAX_CAP, 0)}` : `${usd(c.costUsd, 3)}보다 크고 ${usd(MAX_CAP, 0)} 이하인 금액을 넣어 주세요.`}
              </span>
            ) : null}
          </form>
          {resume.isError ? <ErrorState error={resume.error} /> : null}
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
