// `/lab/experiments`: section experiments — sets of runs over one stretch of the pipeline, one row per set.
// The whole-pipeline evaluation experiments of the later milestone live in PipelineExperiments.tsx.
import { Link, useParams } from "react-router";
import { FlaskConical } from "lucide-react";
import { useBackendKind, useExperiments } from "../api/hooks";
import { type PipelineSection, experimentPath, useArtifacts, useSections, useStageExperiments, useVideoSets } from "../api/stage";
import { bytes, relativeTime } from "../lib/format";
import { ErrorState, Skeleton } from "../ui";
import { StageExperimentView } from "./StageExperimentView";
import { AsksMark, ExperimentStatus, ProgressBar, RangeGlyph, money, rangeLabel } from "./stageParts";

export function ExperimentsPage() {
  const { experimentId } = useParams();
  return experimentId ? <StageExperimentView id={experimentId} /> : <ExperimentList />;
}

function ExperimentList() {
  const list = useStageExperiments();
  const sections = useSections();
  const sets = useVideoSets();
  const all = sections.data ?? [];
  const noVideos = sets.data !== undefined && !sets.data.some((s) => s.items.length > 0);
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <h1 className="lab-title">실험</h1>
          <p className="lab-lead">
            구간 실험은 파이프라인의 한 구간을 정해 두고 그 구간의 실행을 쌓아 가는 묶음입니다. 조합이나 영상을 바꿔 실행을 하나씩 더하고, 조합끼리 영상 · 데이터셋에 걸쳐 비교합니다.
            구간 앞 단계는 이미 끝난 실행에서 이어 가므로 다시 계산하지 않습니다.
          </p>
        </div>
        <Link to="/lab/experiments/new" className="btn btn-primary">
          <FlaskConical size={15} aria-hidden="true" />새 구간 실험
        </Link>
      </header>

      <section className="lab-section" aria-labelledby="sx-list-title">
        <h2 id="sx-list-title" className="lab-section-title">
          구간 실험 {list.data?.length ?? "…"}
        </h2>
        {list.isLoading ? <Skeleton lines={4} /> : null}
        {list.isError ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
        {list.data && list.data.length === 0 ? (
          <div className="empty-state">
            <p className="empty-title">아직 구간 실험이 없습니다</p>
            <div className="empty-body">
              예: &lsquo;영상 정보 추출&rsquo; 구간을 골라 장면 설명 VLM 두 가지를 예시 영상에서 실행하고, 나중에 다른 영상이나 다른 검출기를 같은 실험에 더합니다.
              {noVideos ? " 먼저 실험에 쓸 영상을 데이터셋에 넣어야 합니다." : ""}
            </div>
            <div className="empty-action sx-empty-actions">
              <Link to="/lab/experiments/new" className="btn btn-sm btn-primary">
                새 구간 실험
              </Link>
              {noVideos ? (
                <Link to="/lab/datasets" className="btn btn-sm btn-secondary">
                  영상 데이터셋 만들기
                </Link>
              ) : null}
            </div>
          </div>
        ) : null}
        {list.data && list.data.length > 0 ? (
          <div className="table-wrap">
            <table className="table lab-table sx-list">
              <thead>
                <tr>
                  <th scope="col">실험</th>
                  <th scope="col">구간</th>
                  <th scope="col">기준 프로필</th>
                  <th scope="col" className="num">
                    조합
                  </th>
                  <th scope="col" className="num">
                    영상
                  </th>
                  <th scope="col">실행</th>
                  <th scope="col" className="num">
                    비용
                  </th>
                  <th scope="col">상태</th>
                  <th scope="col">최근 변경</th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((x) => (
                  <tr key={x.id} data-experiment={x.id}>
                    <th scope="row">
                      <Link to={experimentPath(x.id)} className="exp-link">
                        {x.title}
                      </Link>
                      <span className="cell-note mono">{x.id}</span>
                    </th>
                    <td>
                      <span className="sx-range">
                        <RangeGlyph sections={all} from={x.from} to={x.to} />
                        {rangeLabel(all, x.from, x.to)}
                      </span>
                      {x.interactive ? (
                        <span className="cell-note">
                          <AsksMark title="이 구간은 사용자에게 묻습니다. 실행은 한 번에 1건씩 추가하고, 서비스 화면에서 직접 답합니다." />
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <Link to={`/lab/profiles/${x.baseProfile.id}`} className="mono">
                        {x.baseProfile.name}
                      </Link>
                    </td>
                    <td className="num">{x.variants}개</td>
                    <td className="num">{x.items}개</td>
                    <td>
                      <span className="sx-progress-cell">
                        <ProgressBar done={x.progress.done} total={x.progress.total} label={`${x.title} 진행`} />
                        <span className="num">
                          {x.progress.done} / {x.progress.total}건
                        </span>
                      </span>
                      {x.progress.waiting ? <span className="cell-note sx-count-wait">응답 대기 {x.progress.waiting}건</span> : null}
                    </td>
                    <td className="num">
                      {money(x.costUsd)}
                      <span className="cell-note">상한 {money(x.costCapUsd)}</span>
                    </td>
                    <td>
                      <ExperimentStatus status={x.status} waiting={x.progress.waiting} total={x.progress.total} />
                    </td>
                    <td className="muted">{relativeTime(x.updatedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <StepStore sections={all} />
      <PipelineExperimentsPointer />
    </main>
  );
}

/** what later runs can take instead of computing: the reason a second combination is cheaper than the first */
function StepStore({ sections }: { sections: PipelineSection[] }) {
  const store = useArtifacts();
  const steps = Object.entries(store.data?.steps ?? {});
  if (!store.data) return null;
  // in pipeline order: the steps of the sections as they come, anything else (what ingest keeps) in front
  const order = sections.flatMap((s) => s.steps).map((step) => step.node);
  const rank = (node: string) => order.indexOf(node);
  const stored = steps.reduce((sum, [, v]) => sum + v.bytes, 0);
  return (
    <section className="lab-section" aria-labelledby="sx-store-title">
      <h2 id="sx-store-title" className="lab-section-title">
        단계 저장소
      </h2>
      <p className="lab-note">
        같은 영상을 같은 부품으로 계산한 단계 결과입니다. 입력이 같은 단계는 어느 실행에서든 다시 계산하지 않고 여기서 가져옵니다.
        {steps.length ? ` 지금 ${bytes(stored)}를 보관하고 있습니다.` : ""}
      </p>
      {steps.length === 0 ? (
        <p className="muted small">아직 보관된 결과가 없습니다. 영상을 한 번 분석하면 채워집니다.</p>
      ) : (
        <ul className="store-list">
          {steps
            .sort(([, a], [, b]) => rank(a.node) - rank(b.node))
            .map(([name, value]) => (
              <li key={name} title={value.node}>
                <span className="store-name">{value.label}</span>
                <span className="num">{value.entries}건</span>
                <span className="num muted">{bytes(value.bytes)}</span>
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}

/** the whole-pipeline evaluation experiments of the later milestone: shown only where there is something to open */
function PipelineExperimentsPointer() {
  const old = useExperiments();
  const real = useBackendKind() === "real";
  if (!old.data?.length) {
    return real ? (
      <p className="lab-note">
        정답이 있는 시나리오로 전체 파이프라인을 채점해 회귀 게이트를 매기는 평가 실험은 다음 마일스톤(M5)에서 연결됩니다. 그때까지 프로필의 게이트는 &lsquo;평가 전&rsquo;으로
        남습니다.
      </p>
    ) : null;
  }
  return (
    <section className="lab-section sx-later" aria-labelledby="sx-later-title">
      <h2 id="sx-later-title" className="lab-section-title">
        전체 파이프라인 평가 실험 <span className="tag">다음 마일스톤 · 모의 데이터</span>
      </h2>
      <p className="lab-note">
        프로필을 정답 시나리오로 끝까지 돌려 정책 F1과 회귀 게이트를 매기는 화면입니다. 실제 파이프라인에는 M5에 연결되고, 지금은 모의 백엔드의 예시 {old.data.length}건만
        있습니다.
      </p>
      <div>
        <Link to="/lab/experiments/pipeline" className="btn btn-sm btn-secondary">
          평가 실험 열기
        </Link>
      </div>
    </section>
  );
}
