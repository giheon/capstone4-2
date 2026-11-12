// `/lab/experiments/new`: a new section experiment — the range of the pipeline its runs go through, the base
// profile, and its first launch. Runs over the same range belong in one experiment, so when one exists for the
// chosen range and profile this screen leads there first.
import { Fragment, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ArrowLeft, ChevronRight, MessageCircleQuestion, Plus } from "lucide-react";
import type { ProfileSummary } from "../api/contract";
import { useProfiles } from "../api/hooks";
import { type PipelineSection, type StageExperimentSummary, useCreateStageExperiment, useSections, useStageExperiments, useStageLimits } from "../api/stage";
import { relativeTime } from "../lib/format";
import { ErrorState, Skeleton, cx } from "../ui";
import { LaunchPanel, type LaunchSeed } from "./LaunchPanel";
import { ExperimentStatus, interactionText, money, rangeLabel } from "./stageParts";

export function StageExperimentBuilder() {
  const sections = useSections();
  const profiles = useProfiles();
  const limits = useStageLimits();
  const existing = useStageExperiments();
  const create = useCreateStageExperiment();
  const navigate = useNavigate();
  const [search] = useSearchParams();

  // a link can carry the range and the profile (`?from=&to=&profile=`)
  const [picked, setPicked] = useState<{ from: string; to: string } | null>(null);
  const [profileId, setProfileId] = useState<string | null>(null);
  /** the cost cap as typed; the server's default until someone types */
  const [capText, setCapText] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  /** the range for which the person asked for a set of its own, although one exists */
  const [separate, setSeparate] = useState<string | null>(null);
  const [seed, setSeed] = useState<LaunchSeed>({});

  if (sections.isLoading || profiles.isLoading || limits.isLoading) {
    return (
      <main className="lab-page">
        <Skeleton lines={8} />
      </main>
    );
  }
  const all = sections.data ?? [];
  const profileList = profiles.data ?? [];
  const base: ProfileSummary | undefined =
    profileList.find((p) => p.id === (profileId ?? search.get("profile"))) ?? profileList.find((p) => p.service.includes("default")) ?? profileList[0];
  const failed = [sections, profiles, limits].find((q) => q.isError);
  if (failed || !base || !limits.data || !all.length) {
    return (
      <main className="lab-page">
        <Link to="/lab/experiments" className="back-link">
          <ArrowLeft size={14} aria-hidden="true" />
          실험
        </Link>
        <ErrorState error={failed?.error ?? new Error("프로필이 없어 실험을 만들 수 없습니다.")} onRetry={failed ? () => void failed.refetch() : undefined} />
      </main>
    );
  }

  const index = (id: string | null | undefined) => all.findIndex((s) => s.id === id);
  const wanted = picked ?? { from: search.get("from") ?? "", to: search.get("to") ?? search.get("from") ?? "" };
  const fromIndex = Math.max(0, index(wanted.from));
  const toIndex = Math.max(fromIndex, index(wanted.to));
  const range = all.slice(fromIndex, toIndex + 1);
  const from = range[0].id;
  const to = range[range.length - 1].id;
  const rangeKey = `${from}:${to}:${base.id}`;
  const asked = range.flatMap((s) => s.interactions.map((i) => ({ section: s.id, ...i })));
  const interactive = asked.length > 0;
  // sections in front of the range that ask: a run there continues one that someone has answered already
  const askedBefore = all.slice(0, fromIndex).filter((s) => s.interactions.length > 0);

  const same = (existing.data ?? []).filter((x) => x.from === from && x.to === to && x.baseProfile.id === base.id);
  const showLaunch = same.length === 0 || separate === rangeKey;

  const { costCapUsdDefault, costCapUsdMax } = limits.data;
  const cap = capText ?? String(costCapUsdDefault);
  const capValue = Number(cap);
  const blocked = capValue > 0 && capValue <= costCapUsdMax ? [] : [`비용 상한은 0보다 크고 ${costCapUsdMax} USD 이하여야 합니다.`];

  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <Link to="/lab/experiments" className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            실험
          </Link>
          <h1 className="lab-title">새 구간 실험</h1>
          <p className="lab-lead">
            파이프라인의 한 구간을 정해 두고, 그 구간을 여러 조합 · 여러 영상으로 실행해 한곳에 쌓습니다. 실행은 만든 뒤에도 계속 더할 수 있고, 구간 앞 단계는 이미 끝난 실행에서
            이어 가므로 다시 계산하지 않습니다.
          </p>
        </div>
      </header>

      <div className="sx-builder">
        <section className="lab-section" aria-labelledby="sx-range-title">
          <h2 id="sx-range-title" className="lab-section-title">
            구간
          </h2>
          <Rail sections={all} from={fromIndex} to={toIndex} onPick={(a, b) => setPicked({ from: all[a].id, to: all[b].id })} />
          <p className="lab-note">구간을 누르면 그 구간만 고릅니다. 이웃 구간까지 이어서 실험하려면 사이의 ＋를 누르세요 (Shift+클릭도 됩니다). 구간은 만든 뒤에는 바꿀 수 없습니다.</p>
          {interactive ? (
            <div className="sx-notice" role="note" data-testid="sx-interactive-notice">
              <MessageCircleQuestion size={16} aria-hidden="true" />
              <div>
                <p className="sx-notice-title">이 구간은 사용자에게 묻습니다 — {interactionText(all, asked)}</p>
                <ul>
                  <li>실행은 한 번에 1건씩 추가합니다 (조합 1개 × 영상 1개).</li>
                  <li>질문에 자동으로 답하지 않습니다. 실행은 묻는 지점에서 멈추고, 서비스 화면에서 직접 답해야 이어집니다.</li>
                  <li>실행을 추가할 때마다 새로 답합니다. 이전 실행의 답을 가져오지 않습니다.</li>
                </ul>
              </div>
            </div>
          ) : askedBefore.length ? (
            <p className="lab-note" data-testid="sx-reuse-note">
              이 구간 앞의 &lsquo;{askedBefore.map((s) => s.label).join(" · ")}&rsquo;은(는) 사용자에게 묻는 구간입니다. 실행은 그 구간까지 사람이 답을 마친 실행에서 이어 가며, 그때의 답을
              그대로 씁니다. 다시 묻지 않습니다.
            </p>
          ) : null}
        </section>

        <section className="lab-section" aria-labelledby="sx-base-title">
          <h2 id="sx-base-title" className="lab-section-title">
            기준 프로필
          </h2>
          <div className="sx-base">
            <label className="field">
              <span className="sr-only">기준 프로필</span>
              <select className="select" value={base.id} onChange={(e) => setProfileId(e.target.value)}>
                {profileList.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.service.includes("default") ? " (서비스 기본)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <p className="lab-note">
              고르지 않은 부품은 모두 이 프로필의 것을 씁니다. 아무것도 바꾸지 않은 조합이 비교의 기준입니다. 프레임 전송: {base.estimate.framesLeaveDevice ? "외부 API로 보냄" : "밖으로 보내지 않음"} ·{" "}
              <Link to={`/lab/profiles/${base.id}`}>프로필 보기</Link>
            </p>
          </div>
        </section>

        {same.length ? <SameRange experiments={same} label={rangeLabel(all, from, to)} profile={base.name} seed={seed} separate={separate === rangeKey} onSeparate={() => setSeparate(rangeKey)} /> : null}

        {showLaunch ? (
          <section className="lab-section" aria-labelledby="sx-first-title">
            <h2 id="sx-first-title" className="lab-section-title">
              첫 실행
            </h2>
            <LaunchPanel
              // another range or profile is another set of parts: the choice starts over
              key={rangeKey}
              target={{ from, to, baseProfile: base.id }}
              range={range}
              sections={all}
              base={base}
              interactive={interactive}
              submitLabel={() => "실험 시작"}
              pending={create.isPending}
              error={create.error}
              blocked={blocked}
              onChange={setSeed}
              onSubmit={(launch) => create.mutate({ ...launch, title: title.trim() || undefined, from, to, baseProfile: base.id, costCapUsd: capValue }, { onSuccess: (x) => navigate(`/lab/experiments/${x.id}`) })}
              onCancel={() => navigate("/lab/experiments")}
            >
              <label className="field">
                <span className="field-label">제목 (선택)</span>
                <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={rangeLabel(all, from, to)} maxLength={80} />
              </label>
              <div className="field">
                <label className="field-label" htmlFor="sx-cap">
                  비용 상한
                </label>
                <span className="param-input">
                  <input
                    id="sx-cap"
                    className="input num"
                    type="number"
                    min={0.1}
                    max={costCapUsdMax}
                    step={0.1}
                    value={cap}
                    onChange={(e) => setCapText(e.target.value)}
                    aria-describedby="sx-cap-hint"
                  />
                  <span className="muted small">USD</span>
                </span>
                <span id="sx-cap-hint" className="field-hint">
                  이 실험의 실행이 쓴 비용을 모두 더해 여기에 닿으면 남은 실행을 건너뜁니다. 나중에 바꿀 수 있습니다.
                </span>
              </div>
            </LaunchPanel>
          </section>
        ) : null}
      </div>
    </main>
  );
}

/* ---------- an experiment with this range exists: add to it rather than start another ---------- */

function SameRange({
  experiments,
  label,
  profile,
  seed,
  separate,
  onSeparate,
}: {
  experiments: StageExperimentSummary[];
  label: string;
  profile: string;
  seed: LaunchSeed;
  separate: boolean;
  onSeparate: () => void;
}) {
  return (
    <section className="lab-section sx-same" aria-labelledby="sx-same-title" data-testid="sx-same-range">
      <h2 id="sx-same-title" className="lab-section-title">
        같은 구간의 실험이 이미 있습니다
      </h2>
      <p className="lab-note">
        &lsquo;{label}&rsquo; 구간 · 기준 프로필 <span className="mono">{profile}</span>의 실행은 한 실험에 모아야 조합끼리, 영상끼리 비교됩니다. 아래 실험에 실행을 추가하세요.
      </p>
      <ul className="sx-same-list">
        {experiments.map((x) => (
          <li key={x.id}>
            <div className="sx-same-main">
              <Link to={`/lab/experiments/${x.id}`} className="exp-link">
                {x.title}
              </Link>
              <span className="cell-note num">
                조합 {x.variants}개 · 영상 {x.items}개 · 실행 {x.progress.total}건 · {money(x.costUsd)} · {relativeTime(x.updatedAt)}
              </span>
            </div>
            <ExperimentStatus status={x.status} waiting={x.progress.waiting} total={x.progress.total} />
            <Link to={`/lab/experiments/${x.id}?add=1`} state={{ launch: seed }} className="btn btn-sm btn-primary">
              <Plus size={14} aria-hidden="true" />
              기존 실험에 추가
            </Link>
          </li>
        ))}
      </ul>
      {separate ? null : (
        <div>
          <button type="button" className="btn btn-sm btn-ghost" onClick={onSeparate}>
            그래도 별도 실험으로 만들기
          </button>
        </div>
      )}
    </section>
  );
}

/* ---------- the rail: the pipeline's sections, with the range to experiment on ---------- */

function Rail({ sections, from, to, onPick }: { sections: PipelineSection[]; from: number; to: number; onPick: (from: number, to: number) => void }) {
  return (
    <div className="rail" role="group" aria-label="실험할 구간">
      {sections.map((section, i) => {
        const role = i < from ? "before" : i > to ? "after" : "in";
        // the gap in front of this section sits on an edge of the range: it can take the neighbour in
        const joins = i > 0 && ((i === from && i - 1 < from) || (i - 1 === to && i > to));
        return (
          <Fragment key={section.id}>
            {i > 0 ? (
              joins ? (
                <button
                  type="button"
                  className="rail-join"
                  onClick={() => onPick(Math.min(from, i - 1), Math.max(to, i))}
                  aria-label={`‘${sections[i === from ? i - 1 : i].label}’까지 넓히기`}
                  title={`‘${sections[i === from ? i - 1 : i].label}’까지 넓히기`}
                >
                  <Plus size={14} aria-hidden="true" />
                </button>
              ) : (
                <span className={cx("rail-link", i > from && i <= to && "is-in")} aria-hidden="true">
                  <ChevronRight size={14} />
                </span>
              )
            ) : null}
            <button
              type="button"
              className={cx("rail-sec", `is-${role}`, section.interactions.length > 0 && "is-asking")}
              aria-pressed={role === "in"}
              onClick={(e) => (e.shiftKey ? onPick(Math.min(from, i), Math.max(to, i)) : onPick(i, i))}
            >
              <span className="rail-role">{role === "before" ? "끝난 실행에서 이어 감" : role === "in" ? "이 구간을 실행" : "실행하지 않음"}</span>
              <span className="rail-name">{section.label}</span>
              <span className="rail-steps">
                {section.steps.map((step) => (
                  <span key={step.node}>{step.label}</span>
                ))}
              </span>
              {section.interactions.length ? (
                <span className="rail-asks">
                  <MessageCircleQuestion size={13} aria-hidden="true" />
                  사용자에게 물음: {section.interactions.map((i) => i.label).join(" · ")}
                </span>
              ) : null}
              <span className="rail-meta">
                부품 {section.slots.length}
                {section.params.length ? ` · 설정값 ${section.params.length}` : ""}
              </span>
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}
