import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Copy, FlaskConical, Pencil, Play, Plus, Trash2 } from "lucide-react";
import type { Catalog, CatalogCandidate, ProfileSummary } from "../api/contract";
import { useBackendKind, useCatalog, useDeleteProfile, useNoSample, useProfiles, usePromoteProfile, useStartDemo } from "../api/hooks";
import { experimentPath } from "../api/stage";
import { relativeTime, usd } from "../lib/format";
import { EmptyState, ErrorState, Skeleton, Spinner, cx } from "../ui";
import { GateChip, IssueList, KindBadge, ServiceChips, groupByStage, priceText, stageLabels } from "./pipelineParts";

export function ProfilesPage() {
  const { profileId } = useParams();
  const profiles = useProfiles();
  const catalog = useCatalog();
  const list = profiles.data ?? [];
  const selected = profileId ? list.find((p) => p.id === profileId) : (list.find((p) => p.service.includes("default")) ?? list[0]);
  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <h1 className="lab-title">프로필</h1>
          <p className="lab-lead">
            파이프라인의 부품마다 후보 하나를 고른 조합입니다. 프로필로 데모를 실행하거나 실험을 돌리고, 회귀 게이트를 통과하면 서비스에 적용합니다.
          </p>
        </div>
        <Link to={`/lab/profiles/new${selected ? `?base=${selected.id}` : ""}`} className="btn btn-primary">
          <Plus size={15} aria-hidden="true" />새 프로필
        </Link>
      </header>
      {profiles.isLoading ? <Skeleton lines={6} /> : null}
      {profiles.isError ? <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} /> : null}
      {profiles.data ? (
        <div className="profile-layout">
          <nav className="profile-nav" aria-label="프로필 목록">
            <ul>
              {list.map((p) => {
                const errors = p.issues.filter((i) => i.level === "error").length;
                return (
                  <li key={p.id}>
                    <Link to={`/lab/profiles/${p.id}`} className={cx("profile-row", p.id === selected?.id && "is-selected")} aria-current={p.id === selected?.id ? "page" : undefined}>
                      <span className="profile-row-top">
                        <span className="mono profile-name">{p.name}</span>
                        <GateChip gate={p.gate} />
                      </span>
                      {p.service.length ? (
                        <span className="profile-row-chips">
                          <ServiceChips service={p.service} />
                        </span>
                      ) : null}
                      <span className="profile-row-meta">
                        <span className="num">≈ {usd(p.estimate.costUsd, 3)}</span> · {p.estimate.framesLeaveDevice ? "프레임 외부 전송" : "프레임 로컬"}
                        {errors ? <span className="profile-row-error"> · 오류 {errors}</span> : null}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          {selected ? (
            <ProfileDetail key={selected.id} profile={selected} profiles={list} catalog={catalog.data} />
          ) : (
            <EmptyState title="프로필을 찾을 수 없습니다">파일이 지워졌거나 ID가 바뀌었을 수 있습니다.</EmptyState>
          )}
        </div>
      ) : null}
      {list.length >= 2 ? <ProfileCompare profiles={list} catalog={catalog.data} /> : null}
    </main>
  );
}

function candidateIndex(catalog?: Catalog): Map<string, CatalogCandidate> {
  const index = new Map<string, CatalogCandidate>();
  for (const stage of catalog?.stages ?? []) for (const slot of stage.slots) for (const c of slot.candidates) index.set(`${slot.id}:${c.id}`, c);
  return index;
}

const paramValue = (v: number, unit: string) => (unit === "USD" ? `$${v.toFixed(2)}` : `${v} ${unit}`);

function ProfileDetail({ profile, profiles, catalog }: { profile: ProfileSummary; profiles: ProfileSummary[]; catalog?: Catalog }) {
  const navigate = useNavigate();
  const startDemo = useStartDemo();
  const promote = usePromoteProfile();
  const remove = useDeleteProfile();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const base = profile.base ? profiles.find((p) => p.id === profile.base) : undefined;
  const errors = profile.issues.filter((i) => i.level === "error");
  const labels = stageLabels(catalog);
  const candidates = useMemo(() => candidateIndex(catalog), [catalog]);
  const cap = profile.params["case.cost_cap_usd"] ?? 0.3;
  const canPromote = profile.gate === "passed" && errors.length === 0;
  // the real backend measures no regression experiments yet, so nothing can be started or promoted from here
  const real = useBackendKind() === "real";
  const noSample = useNoSample();
  const promoteTargets = [
    !profile.service.includes("default") ? { target: "default" as const, label: "서비스 기본으로 적용" } : null,
    !profile.estimate.framesLeaveDevice && !profile.service.includes("local_only") ? { target: "local_only" as const, label: "로컬 처리용으로 적용" } : null,
  ].filter((x): x is { target: "default" | "local_only"; label: string } => !!x);

  return (
    <section className="profile-detail" aria-labelledby="profile-title">
      <header className="profile-detail-head">
        <div className="profile-detail-id">
          <h2 id="profile-title" className="profile-title mono">
            {profile.name}
          </h2>
          {profile.description ? <p className="profile-desc">{profile.description}</p> : null}
          <p className="profile-meta mono">
            config_hash {profile.configHash} · {profile.path} · 수정 {relativeTime(profile.updatedAt)}
            {profile.base ? ` · 기준 ${profile.base}${base ? "" : " (없음)"}` : ""}
          </p>
        </div>
        <div className="profile-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={errors.length > 0 || startDemo.isPending || noSample}
            title={noSample ? "서버에 예시 영상이 없어 데모 실행을 시작할 수 없습니다." : undefined}
            onClick={() => startDemo.mutate(profile.id, { onSuccess: (r) => navigate(`/lab/runs/${r.id}`) })}
          >
            {startDemo.isPending ? <Spinner /> : <Play size={15} aria-hidden="true" />}이 프로필로 실행
          </button>
          <Link className="btn btn-secondary" to={`/lab/experiments/new?profile=${encodeURIComponent(profile.id)}`}>
            <FlaskConical size={15} aria-hidden="true" />
            구간 실험
          </Link>
          {/* scoring a profile against labelled scenarios is the later milestone: only the mock can start one */}
          {real ? null : (
            <Link className="btn btn-ghost" to={`/lab/experiments/pipeline?profiles=${encodeURIComponent(profile.id)}`}>
              평가 실험 시작
            </Link>
          )}
          <Link className="btn btn-ghost" to={`/lab/profiles/${profile.id}/edit`}>
            <Pencil size={15} aria-hidden="true" />
            편집
          </Link>
          <Link className="btn btn-ghost" to={`/lab/profiles/new?base=${profile.id}`}>
            <Copy size={15} aria-hidden="true" />
            복제
          </Link>
        </div>
      </header>
      {startDemo.isError ? <ErrorState error={startDemo.error} /> : null}

      <dl className="profile-facts">
        <div>
          <dt>회귀 게이트</dt>
          <dd>
            <GateChip gate={profile.gate} />
            {profile.lastExperiment ? (
              <Link to={experimentPath(profile.lastExperiment.id)} className="mono small">
                {profile.lastExperiment.id} · F1 {profile.lastExperiment.policyF1.toFixed(2)}
              </Link>
            ) : (
              <span className="muted small">이 설정으로 돌린 평가 실험 없음</span>
            )}
          </dd>
        </div>
        <div>
          <dt>{real ? "1분 영상 1건 추정 비용" : "예시 영상 1건 추정 비용"}</dt>
          <dd className="num">
            {usd(profile.estimate.costUsd, 3)} <span className="muted">/ 상한 {usd(cap)}</span>
          </dd>
        </div>
        <div>
          <dt>프레임 전송</dt>
          <dd>{profile.estimate.framesLeaveDevice ? "외부 API로 보냄" : "이 컴퓨터 밖으로 보내지 않음"}</dd>
        </div>
        <div>
          <dt>부품</dt>
          <dd>
            외부 API {profile.estimate.apiParts} · 로컬 {profile.estimate.localParts} · 전체 {profile.components.length}
          </dd>
        </div>
        <div>
          <dt>서비스</dt>
          <dd>{profile.service.length ? <ServiceChips service={profile.service} /> : <span className="muted">쓰지 않음</span>}</dd>
        </div>
      </dl>

      <IssueList issues={profile.issues} />

      {promoteTargets.length ? (
        <div className="profile-promote">
          {promoteTargets.map((t) => (
            <button
              key={t.target}
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={!canPromote || promote.isPending}
              onClick={() => promote.mutate({ id: profile.id, target: t.target })}
            >
              {t.label}
            </button>
          ))}
          {!canPromote ? (
            <span className="muted small">
              현재 설정으로 게이트를 통과한 실험이 있어야 적용할 수 있습니다.
              {real ? " 정답 시나리오로 채점하는 평가 실험은 다음 마일스톤(M5)에서 연결되어, 그때까지는 적용할 수 없습니다." : ""}
            </span>
          ) : null}
          {promote.isError ? <ErrorState error={promote.error} /> : null}
        </div>
      ) : null}

      <section className="profile-block" aria-labelledby="parts-title">
        <h3 id="parts-title" className="lab-subtitle">
          부품 {profile.components.length}
          {base ? <span className="muted small"> · {base.name}과 다른 부품을 강조했습니다</span> : null}
        </h3>
        <div className="table-wrap">
          <table className="table lab-table parts-table">
            <thead>
              <tr>
                <th scope="col">부품</th>
                <th scope="col">선택한 후보</th>
                <th scope="col">실행</th>
                <th scope="col">단가 (USD / 1M 토큰)</th>
                <th scope="col">쓰는 노드</th>
              </tr>
            </thead>
            {groupByStage(profile.components).map(([stage, parts]) => (
              <tbody key={stage}>
                <tr className="stage-row">
                  <th colSpan={5} scope="rowgroup">
                    {labels[stage] ?? stage}
                  </th>
                </tr>
                {parts.map((c) => {
                  const ref = base?.components.find((x) => x.slot === c.slot);
                  const changed = !!ref && ref.candidate !== c.candidate;
                  const cand = candidates.get(`${c.slot}:${c.candidate}`);
                  return (
                    <tr key={c.slot} className={cx(changed && "is-diff")}>
                      <th scope="row">{c.slotLabel}</th>
                      <td>
                        <span className="part-label">{c.label}</span>
                        {changed ? <span className="cell-note">기준: {ref.label}</span> : cand?.ref ? <span className="cell-note mono">{cand.ref}</span> : null}
                      </td>
                      <td>
                        <KindBadge kind={c.kind} />
                      </td>
                      <td className="mono">{priceText(cand?.price)}</td>
                      <td className="mono small muted">{c.usedBy.join(", ")}</td>
                    </tr>
                  );
                })}
              </tbody>
            ))}
          </table>
        </div>
      </section>

      <section className="profile-block" aria-labelledby="params-title">
        <h3 id="params-title" className="lab-subtitle">
          예산 · 설정
        </h3>
        <dl className="param-grid">
          {(catalog?.params ?? []).map((p) => {
            const v = profile.params[p.id] ?? p.default;
            const bv = base?.params[p.id];
            const changed = base !== undefined && bv !== undefined && bv !== v;
            return (
              <div key={p.id} className={cx(changed && "is-diff")}>
                <dt>{p.label}</dt>
                <dd className="num">
                  {paramValue(v, p.unit)}
                  {changed ? <span className="cell-note">기준 {paramValue(bv, p.unit)}</span> : null}
                </dd>
              </div>
            );
          })}
        </dl>
      </section>

      <footer className="profile-danger">
        {profile.service.length ? (
          <span className="muted small">서비스가 쓰는 프로필은 지울 수 없습니다.</span>
        ) : confirmDelete ? (
          <>
            <span className="small">{profile.path} 파일을 지웁니다.</span>
            <button type="button" className="btn btn-sm btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(profile.id, { onSuccess: () => navigate("/lab/profiles") })}>
              지우기
            </button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirmDelete(false)}>
              취소
            </button>
          </>
        ) : (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirmDelete(true)}>
            <Trash2 size={14} aria-hidden="true" />
            프로필 삭제
          </button>
        )}
        {remove.isError ? <ErrorState error={remove.error} /> : null}
      </footer>
    </section>
  );
}

function ProfileCompare({ profiles, catalog }: { profiles: ProfileSummary[]; catalog?: Catalog }) {
  const service = profiles.find((p) => p.service.includes("default")) ?? profiles[0];
  const other = profiles.find((p) => p.id !== service.id) ?? profiles[0];
  const [left, setLeft] = useState(service.id);
  const [right, setRight] = useState(other.id);
  const a = profiles.find((p) => p.id === left);
  const b = profiles.find((p) => p.id === right);
  return (
    <section className="lab-section" aria-labelledby="diff-title">
      <div className="diff-head">
        <h2 id="diff-title" className="lab-section-title">
          두 프로필 비교
        </h2>
        <ProfileSelect label="왼쪽" value={left} onChange={setLeft} profiles={profiles} />
        <ProfileSelect label="오른쪽" value={right} onChange={setRight} profiles={profiles} />
      </div>
      {a && b ? <ProfileDiff a={a} b={b} catalog={catalog} /> : null}
    </section>
  );
}

function ProfileSelect({ label, value, onChange, profiles }: { label: string; value: string; onChange: (v: string) => void; profiles: ProfileSummary[] }) {
  return (
    <label className="inline-field">
      <span>{label}</span>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function ProfileDiff({ a, b, catalog }: { a: ProfileSummary; b: ProfileSummary; catalog?: Catalog }) {
  const [onlyDiff, setOnlyDiff] = useState(true);
  const labels = stageLabels(catalog);
  const rows: { group: string; key: string; left: string; right: string }[] = [];
  for (const c of a.components) {
    const other = b.components.find((x) => x.slot === c.slot);
    rows.push({ group: labels[c.stage] ?? c.stage, key: c.slotLabel, left: c.label, right: other?.label ?? "—" });
  }
  for (const p of catalog?.params ?? []) {
    rows.push({ group: "예산 · 설정", key: p.label, left: paramValue(a.params[p.id] ?? p.default, p.unit), right: paramValue(b.params[p.id] ?? p.default, p.unit) });
  }
  rows.push({ group: "결과", key: "추정 비용", left: usd(a.estimate.costUsd, 3), right: usd(b.estimate.costUsd, 3) });
  const changed = rows.filter((r) => r.left !== r.right).length;
  const shown = onlyDiff ? rows.filter((r) => r.left !== r.right) : rows;
  return (
    <>
      <div className="section-bar">
        <p className="lab-note">
          다른 항목 {changed}개를 강조했습니다{onlyDiff ? ` · 같은 항목 ${rows.length - changed}개는 숨김` : ""}.
        </p>
        <label className="check small">
          <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          다른 항목만 보기
        </label>
      </div>
      <div className="table-wrap">
        <table className="table lab-table diff">
          <thead>
            <tr>
              <th scope="col">항목</th>
              <th scope="col" className="mono">
                {a.name}
              </th>
              <th scope="col" className="mono">
                {b.name}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={`${r.group}-${r.key}`} className={cx(r.left !== r.right && "is-diff")}>
                <th scope="row">
                  <span className="muted small">{r.group}</span> {r.key}
                </th>
                <td>{r.left}</td>
                <td>{r.right}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
