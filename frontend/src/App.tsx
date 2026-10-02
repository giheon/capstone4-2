import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronRight,
  FileText,
  FolderOpen,
  History,
  Layers3,
  Plus,
  Shield,
  Upload,
  Video,
} from "lucide-react";
import { createRun, post, request } from "./api";
import {
  Badge,
  Empty,
  ErrorNotice,
  FixtureBadge,
  FixtureNotice,
  formatDate,
  Loading,
  Panel,
  ProfileDetails,
  ProfileSelect,
  Tick,
} from "./components";
import { busyStatuses, indexMatchesProfile, profileNames } from "./types";
import type {
  DocumentRecord,
  Health,
  IndexRecord,
  Profile,
  Run,
  VideoRecord,
} from "./types";
import RunWorkspace from "./RunWorkspace";

type View = "workspace" | "documents" | "history";

export default function App() {
  const [view, setView] = useState<View>("workspace");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [profile, setProfile] = useState("baseline");
  const client = useQueryClient();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => request<Health>("/health"),
    refetchInterval: 15000,
  });
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: () => request<Profile[]>("/profiles"),
  });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => request<DocumentRecord[]>("/documents"),
    refetchInterval: (query) =>
      query.state.data?.some((document) =>
        ["UPLOADED", "QUEUED", "EXTRACTING"].includes(document.status ?? ""),
      )
        ? 1500
        : view === "documents"
          ? 5000
          : false,
  });
  const indexes = useQuery({
    queryKey: ["indexes"],
    queryFn: () => request<IndexRecord[]>("/indexes"),
    refetchInterval: (query) =>
      query.state.data?.some((index) =>
        ["QUEUED", "INDEXING"].includes(index.status),
      )
        ? 1500
        : view === "documents"
          ? 5000
          : false,
  });
  const videos = useQuery({
    queryKey: ["videos"],
    queryFn: () => request<VideoRecord[]>("/videos"),
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => request<Run[]>("/runs"),
    refetchInterval: (query) =>
      query.state.data?.some((run) => busyStatuses.has(run.status ?? "QUEUED"))
        ? 1500
        : view === "history"
          ? 5000
          : false,
  });
  const active = useQuery({
    queryKey: ["run", activeId],
    queryFn: () => request<Run>(`/runs/${activeId}`),
    enabled: !!activeId,
    refetchInterval: (query) =>
      query.state.data && busyStatuses.has(query.state.data.status ?? "QUEUED")
        ? 1200
        : false,
  });
  const selectRun = (run: Run) => {
    client.setQueryData(["run", run.id], run);
    setActiveId(run.id);
    setView("workspace");
  };
  const newRun = () => {
    setActiveId(null);
    setView("workspace");
  };
  const profileList = profiles.data ?? [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            newRun();
          }}
        >
          <span className="brand-mark">
            <Shield size={23} />
          </span>
          <span>
            PrivFrame<small>영상 보호 실험</small>
          </span>
        </a>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="주 메뉴">
          <button
            className={view === "workspace" ? "active" : ""}
            onClick={() => setView("workspace")}
          >
            <Video size={19} />
            실험 작업 공간
          </button>
          <button
            className={view === "documents" ? "active" : ""}
            onClick={() => setView("documents")}
          >
            <FolderOpen size={19} />
            근거 자료
            <span className="nav-count">{documents.data?.length ?? 0}</span>
          </button>
          <button
            className={view === "history" ? "active" : ""}
            onClick={() => setView("history")}
          >
            <History size={19} />
            실행 기록<span className="nav-count">{runs.data?.length ?? 0}</span>
          </button>
        </nav>
        <div className="sidebar-note">
          <span className="small-label">RESEARCH PROTOTYPE</span>
          <p>
            구성을 바꾸고,
            <br />
            같은 영상으로 비교하세요.
          </p>
          <span className="sidebar-line" />
          <small>판정과 사용자 지정, 실행 당시의 근거를 함께 보존합니다.</small>
        </div>
        <div className="connection">
          <span className={`status-dot ${health.isSuccess ? "online" : ""}`} />
          <span>
            {health.isSuccess
              ? "로컬 서비스 연결됨"
              : health.isPending
                ? "서비스 연결 중"
                : "서비스 연결 확인 필요"}
          </span>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <span>
            작업 공간 <ChevronRight size={14} />
            <b>
              {view === "documents"
                ? "근거 자료"
                : view === "history"
                  ? "실행 기록"
                  : activeId
                    ? "실행 검토"
                    : "새 실험"}
            </b>
          </span>
          <button className="button subtle small" onClick={newRun}>
            <Plus size={16} />새 실험
          </button>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <span className="eyebrow">PRIVFRAME LAB</span>
              <h1>
                {view === "documents"
                  ? "판정의 근거가 되는 자료"
                  : view === "history"
                    ? "실험을 이어서 비교하세요"
                    : activeId
                      ? "분석부터 출력까지, 실행 검토"
                      : "영상 비식별화 실험"}
              </h1>
              <p>
                {view === "documents"
                  ? "자료를 추가하고 검색에 사용할 자료 묶음을 만드세요."
                  : view === "history"
                    ? "이전 결과를 확인하거나 구성을 바꿔 새 실행을 시작하세요."
                    : activeId
                      ? "얼굴별 처리와 사용한 근거를 확인한 뒤 결과를 생성하세요."
                      : "영상과 상황을 입력하고, 근거와 함께 처리 방법을 검토하세요."}
              </p>
            </div>
            <span className="version-pill">PROTOTYPE 01</span>
          </div>
          <ErrorNotice
            error={
              health.error ?? profiles.error ?? videos.error ?? active.error
            }
          />
          {health.data &&
            (!health.data.redis_available ||
              !health.data.ffmpeg_available ||
              !health.data.api_key_configured) && (
              <div className="notice info">
                <Layers3 size={18} />
                <span>
                  {!health.data.redis_available &&
                    "작업 서비스가 연결되지 않았습니다. "}
                  {!health.data.ffmpeg_available &&
                    "영상 출력 도구가 준비되지 않았습니다. "}
                  {!health.data.api_key_configured &&
                    "실제 모델 사용에는 서버의 OpenAI 연결 키 설정이 필요합니다. 테스트 픽스처는 별도로 선택할 수 있습니다."}
                </span>
              </div>
            )}
          {view === "workspace" &&
            (!activeId ? (
              <NewExperiment
                profiles={profileList}
                profile={profile}
                setProfile={setProfile}
                indexes={indexes.data ?? []}
                videos={videos.data ?? []}
                onCreated={selectRun}
                onDocuments={() => setView("documents")}
              />
            ) : active.isPending ? (
              <Loading text="실행 기록을 불러오는 중" />
            ) : active.data ? (
              <RunWorkspace
                key={active.data.id}
                run={active.data}
                profiles={profileList}
                indexes={indexes.data ?? []}
                allRuns={runs.data ?? []}
                video={videos.data?.find(
                  (video) => video.id === active.data.video_id,
                )}
                onRun={selectRun}
              />
            ) : (
              <ErrorNotice error={active.error} />
            ))}
          {view === "documents" && (
            <Documents
              documents={documents.data ?? []}
              indexes={indexes.data ?? []}
              profiles={profileList}
              profile={profile}
              setProfile={setProfile}
              error={documents.error ?? indexes.error}
            />
          )}
          {view === "history" && (
            <Panel
              title="실행 기록"
              subtitle="각 실행의 입력, 구성, 근거와 결과는 독립적으로 남습니다."
              accessory={
                <span className="counter">{runs.data?.length ?? 0}개 실행</span>
              }
            >
              <ErrorNotice error={runs.error} />
              {runs.isPending ? (
                <Loading />
              ) : !runs.data?.length ? (
                <Empty>
                  아직 실행이 없습니다. 첫 영상을 추가해 실험을 시작하세요.
                </Empty>
              ) : (
                <div className="history-list">
                  {runs.data.map((run) => (
                    <button
                      className="history-row"
                      key={run.id}
                      onClick={() => selectRun(run)}
                    >
                      <span className="history-icon">
                        <Video size={20} />
                      </span>
                      <span className="history-content">
                        <strong>
                          {videos.data?.find(
                            (video) => video.id === run.video_id,
                          )?.filename ?? "영상 실험"}
                        </strong>
                        <span>{run.scenario.context}</span>
                        <small>
                          {formatDate(run.created_at)} ·{" "}
                          {profileNames[String(run.config.name)] ??
                            String(run.config.name)}
                          {run.parent_id ? " · 비교 실행" : ""}
                        </small>
                      </span>
                      <span className="history-status">
                        {run.config.mode === "fixture" && <FixtureBadge />}
                        <Badge status={run.status} />
                      </span>
                      <ChevronRight size={19} />
                    </button>
                  ))}
                </div>
              )}
            </Panel>
          )}
        </main>
        <footer>
          PrivFrame 연구 프로토타입{" "}
          <span>출력 결과는 법적 적합성의 보장을 의미하지 않습니다.</span>
        </footer>
      </div>
    </div>
  );
}

function NewExperiment({
  profiles,
  profile,
  setProfile,
  indexes,
  videos,
  onCreated,
  onDocuments,
}: {
  profiles: Profile[];
  profile: string;
  setProfile: (value: string) => void;
  indexes: IndexRecord[];
  videos: VideoRecord[];
  onCreated: (run: Run) => void;
  onDocuments: () => void;
}) {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const videoInput = useRef<HTMLInputElement>(null);
  const [videoId, setVideoId] = useState("");
  const [indexId, setIndexId] = useState("");
  const [context, setContext] = useState("");
  const [facts, setFacts] = useState<Record<string, string>>({});
  const selected = profiles.find((item) => item.name === profile);
  const fixture = selected?.config.mode === "fixture";
  const readyIndexes = indexes.filter(
    (index) =>
      index.status === "READY" &&
      selected &&
      indexMatchesProfile(index.config, selected.config),
  );
  const submit = useMutation({
    mutationFn: async () => {
      let chosen = videoId;
      if (file) {
        const form = new FormData();
        form.set("file", file);
        chosen = (
          await request<VideoRecord>("/videos", { method: "POST", body: form })
        ).id;
      }
      return createRun({
        video_id: chosen,
        index_id: indexId || null,
        scenario: {
          context: context.trim(),
          facts: Object.fromEntries(
            Object.entries(facts).filter(([, value]) => value.trim()),
          ),
          track_labels: {},
        },
        profile,
      });
    },
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["videos"] });
      void client.invalidateQueries({ queryKey: ["runs"] });
      onCreated(run);
    },
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <div className="workflow-preview">
        <span>
          <b>01</b>영상과 상황
        </span>
        <ChevronRight size={16} />
        <span>
          <b>02</b>얼굴과 근거 확인
        </span>
        <ChevronRight size={16} />
        <span>
          <b>03</b>처리 검토
        </span>
        <ChevronRight size={16} />
        <span>
          <b>04</b>영상과 보고서
        </span>
      </div>
      <div className="two-column">
        <Panel
          title="어떤 영상을 처리하나요?"
          subtitle="촬영 상황과 이용 목적을 자유롭게 설명해 주세요."
          accessory={<span className="step-number">01</span>}
        >
          <label
            className={`upload-area ${file ? "has-file" : ""}`}
            htmlFor="video-file"
          >
            <span className="upload-icon">
              {file ? <Video size={26} /> : <Upload size={26} />}
            </span>
            <strong>{file?.name ?? "영상 파일 선택"}</strong>
            <span>
              {file
                ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 다른 파일을 선택하려면 클릭`
                : "MP4, MOV 등 영상 파일을 업로드하세요."}
            </span>
            <input
              ref={videoInput}
              id="video-file"
              type="file"
              accept=".mp4,.mov,.mkv,.webm,.avi"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setVideoId("");
              }}
            />
          </label>
          {videos.length > 0 && (
            <label className="field">
              또는 업로드한 영상 사용
              <select
                value={videoId}
                onChange={(event) => {
                  setVideoId(event.target.value);
                  setFile(null);
                  if (videoInput.current) videoInput.current.value = "";
                }}
              >
                <option value="">기존 영상 선택</option>
                {videos.map((video) => (
                  <option key={video.id} value={video.id}>
                    {video.filename}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            촬영 상황과 이용 목적 <span className="required">필수</span>
            <textarea
              rows={5}
              maxLength={10000}
              required
              value={context}
              onChange={(event) => setContext(event.target.value)}
              placeholder="누가, 어디서 촬영했고 어떤 목적으로 영상을 사용하려는지 알려주세요. 공개 대상이나 동의 여부를 알고 있다면 함께 적어주세요."
            />
          </label>
          <details className="optional-facts">
            <summary>
              추가 정보 <span>선택</span>
            </summary>
            <div className="fact-fields">
              {["촬영·이용 주체", "이용 목적", "동의 여부", "공개 범위"].map(
                (name) => (
                  <label className="field" key={name}>
                    {name}
                    <input
                      value={facts[name] ?? ""}
                      onChange={(event) =>
                        setFacts({ ...facts, [name]: event.target.value })
                      }
                      placeholder="알고 있는 경우만 입력"
                    />
                  </label>
                ),
              )}
            </div>
          </details>
        </Panel>
        <div className="stack">
          <Panel
            title="이번 실험의 구성"
            subtitle="같은 영상을 다른 구성으로 다시 실행할 수 있습니다."
            accessory={<span className="step-number">02</span>}
          >
            <ProfileSelect
              profiles={profiles}
              value={profile}
              onChange={(value) => {
                setProfile(value);
                setIndexId("");
              }}
            />
            {selected && <ProfileDetails config={selected.config} />}
            {fixture && <FixtureNotice />}
            <label className="field">
              검색할 자료 묶음
              <select
                value={indexId}
                onChange={(event) => setIndexId(event.target.value)}
              >
                <option value="">근거 없이 실행 · 사용자 검토 필요</option>
                {readyIndexes.map((index) => (
                  <option key={index.id} value={index.id}>
                    {profileNames[String(index.config.name)] ??
                      String(index.config.name)}{" "}
                    · {index.chunk_count}개 청크 ·{" "}
                    {formatDate(index.created_at)}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="text-button" onClick={onDocuments}>
              <FolderOpen size={15} />
              근거 자료 추가 및 색인 <ArrowRight size={14} />
            </button>
          </Panel>
          <div className="start-note">
            <Shield size={24} />
            <div>
              <strong>처리 전, 얼굴별 설정을 확인합니다.</strong>
              <p>
                판정 근거가 부족한 경우에는 사용자 지정이 필요합니다. 실험
                출력에는 원래 판정과 변경 내역이 함께 남습니다.
              </p>
            </div>
          </div>
          <ErrorNotice error={submit.error} />
          <button
            type="submit"
            className="button primary large"
            disabled={
              submit.isPending || !context.trim() || (!file && !videoId)
            }
          >
            {submit.isPending ? "업로드 및 실행 시작 중…" : "영상 분석 시작"}
            <ArrowRight size={19} />
          </button>
        </div>
      </div>
    </form>
  );
}

function Documents({
  documents,
  indexes,
  profiles,
  profile,
  setProfile,
  error,
}: {
  documents: DocumentRecord[];
  indexes: IndexRecord[];
  profiles: Profile[];
  profile: string;
  setProfile: (value: string) => void;
  error: unknown;
}) {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const documentInput = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["documents"] });
    void client.invalidateQueries({ queryKey: ["indexes"] });
  };
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.set("file", file!);
      form.set("title", title.trim() || file!.name);
      if (url.trim()) form.set("source_url", url.trim());
      return request<DocumentRecord>("/documents", {
        method: "POST",
        body: form,
      });
    },
    onSuccess: () => {
      refresh();
      setFile(null);
      if (documentInput.current) documentInput.current.value = "";
      setTitle("");
      setUrl("");
      setMessage("자료를 추가했습니다. 아래에서 선택해 색인을 만드세요.");
    },
  });
  const seed = useMutation({
    mutationFn: () => post<{ task_id: string }>("/documents/seed"),
    onSuccess: () => {
      refresh();
      setMessage(
        "공개 자료 수집을 시작했습니다. 완료되면 자료 목록에 나타납니다.",
      );
    },
  });
  const createIndex = useMutation({
    mutationFn: () =>
      post<IndexRecord>("/indexes", { document_ids: selected, profile }),
    onSuccess: () => {
      refresh();
      setMessage(
        "색인을 만들고 있습니다. 상태가 사용 가능으로 바뀌면 새 실험에서 선택하세요.",
      );
    },
  });
  return (
    <>
      <ErrorNotice error={error} />
      {message && (
        <div className="notice info" role="status">
          <FileText size={18} />
          <span>{message}</span>
        </div>
      )}
      <div className="two-column documents-layout">
        <Panel
          title="자료 추가"
          subtitle="PDF 또는 텍스트 자료를 업로드하세요."
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              upload.mutate();
            }}
          >
            <label className="field">
              자료 파일
              <input
                ref={documentInput}
                type="file"
                accept=".pdf,.txt"
                required
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label className="field">
              자료 제목
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="생략하면 파일 이름 사용"
              />
            </label>
            <label className="field">
              원문 출처 URL
              <input
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://…"
              />
            </label>
            <ErrorNotice error={upload.error} />
            <button
              className="button primary"
              disabled={!file || upload.isPending}
            >
              <Upload size={16} />
              {upload.isPending ? "자료 추가 중…" : "자료 추가"}
            </button>
          </form>
          <div className="separator" />
          <h3>공개 자료로 시작하기</h3>
          <p className="muted">
            기본 법령 및 가이드라인 자료를 서버에서 가져옵니다. 원문 버전과
            출처를 확인하세요.
          </p>
          <button
            className="button secondary"
            onClick={() => seed.mutate()}
            disabled={seed.isPending}
          >
            <FolderOpen size={16} />
            {seed.isPending ? "수집 요청 중…" : "공개 근거 자료 가져오기"}
          </button>
          <ErrorNotice error={seed.error} />
        </Panel>
        <Panel
          title="검색할 자료 묶음 만들기"
          subtitle="자료와 구성을 선택하면 새 버전의 색인을 만듭니다."
          accessory={<span className="counter">{selected.length}개 선택</span>}
        >
          {!documents.length ? (
            <Empty>
              추가한 자료가 없습니다. 파일을 업로드하거나 공개 자료를
              가져오세요.
            </Empty>
          ) : (
            <div className="document-list">
              {documents.map((document) => (
                <label
                  className={`document-row ${selected.includes(document.id) ? "selected" : ""}`}
                  key={document.id}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(document.id)}
                    disabled={document.status !== "READY"}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, document.id]
                          : selected.filter((id) => id !== document.id),
                      )
                    }
                  />
                  <Tick checked={selected.includes(document.id)} />
                  <span>
                    <strong>{document.metadata.title}</strong>
                    <small>
                      {document.format.toUpperCase()} ·{" "}
                      {document.metadata.version ??
                        document.metadata.edition ??
                        formatDate(document.created_at)}
                    </small>
                    {document.error && (
                      <small className="danger-text">{document.error}</small>
                    )}
                  </span>
                  <Badge status={document.status} />
                </label>
              ))}
            </div>
          )}
          <ProfileSelect
            profiles={profiles}
            value={profile}
            onChange={setProfile}
            id="index-profile"
          />
          {profiles.find((item) => item.name === profile)?.config.mode ===
            "fixture" && <FixtureNotice />}
          <ErrorNotice error={createIndex.error} />
          <button
            className="button primary"
            disabled={
              !selected.length ||
              createIndex.isPending ||
              documents.some(
                (document) =>
                  selected.includes(document.id) && document.status !== "READY",
              )
            }
            onClick={() => createIndex.mutate()}
          >
            <Layers3 size={17} />
            {createIndex.isPending
              ? "색인 요청 중…"
              : "선택한 자료 색인 만들기"}
          </button>
        </Panel>
      </div>
      <Panel
        title="자료 묶음 기록"
        subtitle="기존 실행은 당시 사용한 자료 버전을 유지합니다."
      >
        {!indexes.length ? (
          <Empty>아직 만든 색인이 없습니다.</Empty>
        ) : (
          <div className="index-grid">
            {indexes.map((index) => (
              <article className="index-card" key={index.id}>
                <div>
                  <Layers3 size={21} />
                  <Badge status={index.status} />
                </div>
                <h3>
                  {profileNames[String(index.config.name)] ??
                    String(index.config.name)}
                </h3>
                {index.config.mode === "fixture" && <FixtureBadge />}
                <p>
                  {index.document_ids.length}개 자료 · {index.chunk_count}개
                  청크
                </p>
                <small>{formatDate(index.created_at)}</small>
                {index.error && (
                  <div className="notice danger">{index.error}</div>
                )}
                <details>
                  <summary>구성 및 식별자</summary>
                  <code>{index.id}</code>
                  <pre>{JSON.stringify(index.config, null, 2)}</pre>
                </details>
              </article>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
