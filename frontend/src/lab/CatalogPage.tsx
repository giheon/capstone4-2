import { useMemo, useState } from "react";
import { Plus, Search, Trash2 } from "lucide-react";
import type { Capability, Catalog, CatalogAdapter, CatalogSlot, ModelTier } from "../api/contract";
import { useAddCandidate, useBackendKind, useCatalog, useLookupCandidate, useProfiles, useRemoveCandidate } from "../api/hooks";
import { relativeTime } from "../lib/format";
import { ErrorState, Skeleton, Spinner, cx } from "../ui";
import { CAP_LABEL, KIND_LABEL, KindBadge, priceText } from "./pipelineParts";

const CAPS: Capability[] = ["tools", "json", "image", "video"];

export function CatalogPage() {
  const catalog = useCatalog();
  const profiles = useProfiles();
  const usage = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const p of profiles.data ?? []) {
      for (const [slot, cand] of Object.entries(p.selections)) map.set(`${slot}:${cand}`, [...(map.get(`${slot}:${cand}`) ?? []), p.id]);
    }
    return map;
  }, [profiles.data]);

  if (catalog.isLoading) return <main className="lab-page"><Skeleton lines={8} /></main>;
  if (catalog.isError || !catalog.data) return <main className="lab-page"><ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} /></main>;
  const c = catalog.data;
  const slots = c.stages.flatMap((s) => s.slots);
  const candidates = slots.reduce((n, s) => n + s.candidates.length, 0);
  const added = slots.reduce((n, s) => n + s.candidates.filter((x) => x.source === "lab").length, 0);

  return (
    <main className="lab-page">
      <header className="lab-page-head">
        <div>
          <h1 className="lab-title">모델 후보</h1>
          <p className="lab-lead">프로필이 고를 수 있는 부품과 후보 전체입니다. 아래 파일에서 읽고, 파일을 고치면 새로고침만으로 반영됩니다.</p>
          <p className="catalog-file">
            <span className="mono">{c.path}</span> · 수정 {relativeTime(c.modifiedAt)} · 부품 {slots.length} · 후보 {candidates}
            {added ? ` (실험실에서 추가 ${added})` : ""}
          </p>
        </div>
      </header>

      <section className="lab-section howto" aria-labelledby="howto-title">
        <h2 id="howto-title" className="lab-section-title">
          후보를 추가하는 두 가지 방법
        </h2>
        <ol className="howto-list">
          <li>
            <strong>이 화면에서</strong> 부품 옆의 '후보 추가'를 누릅니다. OpenRouter 모델은 slug만 넣으면 단가·입력 형식·도구 지원을 공개 목록에서 채웁니다. 추가한 후보는 카탈로그 파일에{" "}
            <span className="mono">source: lab</span>으로 기록되고 프로필 편집기에 바로 나타납니다.
          </li>
          <li>
            <strong>파일에서</strong> <span className="mono">{c.path}</span>의 해당 부품 <span className="mono">candidates</span> 끝에 항목을 붙이고 저장합니다. 서버를 다시 켤 필요가 없습니다.
          </li>
        </ol>
        <p className="lab-note">
          두 방법 모두 이미 등록된 계열(어댑터)의 다른 모델만 추가할 수 있습니다. 구조가 다른 새 계열은 M4 백엔드에 어댑터를 구현한 뒤 파일의 <span className="mono">adapters</span>에 등록해야 합니다. 로컬 모델은 첫 실행 때 가중치를 내려받습니다.
        </p>
      </section>

      <nav className="stage-jump" aria-label="단계로 이동">
        {c.stages.map((s) => (
          <a key={s.id} href={`#stage-${s.id}`}>
            {s.label} <span className="muted num">{s.slots.length}</span>
          </a>
        ))}
      </nav>

      {c.stages.map((stage) => (
        <section key={stage.id} id={`stage-${stage.id}`} className="lab-section catalog-stage" aria-labelledby={`stage-title-${stage.id}`}>
          <div className="editor-stage-head">
            <h2 id={`stage-title-${stage.id}`} className="lab-section-title">
              {stage.label}
            </h2>
            <p className="lab-note">{stage.description}</p>
          </div>
          {stage.slots.map((slot) => (
            <SlotCard key={slot.id} slot={slot} catalog={c} usage={usage} />
          ))}
        </section>
      ))}

      <section className="lab-section" aria-labelledby="adapters-title">
        <h2 id="adapters-title" className="lab-section-title">
          어댑터 계열 {c.adapters.length}
        </h2>
        <div className="table-wrap">
          <table className="table lab-table">
            <thead>
              <tr>
                <th scope="col">계열</th>
                <th scope="col">실행</th>
                <th scope="col">ref의 뜻</th>
                <th scope="col">화면에서 추가</th>
              </tr>
            </thead>
            <tbody>
              {c.adapters.map((a) => (
                <tr key={a.id}>
                  <th scope="row">
                    <span className="part-label">{a.label}</span>
                    <span className="cell-note mono">{a.id}</span>
                  </th>
                  <td>
                    <KindBadge kind={a.kind} />
                  </td>
                  <td>
                    {a.ref || "—"}
                    {a.example ? <span className="cell-note mono">{a.example}</span> : null}
                  </td>
                  <td>{a.addable ? (a.lookup ? "가능 · 공개 목록에서 자동 채움" : "가능") : "코드에서만"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function SlotCard({ slot, catalog, usage }: { slot: CatalogSlot; catalog: Catalog; usage: Map<string, string[]> }) {
  const [open, setOpen] = useState(false);
  const remove = useRemoveCandidate();
  // in the slot's own order, so the first choice is the part's usual family
  const adapters = slot.adapters.map((id) => catalog.adapters.find((a) => a.id === id)).filter((a): a is CatalogAdapter => !!a && a.addable);
  const titleId = `slot-title-${slot.id.replace(/\./g, "-")}`;
  return (
    <article className="slot-card" aria-labelledby={titleId}>
      <header className="slot-card-head">
        <div>
          <h3 id={titleId} className="slot-card-title">
            {slot.label}
          </h3>
          <p className="slot-card-meta">
            <span className="mono">{slot.id}</span> · 노드 <span className="mono">{slot.usedBy.join(", ")}</span>
            {slot.requires.length ? ` · 필요한 기능: ${slot.requires.map((r) => CAP_LABEL[r]).join(", ")}` : ""}
          </p>
        </div>
        {adapters.length ? (
          <button type="button" className="btn btn-sm btn-secondary" aria-expanded={open} onClick={() => setOpen(!open)}>
            <Plus size={14} aria-hidden="true" />
            후보 추가
          </button>
        ) : (
          <span className="muted small">설정 · 코드 부품이라 파일과 코드에서만 바꿉니다</span>
        )}
      </header>
      {open ? <AddCandidateForm slot={slot} adapters={adapters} path={catalog.path} onClose={() => setOpen(false)} /> : null}
      <div className="table-wrap">
        <table className="table lab-table cand-table">
          <thead>
            <tr>
              <th scope="col">후보</th>
              <th scope="col">실행</th>
              <th scope="col">단가 (USD / 1M)</th>
              <th scope="col">라이선스</th>
              <th scope="col">근거</th>
              <th scope="col">쓰는 프로필</th>
              <th scope="col">
                <span className="sr-only">관리</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {slot.candidates.map((c) => {
              const users = usage.get(`${slot.id}:${c.id}`) ?? [];
              const kind = catalog.adapters.find((a) => a.id === c.adapter)?.kind ?? "local";
              return (
                <tr key={c.id} className={cx(c.status === "planned" && "is-planned")}>
                  <th scope="row">
                    <span className="cand-name">
                      <span className="part-label">{c.label}</span>
                      {c.id === slot.default ? <span className="chip">기본</span> : null}
                      {c.source === "lab" ? <span className="chip chip-lab">실험실 추가{c.addedAt ? ` ${c.addedAt}` : ""}</span> : null}
                      {c.status === "planned" ? <span className="chip">구현 예정</span> : null}
                    </span>
                    <span className="cell-note mono">
                      {c.adapter}
                      {c.ref ? ` · ${c.ref}` : ""}
                    </span>
                  </th>
                  <td>
                    <KindBadge kind={kind} />
                  </td>
                  <td className="mono">{priceText(c.price)}</td>
                  <td className="small">{c.license ?? "—"}</td>
                  <td className="small cand-note">
                    {c.note ?? ""}
                    {c.caps?.length ? <span className="cell-note">{c.caps.map((x) => CAP_LABEL[x]).join(" · ")}</span> : null}
                  </td>
                  <td className="mono small">{users.length ? users.join(", ") : <span className="muted">—</span>}</td>
                  <td>
                    {c.source === "lab" ? (
                      <button
                        type="button"
                        className="btn btn-sm btn-ghost btn-icon"
                        aria-label={`${c.label} 후보 지우기`}
                        title={users.length ? "프로필이 쓰는 후보는 지울 수 없습니다" : "카탈로그에서 지우기"}
                        disabled={users.length > 0 || remove.isPending}
                        onClick={() => remove.mutate({ slot: slot.id, id: c.id })}
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {remove.isError ? <ErrorState error={remove.error} /> : null}
    </article>
  );
}

function AddCandidateForm({ slot, adapters, path, onClose }: { slot: CatalogSlot; adapters: CatalogAdapter[]; path: string; onClose: () => void }) {
  const mock = useBackendKind() === "mock";
  const [adapterId, setAdapterId] = useState(adapters[0]?.id ?? "");
  const adapter = adapters.find((a) => a.id === adapterId) ?? adapters[0];
  const [ref, setRef] = useState("");
  const [label, setLabel] = useState("");
  const [priceIn, setPriceIn] = useState("");
  const [priceOut, setPriceOut] = useState("");
  const [caps, setCaps] = useState<Capability[]>(slot.requires);
  const [tier, setTier] = useState<ModelTier>("efficient");
  const [license, setLicense] = useState("");
  const [note, setNote] = useState("");
  const lookup = useLookupCandidate();
  const add = useAddCandidate();
  const isModel = slot.id.startsWith("llm.") || slot.id.startsWith("vlm.") || adapter?.kind === "api";
  const api = adapter?.kind === "api";
  const prefix = slot.id.replace(/\./g, "-");

  const fill = () =>
    lookup.mutate(ref.trim(), {
      onSuccess: (r) => {
        setRef(r.ref);
        setLabel(r.label);
        setPriceIn(String(r.price.in));
        setPriceOut(String(r.price.out));
        setCaps(r.caps);
        setTier(r.tier);
      },
    });

  return (
    <form
      className="add-form"
      onSubmit={(e) => {
        e.preventDefault();
        add.mutate(
          {
            slot: slot.id,
            adapter: adapterId,
            ref: ref.trim(),
            label: label.trim() || ref.trim(),
            price: api ? { in: Number(priceIn), out: Number(priceOut) } : undefined,
            caps: isModel ? caps : undefined,
            tier: slot.id.startsWith("llm.") ? tier : undefined,
            license: api ? undefined : license,
            note,
          },
          {
            onSuccess: () => {
              setRef("");
              setLabel("");
              setPriceIn("");
              setPriceOut("");
              setNote("");
              setLicense("");
              lookup.reset();
            },
          },
        );
      }}
    >
      <div className="add-grid">
        <div className="field">
          <label className="field-label" htmlFor={`${prefix}-adapter`}>
            계열
          </label>
          <select id={`${prefix}-adapter`} className="select" value={adapterId} onChange={(e) => setAdapterId(e.target.value)}>
            {adapters.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
          {adapter ? <span className="field-hint">{KIND_LABEL[adapter.kind]} · 같은 계열의 다른 모델만 추가됩니다</span> : null}
        </div>
        <div className="field field-span2">
          <label className="field-label" htmlFor={`${prefix}-ref`}>
            {adapter?.ref || "모델 ID"}
          </label>
          <span className="input-row">
            <input id={`${prefix}-ref`} className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder={adapter?.example} required />
            {adapter?.lookup === "openrouter" ? (
              <button type="button" className="btn btn-secondary" disabled={!ref.trim() || lookup.isPending} onClick={fill}>
                {lookup.isPending ? <Spinner /> : <Search size={14} aria-hidden="true" />}
                OpenRouter에서 가져오기
              </button>
            ) : null}
          </span>
          {lookup.isSuccess ? (
            <span className="field-hint lookup-ok">
              공개 목록에서 단가 · 기능을 채웠습니다{lookup.data.contextLength ? ` (컨텍스트 ${lookup.data.contextLength.toLocaleString()} 토큰)` : ""}. 목록 단가는 제공자에 따라 다를 수 있고, 등급은 단가로 추정한 값이니 확인하세요.
            </span>
          ) : null}
          {lookup.isError ? <ErrorState error={lookup.error} /> : null}
        </div>
        <label className="field">
          <span className="field-label">화면 이름</span>
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="비우면 ID를 씁니다" />
        </label>
        {api ? (
          <>
            <label className="field">
              <span className="field-label">입력 단가 (USD / 1M 토큰)</span>
              <input className="input num" type="number" min={0} step="any" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} required />
            </label>
            <label className="field">
              <span className="field-label">출력 단가 (USD / 1M 토큰)</span>
              <input className="input num" type="number" min={0} step="any" value={priceOut} onChange={(e) => setPriceOut(e.target.value)} required />
            </label>
          </>
        ) : (
          <label className="field">
            <span className="field-label">라이선스</span>
            <input className="input" value={license} onChange={(e) => setLicense(e.target.value)} placeholder="예: Apache-2.0" />
          </label>
        )}
        {isModel ? (
          <fieldset className="field caps-field">
            <legend className="field-label">기능</legend>
            <span className="caps-options">
              {CAPS.map((cap) => (
                <label key={cap} className="check">
                  <input
                    type="checkbox"
                    checked={caps.includes(cap)}
                    onChange={(e) => setCaps(e.target.checked ? [...caps, cap] : caps.filter((x) => x !== cap))}
                  />
                  {CAP_LABEL[cap]}
                  {slot.requires.includes(cap) ? <span className="muted"> (필수)</span> : null}
                </label>
              ))}
            </span>
          </fieldset>
        ) : null}
        {slot.id.startsWith("llm.") ? (
          <div className="field">
            <label className="field-label" htmlFor={`${prefix}-tier`}>
              등급
            </label>
            <select id={`${prefix}-tier`} className="select" value={tier} onChange={(e) => setTier(e.target.value as ModelTier)}>
              <option value="frontier">상위 모델</option>
              <option value="efficient">경량 모델</option>
            </select>
            {mock ? <span className="field-hint">모의 실행이 동작을 바꾸는 기준입니다</span> : null}
          </div>
        ) : null}
        <label className="field field-wide">
          <span className="field-label">근거 메모</span>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="왜 후보로 넣는지, 벤치마크나 출처" />
        </label>
      </div>
      <div className="add-actions">
        <button type="submit" className="btn btn-primary" disabled={add.isPending || !ref.trim()}>
          {add.isPending ? <Spinner /> : null}
          카탈로그에 추가
        </button>
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          닫기
        </button>
        {add.isSuccess ? (
          <p className="add-done small" role="status">
            '{add.data.label}'을(를) 추가했습니다 · <span className="mono">{path}</span>
          </p>
        ) : null}
      </div>
      {add.isError ? <ErrorState error={add.error} /> : null}
    </form>
  );
}
