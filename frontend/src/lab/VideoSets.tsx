// The "영상 데이터셋" part of `/lab/datasets`: named sets of videos that section experiments run on.
import { type FormEvent, type ReactNode, useState } from "react";
import { Link } from "react-router";
import { Plus } from "lucide-react";
import { useRuns } from "../api/hooks";
import {
  type ExpectedLabels,
  type VideoItem,
  type VideoSet,
  useAddVideo,
  useAddVideoFromCase,
  useCreateVideoSet,
  useDeleteVideoSet,
  useEditVideo,
  useEditVideoSet,
  useRemoveVideo,
  useVideoSets,
} from "../api/stage";
import { EmptyState, ErrorState, Skeleton, Spinner } from "../ui";
import { SendTag } from "./pipelineParts";
import { secs } from "./stageParts";

export function VideoSets() {
  const sets = useVideoSets();
  const [creating, setCreating] = useState(false);
  return (
    <section className="lab-section" aria-labelledby="vs-title">
      <div className="section-bar">
        <h2 id="vs-title" className="lab-section-title">
          영상 데이터셋 {sets.data?.length ?? "…"}
        </h2>
        <button type="button" className="btn btn-sm btn-secondary" aria-expanded={creating} onClick={() => setCreating(!creating)}>
          <Plus size={14} aria-hidden="true" />새 데이터셋
        </button>
      </div>
      <p className="lab-note">
        <Link to="/lab/experiments">구간 실험</Link>이 돌리는 영상 묶음입니다. 영상마다 메모와, 알고 있다면 영상에 실제로 든 것(정답)을 함께 둡니다. 분석 중에 나오는 질문은
        실험이 대신 답하지 않고, 실행을 추가한 사람이 서비스 화면에서 직접 답합니다.
      </p>
      {creating ? <NewSet onDone={() => setCreating(false)} /> : null}
      {sets.isLoading ? <Skeleton lines={3} /> : null}
      {sets.isError ? <ErrorState error={sets.error} onRetry={() => void sets.refetch()} /> : null}
      {sets.data && sets.data.length === 0 && !creating ? (
        <EmptyState
          title="아직 영상 데이터셋이 없습니다"
          action={
            <button type="button" className="btn btn-sm btn-primary" onClick={() => setCreating(true)}>
              새 데이터셋
            </button>
          }
        >
          데이터셋을 만든 뒤 영상을 올리거나, 서비스에서 이미 처리한 실행의 영상을 넣으세요.
        </EmptyState>
      ) : null}
      {(sets.data ?? []).map((set) => (
        <SetCard key={set.id} set={set} />
      ))}
    </section>
  );
}

/* ---------- a set: its name, what it is, and whether its videos may leave this machine ---------- */

interface SetDraft {
  name: string;
  description: string;
  sendable: boolean;
}

function SetFields({ draft, onChange, group }: { draft: SetDraft; onChange: (draft: SetDraft) => void; group: string }) {
  return (
    <>
      <div className="vs-form-grid">
        <label className="field">
          <span className="field-label">이름</span>
          <input className="input" value={draft.name} onChange={(e) => onChange({ ...draft, name: e.target.value })} required maxLength={60} placeholder="예: 팀 촬영본 · 매장" />
        </label>
        <label className="field">
          <span className="field-label">설명 (선택)</span>
          <input className="input" value={draft.description} onChange={(e) => onChange({ ...draft, description: e.target.value })} maxLength={200} placeholder="어떤 영상을 모았는지" />
        </label>
      </div>
      <fieldset className="vs-send">
        <legend className="field-label">이 영상들을 외부 모델로 보내도 되나요?</legend>
        <label className="check">
          <input type="radio" name={group} checked={!draft.sendable} onChange={() => onChange({ ...draft, sendable: false })} />
          <span>
            <b>금지</b> — 프레임을 클라우드 모델로 보내지 않습니다. 프레임을 외부로 보내는 프로필은 이 데이터셋의 기준이 될 수 없고, 그런 조합은 이 영상을 건너뜁니다. 실제
            사람이 찍힌 영상은 이쪽으로 두세요.
          </span>
        </label>
        <label className="check">
          <input type="radio" name={group} checked={draft.sendable} onChange={() => onChange({ ...draft, sendable: true })} />
          <span>
            <b>허용</b> — 외부 API 모델을 쓰는 조합도 이 영상을 받습니다. 합성 영상이거나, 찍힌 사람 모두에게 전송 동의를 받은 영상만 넣으세요.
          </span>
        </label>
      </fieldset>
    </>
  );
}

function NewSet({ onDone }: { onDone: () => void }) {
  const create = useCreateVideoSet();
  const [draft, setDraft] = useState<SetDraft>({ name: "", description: "", sendable: false });
  return (
    <form
      className="vs-form"
      aria-label="새 영상 데이터셋"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate(draft, { onSuccess: onDone });
      }}
    >
      <SetFields draft={draft} onChange={setDraft} group="vs-sendable-new" />
      <div className="exp-actions">
        <button type="submit" className="btn btn-sm btn-primary" disabled={create.isPending || !draft.name.trim()}>
          {create.isPending ? <Spinner /> : null}
          데이터셋 만들기
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onDone}>
          취소
        </button>
      </div>
      {create.isError ? <ErrorState error={create.error} /> : null}
    </form>
  );
}

function EditSet({ set, onDone }: { set: VideoSet; onDone: () => void }) {
  const edit = useEditVideoSet(set.id);
  const [draft, setDraft] = useState<SetDraft>({ name: set.name, description: set.description, sendable: set.sendable });
  return (
    <form
      className="vs-form"
      aria-label={`${set.name} 고치기`}
      onSubmit={(e) => {
        e.preventDefault();
        edit.mutate(draft, { onSuccess: onDone });
      }}
    >
      <SetFields draft={draft} onChange={setDraft} group={`vs-sendable-${set.id}`} />
      {draft.sendable && !set.sendable ? (
        <p className="vs-caution" role="note">
          허용으로 바꾸면 다음 실험부터 이 데이터셋의 영상 {set.items.length}개가 외부 API 모델로 전송될 수 있습니다.
        </p>
      ) : null}
      <div className="exp-actions">
        <button type="submit" className="btn btn-sm btn-primary" disabled={edit.isPending || !draft.name.trim()}>
          {edit.isPending ? <Spinner /> : null}
          저장
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onDone}>
          취소
        </button>
      </div>
      {edit.isError ? <ErrorState error={edit.error} /> : null}
    </form>
  );
}

function SetCard({ set }: { set: VideoSet }) {
  const remove = useDeleteVideoSet();
  const [panel, setPanel] = useState<"add" | "edit" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const title = `vs-${set.id}`;
  const columns = set.builtin ? 5 : 6;
  return (
    <article className="vset" aria-labelledby={title} data-set={set.id}>
      <header className="vset-head">
        <div className="vset-id">
          <h3 id={title} className="lab-subtitle">
            {set.name}
          </h3>
          <SendTag sendable={set.sendable} />
          {set.builtin ? <span className="tag">기본 제공</span> : null}
          <span className="mono muted small">{set.id}</span>
        </div>
        {set.builtin ? null : (
          <div className="vset-actions">
            <button type="button" className="btn btn-sm btn-secondary" aria-expanded={panel === "add"} onClick={() => setPanel(panel === "add" ? null : "add")}>
              영상 추가
            </button>
            <button type="button" className="btn btn-sm btn-ghost" aria-expanded={panel === "edit"} onClick={() => setPanel(panel === "edit" ? null : "edit")}>
              데이터셋 고치기
            </button>
            {confirming ? (
              <span className="confirm" role="group" aria-label="데이터셋 지우기 확인">
                <span>
                  이 데이터셋{set.items.length ? `과 영상 ${set.items.length}개` : ""}를 지웁니다.
                </span>
                <button type="button" className="btn btn-sm btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(set.id)}>
                  {remove.isPending ? <Spinner /> : null}
                  지우기
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(false)}>
                  취소
                </button>
              </span>
            ) : (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(true)}>
                데이터셋 지우기
              </button>
            )}
          </div>
        )}
      </header>
      {set.description ? <p className="vset-desc">{set.description}</p> : null}
      {remove.isError ? <ErrorState error={remove.error} /> : null}
      {panel === "edit" ? <EditSet set={set} onDone={() => setPanel(null)} /> : null}
      {panel === "add" ? <AddVideo set={set} onDone={() => setPanel(null)} /> : null}
      {set.items.length === 0 ? (
        <p className="muted small">아직 영상이 없습니다. &lsquo;영상 추가&rsquo;로 파일을 올리거나 기존 실행의 영상을 넣으세요.</p>
      ) : (
        <div className="table-wrap">
          <table className="table lab-table vset-table">
            <thead>
              <tr>
                <th scope="col">영상</th>
                <th scope="col" className="num">
                  길이
                </th>
                <th scope="col">메모</th>
                <th scope="col">정답</th>
                <th scope="col">가져온 곳</th>
                {set.builtin ? null : (
                  <th scope="col">
                    <span className="sr-only">고치기 · 빼기</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {set.items.map((item) => (
                <VideoRow key={item.id} set={set} item={item} columns={columns} editing={editing === item.id} onEdit={(on) => setEditing(on ? item.id : null)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

/* ---------- a video: the memo a run starts with, and what is known to be in it ---------- */

interface VideoDraft {
  title: string;
  memo: string;
  /** one text per line */
  texts: string;
  people: string;
  plates: string;
  scenes: string;
}

const BLANK: VideoDraft = { title: "", memo: "", texts: "", people: "", plates: "", scenes: "" };

function draftOf(item: VideoItem): VideoDraft {
  const count = (value: number | undefined) => (value === undefined ? "" : String(value));
  return {
    title: item.title,
    memo: item.memo,
    texts: (item.expected?.texts ?? []).join("\n"),
    people: count(item.expected?.people),
    plates: count(item.expected?.plates),
    scenes: count(item.expected?.scenes),
  };
}

/** what the draft says is in the video; `{}` when nothing is filled in */
function expectedOf(draft: VideoDraft): ExpectedLabels {
  const count = (text: string) => (text.trim() === "" ? undefined : Math.max(0, Math.round(Number(text))));
  const lines = draft.texts.split("\n").map((t) => t.trim()).filter(Boolean);
  return {
    ...(lines.length ? { texts: lines } : {}),
    ...(count(draft.people) !== undefined ? { people: count(draft.people) } : {}),
    ...(count(draft.plates) !== undefined ? { plates: count(draft.plates) } : {}),
    ...(count(draft.scenes) !== undefined ? { scenes: count(draft.scenes) } : {}),
  };
}

function expectedText(expected: ExpectedLabels | null): string {
  if (!expected) return "";
  return [
    expected.texts?.length ? `글자 ${expected.texts.length}` : "",
    expected.people !== undefined ? `인물 ${expected.people}` : "",
    expected.plates !== undefined ? `번호판 ${expected.plates}` : "",
    expected.scenes !== undefined ? `장면 ${expected.scenes}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** what is known to be in a video: the same fields when adding and when correcting */
function LabelFields({ draft, onChange }: { draft: VideoDraft; onChange: (draft: VideoDraft) => void }) {
  const set = (patch: Partial<VideoDraft>) => onChange({ ...draft, ...patch });
  return (
    <div className="vs-form-grid">
      <label className="field field-wide">
        <span className="field-label">영상에 보이는 글자 — 한 줄에 하나</span>
        <textarea className="textarea vs-memo" value={draft.texts} onChange={(e) => set({ texts: e.target.value })} placeholder={"김○○ 바리스타\n○○커피"} />
      </label>
      <div className="vs-counts field-wide">
        <label className="field">
          <span className="field-label">인물 수</span>
          <input className="input num" type="number" min={0} step={1} value={draft.people} onChange={(e) => set({ people: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">번호판 수</span>
          <input className="input num" type="number" min={0} step={1} value={draft.plates} onChange={(e) => set({ plates: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">장면 수</span>
          <input className="input num" type="number" min={0} step={1} value={draft.scenes} onChange={(e) => set({ scenes: e.target.value })} />
        </label>
      </div>
    </div>
  );
}

function VideoRow({ set, item, columns, editing, onEdit }: { set: VideoSet; item: VideoItem; columns: number; editing: boolean; onEdit: (on: boolean) => void }) {
  const remove = useRemoveVideo(set.id);
  const [confirming, setConfirming] = useState(false);
  const expected = expectedText(item.expected);
  return (
    <>
      <tr data-item={item.id}>
        <th scope="row">
          {item.title}
          <span className="cell-note mono">
            {item.filename}
            {item.width && item.height ? ` · ${item.width}×${item.height}` : ""}
            {item.missing ? " · 파일 없음" : ""}
          </span>
        </th>
        <td className="num">{secs(item.durationSec)}</td>
        <td className="vset-memo">{item.memo || <span className="muted">—</span>}</td>
        <td>{expected || <span className="muted">없음</span>}</td>
        <td>
          {item.origin ? (
            <Link to={`/lab/runs/${item.origin}`} className="mono small">
              {item.origin}
            </Link>
          ) : (
            <span className="muted">{set.builtin ? "기본 제공" : "업로드"}</span>
          )}
        </td>
        {set.builtin ? null : (
          <td className="vset-row-actions">
            {confirming ? (
              <span className="confirm" role="group" aria-label="영상 빼기 확인">
                <button type="button" className="btn btn-sm btn-danger" disabled={remove.isPending} onClick={() => remove.mutate(item.id)}>
                  {remove.isPending ? <Spinner /> : null}
                  빼기
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(false)}>
                  취소
                </button>
              </span>
            ) : (
              <>
                <button type="button" className="btn btn-sm btn-ghost" aria-expanded={editing} onClick={() => onEdit(!editing)} aria-label={`${item.title} 고치기`}>
                  고치기
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setConfirming(true)} aria-label={`${item.title} 빼기`}>
                  빼기
                </button>
              </>
            )}
            {remove.isError ? <ErrorState error={remove.error} /> : null}
          </td>
        )}
      </tr>
      {editing ? (
        <tr className="vset-edit-row">
          <td colSpan={columns}>
            <EditVideo set={set} item={item} onDone={() => onEdit(false)} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function EditVideo({ set, item, onDone }: { set: VideoSet; item: VideoItem; onDone: () => void }) {
  const edit = useEditVideo(set.id);
  const [draft, setDraft] = useState<VideoDraft>(() => draftOf(item));
  return (
    <form
      className="vs-add"
      aria-label={`${item.title} 고치기`}
      onSubmit={(e) => {
        e.preventDefault();
        // an empty `expected` clears the labels
        edit.mutate({ itemId: item.id, title: draft.title.trim() || item.title, memo: draft.memo, expected: expectedOf(draft) }, { onSuccess: onDone });
      }}
    >
      <div className="vs-form-grid">
        <label className="field">
          <span className="field-label">제목</span>
          <input className="input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} maxLength={200} required />
        </label>
        <label className="field field-wide">
          <span className="field-label">메모</span>
          <textarea className="textarea vs-memo" value={draft.memo} onChange={(e) => setDraft({ ...draft, memo: e.target.value })} maxLength={4000} />
        </label>
      </div>
      <p className="lab-note">정답을 비우면 이 영상은 정답과 비교하지 않습니다. 고친 내용은 다음 실험부터 쓰입니다.</p>
      <LabelFields draft={draft} onChange={setDraft} />
      <div className="exp-actions">
        <button type="submit" className="btn btn-sm btn-primary" disabled={edit.isPending}>
          {edit.isPending ? <Spinner /> : null}
          저장
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onDone}>
          취소
        </button>
      </div>
      {edit.isError ? <ErrorState error={edit.error} /> : null}
    </form>
  );
}

/* ---------- adding a video ---------- */

function AddVideo({ set, onDone }: { set: VideoSet; onDone: () => void }) {
  const [mode, setMode] = useState<"upload" | "run">("upload");
  return (
    <div className="vs-form" role="group" aria-label={`${set.name}에 영상 추가`}>
      <div className="segmented" role="radiogroup" aria-label="영상을 가져올 곳">
        <label>
          <input type="radio" name={`vs-add-${set.id}`} checked={mode === "upload"} onChange={() => setMode("upload")} />
          파일 올리기
        </label>
        <label>
          <input type="radio" name={`vs-add-${set.id}`} checked={mode === "run"} onChange={() => setMode("run")} />
          기존 실행에서
        </label>
      </div>
      {mode === "upload" ? <UploadVideo set={set} onDone={onDone} /> : <VideoFromRun set={set} onDone={onDone} />}
    </div>
  );
}

function Actions({ pending, disabled, label, onCancel, children }: { pending: boolean; disabled: boolean; label: string; onCancel: () => void; children?: ReactNode }) {
  return (
    <div className="exp-actions">
      <button type="submit" className="btn btn-sm btn-primary" disabled={disabled || pending}>
        {pending ? <Spinner /> : null}
        {label}
      </button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={onCancel}>
        취소
      </button>
      {children}
    </div>
  );
}

function UploadVideo({ set, onDone }: { set: VideoSet; onDone: () => void }) {
  const add = useAddVideo(set.id);
  const [file, setFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<VideoDraft>(BLANK);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    const expected = expectedOf(draft);
    add.mutate({ file, title: draft.title.trim(), memo: draft.memo, expected: Object.keys(expected).length ? expected : null }, { onSuccess: onDone });
  };
  return (
    <form className="vs-add" onSubmit={submit}>
      <div className="vs-form-grid">
        <label className="field">
          <span className="field-label">영상 파일</span>
          <input className="input" type="file" accept="video/*,.mp4,.mov,.mkv,.webm" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">
          <span className="field-label">제목 (선택)</span>
          <input className="input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} maxLength={200} placeholder={file?.name ?? "비우면 파일 이름"} />
        </label>
        <label className="field field-wide">
          <span className="field-label">메모 (선택)</span>
          <textarea
            className="textarea vs-memo"
            value={draft.memo}
            onChange={(e) => setDraft({ ...draft, memo: e.target.value })}
            maxLength={4000}
            placeholder="서비스에서 영상을 올릴 때 적는 메모와 같습니다. 예: 유튜브에 올릴 매장 홍보 영상, 직원은 동의함."
          />
        </label>
      </div>
      <details className="vs-more">
        <summary>정답 (선택)</summary>
        <p className="lab-note">정답을 적어 두면 영상 정보 추출의 결과를 정답과 비교해 점수를 냅니다. 비워 둔 항목은 채점하지 않습니다. 나중에 고칠 수 있습니다.</p>
        <LabelFields draft={draft} onChange={setDraft} />
      </details>
      <Actions pending={add.isPending} disabled={!file} label={add.isPending && add.progress !== null ? `올리는 중 ${Math.round(add.progress * 100)}%` : "영상 추가"} onCancel={onDone} />
      {add.isError ? <ErrorState error={add.error} /> : null}
    </form>
  );
}

function VideoFromRun({ set, onDone }: { set: VideoSet; onDone: () => void }) {
  const runs = useRuns();
  const add = useAddVideoFromCase(set.id);
  const [caseId, setCaseId] = useState("");
  const [title, setTitle] = useState("");
  // a run with events is a case on the server: its video and memo can be taken
  const choices = (runs.data ?? []).filter((r) => r.source === "live");
  const taken = new Set(set.items.map((i) => i.origin).filter(Boolean));
  return (
    <form
      className="vs-add"
      onSubmit={(e) => {
        e.preventDefault();
        if (caseId) add.mutate({ caseId, title: title.trim() || undefined }, { onSuccess: onDone });
      }}
    >
      <p className="lab-note">그 실행이 쓴 영상을 메모와 함께 가져옵니다. 정답은 넣은 뒤 &lsquo;고치기&rsquo;에서 적습니다.</p>
      <div className="vs-form-grid">
        <label className="field">
          <span className="field-label">실행</span>
          <select className="select" value={caseId} onChange={(e) => setCaseId(e.target.value)} required>
            <option value="">{runs.isLoading ? "불러오는 중…" : choices.length ? "실행을 고르세요" : "가져올 실행이 없습니다"}</option>
            {choices.map((r) => (
              <option key={r.id} value={r.id} disabled={taken.has(r.id)}>
                {r.title} · {r.id}
                {taken.has(r.id) ? " (이미 있음)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field-label">제목 (선택)</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="비우면 실행의 제목" />
        </label>
      </div>
      <Actions pending={add.isPending} disabled={!caseId} label="영상 추가" onCancel={onDone} />
      {runs.isError ? <ErrorState error={runs.error} /> : null}
      {add.isError ? <ErrorState error={add.error} /> : null}
    </form>
  );
}
