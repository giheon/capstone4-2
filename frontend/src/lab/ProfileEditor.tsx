import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import type { CatalogAdapter, CatalogSlot, ProfileIssue, ProfileSpec, ProfileSummary } from "../api/contract";
import { useBackendKind, useCatalog, useNoSample, useProfileCheck, useProfiles, useSaveProfile, useSettled, useStartDemo } from "../api/hooks";
import { usd } from "../lib/format";
import { ErrorState, Skeleton, Spinner, cx } from "../ui";
import { IssueList, KindBadge, priceText } from "./pipelineParts";

const TIER_LABEL = { frontier: "상위 모델", efficient: "경량 모델" } as const;

function freeId(base: string, taken: Set<string>): string {
  for (let i = 2; ; i += 1) {
    const id = `${base.replace(/-\d+$/, "")}-${i}`;
    if (!taken.has(id)) return id;
  }
}

export function ProfileEditor() {
  // the mock prices its example scenario; the real backend estimates a one-minute video
  const sampleUnit = useBackendKind() === "real" ? "1분 영상" : "예시 영상";
  const { profileId } = useParams();
  const [search] = useSearchParams();
  const catalog = useCatalog();
  const profiles = useProfiles();
  const editing = profileId ? profiles.data?.find((p) => p.id === profileId) : undefined;
  const reference: ProfileSummary | undefined = editing
    ? (profiles.data?.find((p) => p.id === editing.base) ?? editing)
    : (profiles.data?.find((p) => p.id === search.get("base")) ?? profiles.data?.find((p) => p.service.includes("default")));
  const [draft, setDraft] = useState<ProfileSpec | null>(null);

  useEffect(() => {
    if (draft || !catalog.data || !profiles.data) return;
    if (profileId && !editing) return;
    const slots = catalog.data.stages.flatMap((s) => s.slots);
    const source = editing ?? reference;
    const selections = Object.fromEntries(slots.map((s) => [s.id, source?.selections[s.id] ?? s.default]));
    const params = Object.fromEntries(catalog.data.params.map((p) => [p.id, source?.params[p.id] ?? p.default]));
    if (editing) {
      setDraft({ id: editing.id, name: editing.name, description: editing.description, base: editing.base, selections, params });
    } else {
      const id = freeId(source?.id ?? "profile", new Set(profiles.data.map((p) => p.id)));
      setDraft({ id, name: id, description: "", base: source?.id, selections, params });
    }
  }, [draft, catalog.data, profiles.data, editing, reference, profileId]);

  const debounced = useSettled(draft, 250);
  const check = useProfileCheck(debounced);
  const save = useSaveProfile(editing ? "update" : "create");
  const startDemo = useStartDemo();
  const noSample = useNoSample();
  const navigate = useNavigate();
  const adapters = useMemo(() => new Map((catalog.data?.adapters ?? []).map((a) => [a.id, a])), [catalog.data]);

  if (catalog.isLoading || profiles.isLoading) return <main className="lab-page"><Skeleton lines={8} /></main>;
  if (catalog.isError) return <main className="lab-page"><ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} /></main>;
  if (profileId && profiles.data && !editing) {
    return (
      <main className="lab-page">
        <ErrorState error={new Error(`프로필 '${profileId}'을(를) 찾을 수 없습니다.`)} />
      </main>
    );
  }
  if (!draft || !catalog.data) return <main className="lab-page"><Skeleton lines={8} /></main>;

  const issues = check.data?.issues ?? [];
  const errors = issues.filter((i) => i.level === "error");
  const back = editing ? `/lab/profiles/${editing.id}` : reference ? `/lab/profiles/${reference.id}` : "/lab/profiles";
  const set = (patch: Partial<ProfileSpec>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const submit = (then: "view" | "run") => {
    save.mutate(draft, {
      onSuccess: (saved) => {
        if (then === "view") navigate(`/lab/profiles/${saved.id}`);
        else startDemo.mutate(saved.id, { onSuccess: (r) => navigate(`/lab/runs/${r.id}`) });
      },
    });
  };
  const costDelta = check.data && reference ? check.data.estimate.costUsd - reference.estimate.costUsd : 0;

  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <Link to={back} className="back-link">
            <ArrowLeft size={14} aria-hidden="true" />
            {editing ? editing.name : "프로필"}
          </Link>
          <h1 className="lab-title">{editing ? "프로필 편집" : "새 프로필"}</h1>
          <p className="lab-lead">
            부품마다 후보를 고르면 오른쪽 검사 결과와 {sampleUnit} 1건의 추정 비용이 바로 바뀝니다. 저장하면 <span className="mono">configs/pipeline/profiles/{draft.id || "…"}.yaml</span>에 기록됩니다.
          </p>
        </div>
      </header>
      <form
        className="editor"
        onSubmit={(e) => {
          e.preventDefault();
          submit("view");
        }}
      >
        <div className="editor-main">
          <section className="editor-section" aria-labelledby="basics-title">
            <h2 id="basics-title" className="lab-section-title">
              기본 정보
            </h2>
            <div className="editor-fields">
              <div className="field">
                <label className="field-label" htmlFor="profile-id">
                  ID
                </label>
                <input
                  id="profile-id"
                  className="input mono"
                  value={draft.id}
                  onChange={(e) => {
                    const id = e.target.value.toLowerCase();
                    // the name follows the ID until someone types a name of their own
                    set({ id, name: draft.name === draft.id ? id : draft.name });
                  }}
                  disabled={!!editing}
                  required
                  pattern="[a-z0-9][a-z0-9._\-]{1,47}"
                  aria-describedby="id-hint"
                />
                <span id="id-hint" className="field-hint">
                  파일 이름이 됩니다. 영문 소문자 · 숫자 · '-'
                </span>
              </div>
              <label className="field">
                <span className="field-label">이름</span>
                <input className="input" value={draft.name} onChange={(e) => set({ name: e.target.value })} />
              </label>
              <label className="field field-wide">
                <span className="field-label">설명</span>
                <input className="input" value={draft.description} onChange={(e) => set({ description: e.target.value })} placeholder="무엇을 확인하려는 조합인지 적어 두세요" />
              </label>
            </div>
            {reference && reference.id !== editing?.id ? (
              <p className="lab-note">
                <span className="mono">{reference.name}</span>에서 시작했습니다. 기준과 다르게 고른 부품은 노란 표시로 남습니다.
              </p>
            ) : null}
          </section>

          {catalog.data.stages.map((stage) => (
            <section key={stage.id} className="editor-section" aria-labelledby={`stage-${stage.id}`}>
              <div className="editor-stage-head">
                <h2 id={`stage-${stage.id}`} className="lab-section-title">
                  {stage.label}
                </h2>
                <p className="lab-note">{stage.description}</p>
              </div>
              <div className="slot-grid">
                {stage.slots.map((slot) => (
                  <SlotField
                    key={slot.id}
                    slot={slot}
                    value={draft.selections[slot.id]}
                    baseValue={reference?.selections[slot.id]}
                    adapters={adapters}
                    issues={issues.filter((i) => i.slot === slot.id && i.level !== "info")}
                    onChange={(v) => set({ selections: { ...draft.selections, [slot.id]: v } })}
                  />
                ))}
              </div>
            </section>
          ))}

          <section className="editor-section" aria-labelledby="params-edit-title">
            <h2 id="params-edit-title" className="lab-section-title">
              예산 · 설정
            </h2>
            <div className="param-fields">
              {catalog.data.params.map((p) => {
                const value = draft.params[p.id] ?? p.default;
                const baseValue = reference?.params[p.id];
                const changed = baseValue !== undefined && baseValue !== value;
                const inputId = `param-${p.id.replace(/\./g, "-")}`;
                return (
                  <div key={p.id} className={cx("field param-field", changed && "is-changed")}>
                    <label className="field-label" htmlFor={inputId}>
                      {p.label}
                    </label>
                    <span className="param-input">
                      <input
                        id={inputId}
                        aria-describedby={`${inputId}-hint`}
                        type="number"
                        className="input num"
                        min={p.min}
                        max={p.max}
                        step={p.step}
                        value={value}
                        onChange={(e) => set({ params: { ...draft.params, [p.id]: Number(e.target.value) } })}
                      />
                      <span className="muted small">{p.unit}</span>
                    </span>
                    <span id={`${inputId}-hint`} className="field-hint">
                      {p.min}~{p.max}
                      {changed ? ` · 기준 ${baseValue}` : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        <aside className="editor-side" aria-label="검사 결과">
          <div className="editor-summary" aria-live="polite">
            <p className="summary-label">{sampleUnit} 1건 추정 비용</p>
            <p className="summary-cost num">
              {check.data ? usd(check.data.estimate.costUsd, 3) : "—"}
              {check.isFetching ? <Spinner size={12} label="다시 계산 중" /> : null}
            </p>
            <p className="summary-sub">
              {reference && check.data && Math.abs(costDelta) >= 0.0005 ? (
                <span className={cx(costDelta > 0 ? "delta is-bad" : "delta is-good")}>
                  {reference.name} 대비 {costDelta > 0 ? "+" : "−"}${Math.abs(costDelta).toFixed(3)}
                </span>
              ) : (
                <span className="muted">상한 {usd(draft.params["case.cost_cap_usd"] ?? 0.3)}</span>
              )}
            </p>
            {check.data ? (
              <dl className="summary-facts">
                <div>
                  <dt>프레임 전송</dt>
                  <dd>{check.data.estimate.framesLeaveDevice ? "외부 API로 보냄" : "밖으로 보내지 않음"}</dd>
                </div>
                <div>
                  <dt>실행 위치</dt>
                  <dd>
                    외부 API {check.data.estimate.apiParts} · 로컬 {check.data.estimate.localParts}
                  </dd>
                </div>
                <div>
                  <dt>config_hash</dt>
                  <dd className="mono">{check.data.configHash}</dd>
                </div>
              </dl>
            ) : null}
            <h2 className="summary-title">검사</h2>
            <IssueList issues={issues} empty={check.data ? "오류와 주의할 점이 없습니다." : undefined} />
            {check.isError ? <ErrorState error={check.error} /> : null}
            <div className="summary-actions">
              <button type="submit" className="btn btn-primary" disabled={errors.length > 0 || save.isPending || !check.data}>
                {save.isPending ? <Spinner /> : null}저장
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={errors.length > 0 || save.isPending || startDemo.isPending || !check.data || noSample}
                title={noSample ? "서버에 예시 영상이 없어 데모 실행을 시작할 수 없습니다." : undefined}
                onClick={() => submit("run")}
              >
                저장하고 실행
              </button>
              <Link to={back} className="btn btn-ghost">
                취소
              </Link>
            </div>
            {errors.length ? <p className="summary-blocked small">오류를 고쳐야 저장할 수 있습니다.</p> : null}
            {save.isError ? <ErrorState error={save.error} /> : null}
            {startDemo.isError ? (
              <ErrorState error={new Error(`저장했지만 실행을 시작하지 못했습니다. ${startDemo.error instanceof Error ? startDemo.error.message : ""}`)} />
            ) : null}
          </div>
        </aside>
      </form>
    </main>
  );
}

function SlotField({
  slot,
  value,
  baseValue,
  adapters,
  issues,
  onChange,
}: {
  slot: CatalogSlot;
  value: string;
  baseValue?: string;
  adapters: Map<string, CatalogAdapter>;
  issues: ProfileIssue[];
  onChange: (value: string) => void;
}) {
  const chosen = slot.candidates.find((c) => c.id === value);
  const changed = baseValue !== undefined && baseValue !== value;
  const baseLabel = slot.candidates.find((c) => c.id === baseValue)?.label ?? baseValue;
  const id = `slot-${slot.id.replace(/\./g, "-")}`;
  const groups = [
    { label: "후보", items: slot.candidates.filter((c) => c.source === "file" && c.status !== "planned") },
    { label: "실험실에서 추가", items: slot.candidates.filter((c) => c.source === "lab") },
    { label: "구현 예정 (고를 수 없음)", items: slot.candidates.filter((c) => c.status === "planned") },
  ].filter((g) => g.items.length);
  const kind = chosen ? adapters.get(chosen.adapter)?.kind : undefined;
  return (
    <div className={cx("slot-field", changed && "is-changed", issues.some((i) => i.level === "error") && "has-error")}>
      <div className="slot-head">
        <label htmlFor={id} className="slot-label">
          {slot.label}
        </label>
        {changed ? <span className="changed-mark">기준: {baseLabel}</span> : null}
      </div>
      <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={`${id}-meta`}>
        {!chosen ? <option value={value}>{value} (카탈로그에 없음)</option> : null}
        {groups.map((g) => (
          <optgroup key={g.label} label={g.label}>
            {g.items.map((c) => (
              <option key={c.id} value={c.id} disabled={c.status === "planned"}>
                {c.label}
                {c.id === slot.default ? " · 기본" : ""}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <p id={`${id}-meta`} className="slot-meta">
        {kind ? <KindBadge kind={kind} /> : null}
        {chosen?.price ? <span className="mono">{priceText(chosen.price)}</span> : null}
        {chosen?.tier ? <span>{TIER_LABEL[chosen.tier]}</span> : null}
        {chosen?.license ? <span>{chosen.license}</span> : null}
      </p>
      {chosen?.note ? <p className="slot-note">{chosen.note}</p> : null}
      {issues.length ? <IssueList issues={issues} /> : null}
    </div>
  );
}
