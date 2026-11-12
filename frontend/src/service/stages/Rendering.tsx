import { Check } from "lucide-react";
import type { CaseDetail } from "../../api/contract";
import { Spinner, cx } from "../../ui";
import { ActivityFeed } from "../parts";

export function RenderingStage({ c }: { c: CaseDetail }) {
  const removed = (c.plan?.decisions ?? []).filter((d) => d.scope === "metadata" && d.method === "remove");
  return (
    <div className="stage-grid">
      <section className="stage-main" aria-labelledby="render-title">
        <div className="working">
          <Spinner size={18} />
          <h2 id="render-title">원본에 처리 결과를 입혀 출력 영상을 만들고 있습니다</h2>
        </div>
        <p className="stage-lead">
          분석용 사본이 아니라 원본 스트림을 다시 읽어, 처리할 프레임만 고칩니다. 오디오·자막·메타데이터는 바이트 그대로 복사합니다.
          {removed.length ? ` 삭제하기로 한 정보(${removed.map((d) => d.targetLabel).join(", ")})만 출력 파일에서 뺍니다.` : ""}
        </p>
        <ol className="render-steps">
          {c.renderSteps.map((step) => (
            <li key={step.label} className={cx("render-step", `is-${step.state}`)}>
              <span className="render-mark" aria-hidden="true">
                {step.state === "done" ? <Check size={13} strokeWidth={3} /> : step.state === "running" ? <Spinner size={13} /> : null}
              </span>
              <span>{step.label}</span>
              {step.detail ? <span className="muted small">{step.detail}</span> : null}
              <span className="sr-only">{step.state === "done" ? "완료" : step.state === "running" ? "진행 중" : "대기"}</span>
            </li>
          ))}
        </ol>
      </section>
      <aside className="stage-side">
        <section className="side-block">
          <h2 className="side-title">완료 전에 확인하는 것</h2>
          <ul className="check-list">
            <li>프레임 수와 프레임별 타임스탬프가 원본과 같은지</li>
            <li>오디오가 바이트 단위로 같은지 (framemd5)</li>
            <li>회전·색 정보·챕터 등 메타데이터가 같은지</li>
            <li>처리하지 않은 영역의 화질 손실 (PSNR·SSIM)</li>
            <li>처리한 영역에서 얼굴·번호판·글자가 다시 검출되지 않는지</li>
            {removed.length ? <li>삭제하기로 한 정보({removed.map((d) => d.targetLabel).join(", ")})가 출력 파일에 남지 않았는지</li> : null}
          </ul>
          <p className="muted small">하나라도 실패하면 작업을 완료로 표시하지 않습니다.</p>
        </section>
        <section className="side-block">
          <h2 className="side-title">진행 기록</h2>
          <ActivityFeed items={c.activity} limit={4} />
        </section>
      </aside>
    </div>
  );
}
