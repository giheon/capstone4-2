import type { ReactNode } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import type { Authority, Citation, Provenance } from "../api/contract";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function BrandMark({ size = 20 }: { size?: number }) {
  // a frame whose lower-right corner is covered: the product in one glyph
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <rect x="1.5" y="1.5" width="17" height="17" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <rect x="9" y="9" width="8.5" height="8.5" rx="1" fill="currentColor" />
    </svg>
  );
}

export function Spinner({ size = 14, label }: { size?: number; label?: string }) {
  return (
    <span className="spinner" role={label ? "status" : undefined} aria-label={label}>
      <Loader2 size={size} aria-hidden="true" />
    </span>
  );
}

const AUTHORITY_LABEL: Record<Authority, string> = { 1: "규범", 2: "해석", 3: "참고" };

function AuthorityTag({ level }: { level: Authority }) {
  return (
    <span className={`tag authority-${level}`} title={`권위 등급 ${level}`}>
      {AUTHORITY_LABEL[level]}
    </span>
  );
}

function CitationLine({ citation }: { citation: Citation }) {
  return (
    <li className={cx("citation", citation.placeholder && "is-placeholder")}>
      <AuthorityTag level={citation.authority} />
      <span className="citation-label">{citation.label}</span>
      {citation.note ? <span className="citation-note">{citation.note}</span> : null}
    </li>
  );
}

export function CitationList({ items, empty }: { items: Citation[]; empty?: string }) {
  if (items.length === 0) return empty ? <p className="muted small">{empty}</p> : null;
  return (
    <ul className="citations">
      {items.map((c) => (
        <CitationLine key={c.id} citation={c} />
      ))}
    </ul>
  );
}

const PROVENANCE_LABEL: Record<Provenance, string> = { declared: "선언", answered: "답변", inferred: "추론" };

export function ProvenanceTag({ provenance, confidence, source }: { provenance: Provenance; confidence?: number; source?: string }) {
  return (
    <span className={`tag provenance-${provenance}`}>
      {PROVENANCE_LABEL[provenance]}
      {confidence !== undefined ? <span className="tag-sub num">{confidence.toFixed(2)}</span> : null}
      {source ? <span className="tag-sub">· {source}</span> : null}
    </span>
  );
}

export function SeverityTag({ severity }: { severity: "high" | "medium" | "low" }) {
  const label = { high: "심각", medium: "보통", low: "낮음" }[severity];
  return <span className={`tag severity-${severity}`}>{label}</span>;
}

export function RiskTag({ risk }: { risk: "high" | "medium" | "low" | "uncertain" }) {
  const label = { high: "위험 높음", medium: "위험 보통", low: "위험 낮음", uncertain: "불확실" }[risk];
  return <span className={`tag risk-${risk}`}>{label}</span>;
}

export function Meter({ value, max, label, tone }: { value: number; max: number; label?: string; tone?: "accent" | "warn" | "err" }) {
  const ratio = max > 0 ? Math.min(1, value / max) : 0;
  const auto = ratio >= 1 ? "err" : ratio >= 0.8 ? "warn" : "accent";
  return (
    <span className={`meter tone-${tone ?? auto}`} role="meter" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} aria-label={label}>
      <span className="meter-fill" style={{ width: `${ratio * 100}%` }} />
    </span>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.";
  return (
    <div className="error-state" role="alert">
      <AlertTriangle size={16} aria-hidden="true" />
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="btn btn-sm btn-secondary" onClick={onRetry}>
          다시 시도
        </button>
      ) : null}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton-line" style={{ width: `${92 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function MockBadge({ title = "M2 모의 백엔드로 동작합니다. 분석 결과는 예시 시나리오입니다." }: { title?: string }) {
  return (
    <span className="mock-badge" title={title}>
      모의 데이터
    </span>
  );
}
