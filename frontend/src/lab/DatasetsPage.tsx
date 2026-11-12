import { Link } from "react-router";
import type { EvaluatorInfo } from "../api/contract";
import { useBackendKind, useDatasets } from "../api/hooks";
import { experimentPath, useEvaluators, useSections } from "../api/stage";
import { ErrorState, Skeleton, cx } from "../ui";
import { directionText, sectionLabel } from "./stageParts";
import { VideoSets } from "./VideoSets";

const TYPE_LABEL: Record<EvaluatorInfo["type"], string> = {
  deterministic: "결정적",
  trajectory: "궤적",
  llm_panel: "LLM 패널",
  human: "사람",
};

const STATUS_LABEL = { ready: "사용 가능", draft: "작성 중", planned: "계획" } as const;

export function DatasetsPage() {
  const q = useDatasets();
  const real = useBackendKind() === "real";
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <h1 className="lab-title">데이터셋 · 평가기</h1>
          <p className="lab-lead">
            구간 실험이 돌리는 영상 데이터셋, 실행이 끝나면 점수를 내는 등록된 평가기, 그리고 계획 중인 평가 데이터셋 · 평가기입니다. 팀이 쓴 시나리오는 법적 근거가 아니라 평가
            데이터로만 쓰고, 법률 코퍼스와 저장 위치부터 분리합니다.
          </p>
        </div>
      </header>
      <VideoSets />
      <RegisteredEvaluators />
      {q.isLoading ? <Skeleton lines={6} /> : null}
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {q.data ? (
        <>
          <section className="lab-section" aria-labelledby="ds-title">
            <h2 id="ds-title" className="lab-section-title">
              평가 데이터셋 {q.data.datasets.length} <span className="tag">계획 · 실행과 연결 전</span>
            </h2>
            <div className="table-wrap">
              <table className="table lab-table">
                <thead>
                  <tr>
                    <th scope="col">이름</th>
                    <th scope="col">용도</th>
                    <th scope="col" className="num">
                      항목
                    </th>
                    <th scope="col">버전</th>
                    <th scope="col">담당</th>
                    <th scope="col">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.datasets.map((d) => (
                    <tr key={d.id}>
                      <th scope="row">
                        <span className="strategy-name">{d.name}</span>
                        <span className="mono muted small">{d.id}</span>
                      </th>
                      <td>
                        {d.purpose}
                        <span className="cell-note">필드: {d.fields.join(" · ")}</span>
                      </td>
                      <td className="num">{d.items}</td>
                      <td className="mono">{d.version}</td>
                      <td>{d.owner}</td>
                      <td>
                        <span className={cx("ds-status", `ds-${d.status}`)}>{STATUS_LABEL[d.status]}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="lab-section" aria-labelledby="ev-title">
            <h2 id="ev-title" className="lab-section-title">
              계획된 평가기 {q.data.evaluators.length} <span className="tag">계획 · 아직 등록 안 됨</span>
            </h2>
            <p className="lab-note">
              팀이 설계 중인 평가 체계의 초안입니다. 아래 평가기는 아직 실행에 연결되어 있지 않으며, 확정되면 위 &lsquo;등록된 평가기&rsquo;에 올라가 구간 실험의 실행에 점수를 냅니다.
            </p>
            <div className="table-wrap">
              <table className="table lab-table">
                <thead>
                  <tr>
                    <th scope="col">평가기</th>
                    <th scope="col">종류</th>
                    <th scope="col">대상</th>
                    <th scope="col">지표</th>
                    <th scope="col">기준</th>
                    <th scope="col">최근 값 (서비스 프로필)</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.evaluators.map((e) => (
                    <tr key={e.id}>
                      <th scope="row">
                        <span className="strategy-name">{e.name}</span>
                        <span className="cell-note">{e.description}</span>
                      </th>
                      <td>
                        <span className={cx("ev-type", `ev-${e.type}`)}>{TYPE_LABEL[e.type]}</span>
                      </td>
                      <td>{e.target}</td>
                      <td>{e.metric}</td>
                      <td className="mono">{e.threshold ?? "—"}</td>
                      <td className="mono">{e.lastValue ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {real ? (
              <p className="lab-note">
                최근 값은 서비스 기본 프로필을 정답 시나리오로 돌려 계산합니다. 정답으로 채점하는 평가 실험은 다음 마일스톤(M5)에서 연결되어, 지금은 잰 값이 없습니다. LLM 패널은 채점
                대상을 만든 모델군을 빼고 세 모델의 다수결로 매깁니다.
              </p>
            ) : (
            <p className="lab-note">
              최근 값은 서비스 기본 프로필을 현재 설정으로 돌린 마지막 실험에서 계산했습니다
              {q.data.evaluatorSource ? (
                <>
                  {" "}
                  (<Link to={experimentPath(q.data.evaluatorSource.split(" · ")[0])} className="mono">
                    {q.data.evaluatorSource}
                  </Link>)
                </>
              ) : null}
              . 모의 실행은 S01 한 시나리오만 돌립니다. LLM 패널은 채점 대상을 만든 모델군을 빼고 세 모델의 다수결로 매기며 M5에 붙습니다.
            </p>
            )}
          </section>
        </>
      ) : null}
    </main>
  );
}

/** what actually scores runs today: the evaluators registered on the server (`GET /api/lab/evaluators`) */
function RegisteredEvaluators() {
  const q = useEvaluators();
  const sections = useSections();
  const label = (id: string) => sectionLabel(sections.data, id);
  return (
    <section className="lab-section" aria-labelledby="reg-ev-title">
      <h2 id="reg-ev-title" className="lab-section-title">
        등록된 평가기 {q.data?.length ?? "…"}
      </h2>
      <p className="lab-note">
        구간 실험의 실행이 끝나면, 그 실행의 구간을 보는 평가기가 돌아 점수를 냅니다. 점수는 실행마다 저장되고 <Link to="/lab/experiments">실험</Link> 화면에서 조합별 · 데이터셋별로
        평균됩니다. 다른 도구가 계산한 점수는 API(<span className="mono">PUT …/runs/{"{run}"}/scores</span>)로 넣을 수 있습니다.
      </p>
      {q.isLoading ? <Skeleton lines={2} /> : null}
      {q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
      {q.data && q.data.length === 0 ? <p className="muted small">등록된 평가기가 없습니다. 실행에는 밖에서 넣은 점수만 저장됩니다.</p> : null}
      {q.data && q.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table lab-table" data-testid="registered-evaluators">
            <thead>
              <tr>
                <th scope="col">평가기</th>
                <th scope="col">보는 구간</th>
                <th scope="col">내는 지표</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((e) => (
                <tr key={e.id} data-evaluator={e.id}>
                  <th scope="row">
                    <span className="strategy-name">{e.label}</span>
                    <span className="mono muted small">{e.id}</span>
                    <span className="cell-note">{e.description}</span>
                  </th>
                  <td>{e.sections.map(label).join(" · ")}</td>
                  <td>
                    <ul className="ev-metrics">
                      {e.metrics.map((m) => (
                        <li key={m.id}>
                          {m.label} <span className="mono muted small">{m.id}</span>
                          <span className="muted small">
                            {" "}
                            · {m.unit === "ratio" ? "0–100%" : m.unit || "수"}
                            {directionText(m.direction) ? ` · ${directionText(m.direction)}` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
