import type { ReactNode } from "react";
import { AlertCircle, Check, FlaskConical, LoaderCircle } from "lucide-react";
import type { Profile } from "./types";
import { profileNames, statusNames } from "./types";

export function Panel({
  title,
  subtitle,
  accessory,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  accessory?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {accessory}
      </div>
      {children}
    </section>
  );
}
export function Badge({ status = "QUEUED" }: { status?: string }) {
  const tone =
    status === "COMPLETED" || status === "READY"
      ? "success"
      : status === "FAILED"
        ? "danger"
        : status === "REVIEW_REQUIRED"
          ? "warning"
          : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <span className="status-dot" />
      {statusNames[status] ??
        (
          {
            UPLOADED: "업로드됨",
            INDEXING: "색인 중",
            READY: "사용 가능",
            EXTRACTED: "추출 완료",
          } as Record<string, string>
        )[status] ??
        status}
    </span>
  );
}
export function FixtureBadge() {
  return (
    <span className="badge fixture">
      <FlaskConical size={13} />
      테스트 픽스처
    </span>
  );
}
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="notice danger" role="alert">
      <AlertCircle size={17} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}
export function Loading({ text = "불러오는 중" }: { text?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={18} className="spin" />
      {text}
    </div>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function ProfileSelect({
  profiles,
  value,
  onChange,
  id = "profile",
  disabled,
}: {
  profiles: Profile[];
  value: string;
  onChange: (value: string) => void;
  id?: string;
  disabled?: boolean;
}) {
  return (
    <label className="field" htmlFor={id}>
      실험 구성
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        {profiles.length ? (
          profiles.map((profile) => (
            <option key={profile.name} value={profile.name}>
              {profileNames[profile.name] ?? profile.name}
              {profile.config.mode === "fixture" ? " · 테스트 전용" : ""}
            </option>
          ))
        ) : (
          <option value="baseline">기본 실험</option>
        )}
      </select>
    </label>
  );
}
export function ProfileDetails({
  config,
}: {
  config: Record<string, unknown>;
}) {
  return (
    <div className="config-summary">
      <span>
        얼굴 검출 <b>{String(config.detector ?? "—")}</b>
      </span>
      <span>
        청크 크기 <b>{String(config.chunk_size ?? "—")}</b>
      </span>
      <span>
        판정 모델 <b>{String(config.llm_model ?? "—")}</b>
      </span>
    </div>
  );
}
export function FixtureNotice() {
  return (
    <div className="notice warning">
      <FlaskConical size={18} />
      <span>
        <strong>테스트 픽스처 · 실제 모델 결과가 아닙니다.</strong>
        <br />
        화면과 처리 흐름 확인에 사용하는 테스트 구성입니다.
      </span>
    </div>
  );
}
export function formatDate(value?: string) {
  if (!value) return "날짜 미확인";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}
export function seconds(value: number) {
  return `${Math.floor(value / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(value % 60)
    .toString()
    .padStart(2, "0")}`;
}
export function Tick({ checked }: { checked: boolean }) {
  return (
    <span className={`tick ${checked ? "checked" : ""}`}>
      {checked && <Check size={13} />}
    </span>
  );
}
