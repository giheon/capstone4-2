import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Plus, Shield, Upload, Video } from "lucide-react";
import { createRun, request } from "./api";
import {
  Badge,
  Empty,
  ErrorNotice,
  formatDate,
  Loading,
  Panel,
} from "./components";
import { InputContext } from "./InputContext";
import { busyStatuses } from "./types";
import type { AppConfig, Run, ScenarioInput, VideoRecord } from "./types";
import RunWorkspace from "./RunWorkspace";

export default function App() {
  const [view, setView] = useState<"workspace" | "history">("workspace");
  const [activeId, setActiveId] = useState<string | null>(null);
  const client = useQueryClient();
  const config = useQuery({
    queryKey: ["app-config"],
    queryFn: () => request<AppConfig>("/app-config"),
    refetchInterval: 15000,
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
  const select = (run: Run) => {
    client.setQueryData(["run", run.id], run);
    setActiveId(run.id);
    setView("workspace");
  };
  const reset = () => {
    setActiveId(null);
    setView("workspace");
  };
  return (
    <InputContext.Provider value={config.data?.inputs ?? null}>
      <div className="app-shell">
        <aside className="sidebar">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              reset();
            }}
          >
            <span className="brand-mark">
              <Shield size={23} />
            </span>
            <span>
              PrivFrame<small>영상 비식별화</small>
            </span>
          </a>
          <nav aria-label="주 메뉴">
            <button
              className={view === "workspace" ? "active" : ""}
              onClick={() => setView("workspace")}
            >
              <Video size={19} />
              영상 처리
            </button>
            <button
              className={view === "history" ? "active" : ""}
              onClick={() => setView("history")}
            >
              <History size={19} />
              작업 내역
              <span className="nav-count">{runs.data?.length ?? 0}</span>
            </button>
          </nav>
          <div className="sidebar-note">
            <p>
              필요한 정보만 남기는
              <br />
              영상 비식별화
            </p>
            <small>
              등장 인물과 처리 방법을 확인한 뒤 보호된 영상을 저장하세요.
            </small>
          </div>
          <div className="connection">
            <span
              className={`status-dot ${config.isSuccess ? "online" : ""}`}
            />
            <span>{config.isSuccess ? "서비스 연결됨" : "서비스 연결 중"}</span>
          </div>
        </aside>
        <div className="main-area">
          <header className="topbar">
            <span>
              {view === "history"
                ? "작업 내역"
                : activeId
                  ? "영상 처리 확인"
                  : "새 영상 처리"}
            </span>
            <button className="button subtle small" onClick={reset}>
              <Plus size={16} />새 영상
            </button>
          </header>
          <main>
            <div className="page-heading">
              <div>
                <span className="eyebrow">PRIVFRAME</span>
                <h1>
                  {view === "history"
                    ? "이전 작업을 확인하세요"
                    : activeId
                      ? "영상을 확인하고 보호하세요"
                      : "영상 속 개인정보를 보호하세요"}
                </h1>
                <p>
                  {view === "history"
                    ? "처리 결과를 확인하고 필요한 경우 다시 수정할 수 있습니다."
                    : "이용 목적에 맞는 처리 제안을 확인하고 영상을 저장하세요."}
                </p>
              </div>
            </div>
            <ErrorNotice
              error={config.error ?? runs.error ?? videos.error ?? active.error}
            />
            {view === "history" ? (
              <Panel title="작업 내역">
                {runs.data?.length ? (
                  <div className="stage-list">
                    {runs.data.map((run) => (
                      <button
                        key={run.id}
                        className="history-row"
                        onClick={() => select(run)}
                      >
                        <Video size={20} />
                        <span className="history-content">
                          <strong>
                            {videos.data?.find((v) => v.id === run.video_id)
                              ?.filename ?? "영상"}
                          </strong>
                          <small>{formatDate(run.created_at)}</small>
                        </span>
                        <Badge status={run.status} />
                      </button>
                    ))}
                  </div>
                ) : (
                  <Empty>아직 처리한 영상이 없습니다.</Empty>
                )}
              </Panel>
            ) : activeId ? (
              active.data ? (
                <RunWorkspace
                  key={active.data.id}
                  run={active.data}
                  video={videos.data?.find(
                    (v) => v.id === active.data.video_id,
                  )}
                  onRun={select}
                />
              ) : (
                <Loading />
              )
            ) : config.data ? (
              <UploadForm
                config={config.data}
                onRun={(run) => {
                  void client.invalidateQueries({ queryKey: ["videos"] });
                  void client.invalidateQueries({ queryKey: ["runs"] });
                  select(run);
                }}
              />
            ) : (
              <Loading />
            )}
          </main>
        </div>
      </div>
    </InputContext.Provider>
  );
}

function UploadForm({
  config,
  onRun,
}: {
  config: AppConfig;
  onRun: (run: Run) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [purpose, setPurpose] = useState("");
  const [audience, setAudience] = useState("");
  const start = useMutation({
    mutationFn: async () => {
      const body = new FormData();
      body.append("file", file!);
      const video = await request<VideoRecord>("/videos", {
        method: "POST",
        body,
      });
      return createRun({
        video_id: video.id,
        scenario: {
          context: "",
          use_purpose: purpose as ScenarioInput["use_purpose"],
          audience_scope: audience as ScenarioInput["audience_scope"],
        },
      });
    },
    onSuccess: onRun,
  });
  return (
    <Panel
      title="영상과 이용 정보를 알려주세요"
      subtitle="이용 목적과 공개 범위에 따라 필요한 보호 방법을 제안합니다."
      accessory={<Shield size={20} className="muted" />}
    >
      {!config.ready && (
        <div className="notice info" role="status">
          <div>
            <strong>서비스 준비 중</strong>
            {config.messages.map((message) => (
              <p key={message}>{message}</p>
            ))}
          </div>
        </div>
      )}
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (file && purpose && audience && config.ready) start.mutate();
        }}
      >
        <label className="upload-area" htmlFor="video-file">
          <Upload size={28} />
          <strong>{file?.name ?? "처리할 영상을 선택하세요"}</strong>
          <span>MP4 · MOV · MKV · WebM · AVI</span>
          <input
            id="video-file"
            type="file"
            accept=".mp4,.mov,.mkv,.webm,.avi"
            required
            disabled={start.isPending}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <div className="fact-fields">
          <label className="field">
            이 영상을 어떤 목적으로 사용하려고 하나요?
            <select
              required
              value={purpose}
              disabled={start.isPending}
              onChange={(e) => setPurpose(e.target.value)}
            >
              <option value="" disabled>
                이용 목적 선택
              </option>
              {config.inputs.use_purpose.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            이 영상을 누가 볼 수 있나요?
            <select
              required
              value={audience}
              disabled={start.isPending}
              onChange={(e) => setAudience(e.target.value)}
            >
              <option value="" disabled>
                공개·제공 범위 선택
              </option>
              {config.inputs.audience_scope.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ErrorNotice error={start.error} />
        <div className="panel-action">
          <p>잘 모르겠다면 ‘아직 정하지 않음·모름’을 선택하세요.</p>
          <button
            className="button primary"
            type="submit"
            disabled={
              !config.ready || !file || !purpose || !audience || start.isPending
            }
          >
            {start.isPending ? "영상 업로드 중…" : "영상 분석 시작"}
          </button>
        </div>
      </form>
    </Panel>
  );
}
