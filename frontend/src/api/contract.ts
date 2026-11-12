// API contract between the PrivFrame web app and its backend.
// M2: served by the mock in /mock (Vite plugin). M4: FastAPI implements the same shapes.
// Times are seconds on the ORIGINAL video timeline unless a field says otherwise.

export type CaseStatus =
  | "QUEUED"
  | "ANALYZING"
  | "AWAITING_IDENTITY"
  | "ANALYZING_LEGAL"
  | "AWAITING_ANSWERS"
  | "PLANNING"
  | "AWAITING_REVIEW"
  | "AUDITING"
  | "AWAITING_AUDIT_CONFIRM"
  | "RENDERING"
  | "COMPLETED"
  | "FAILED"
  /** stopped before finishing; `pauseReason` says why and the case continues from its last checkpoint */
  | "PAUSED";

/** budget = cost cap reached · user = stopped on request · restart = the server restarted mid-run */
export type PauseReason = "budget" | "user" | "restart";

export type PrivacyMode = "anonymized_upload" | "local_only";
export type Provenance = "declared" | "answered" | "inferred";
export type Authority = 1 | 2 | 3;
/**
 * What a decision does to its target. Something on screen is kept or covered with a mosaic: the only redaction,
 * at a fixed level per kind of target (there is deliberately no strength). File metadata is kept or removed.
 */
export type Method = "keep" | "mosaic" | "remove";
export type Shape = "ellipse" | "box" | "outline";
/** `metadata`: information in the file itself (the shooting-location tag), not something in the picture */
export type TargetKind = "person" | "plate" | "text" | "region" | "metadata";
/** the part of a target a requirement or a decision is about */
export type Scope = "face" | "body" | "text" | "plate" | "region" | "metadata";
/** seconds, half-open `[start, end)` and aligned to frames: a target visible to the end of a 10 s video has `[0, 10]` */
export type Range = [number, number];

export interface VideoMeta {
  filename: string;
  sizeBytes: number;
  durationSec: number;
  width: number;
  height: number;
  fps: string;
  codec: string;
  audio: string;
  frameCount: number;
  sample: boolean;
}

export interface UploadInfo {
  filename: string;
  sizeBytes: number;
  durationSec?: number;
  width?: number;
  height?: number;
}

export interface CaseSummary {
  id: string;
  title: string;
  createdAt: string;
  status: CaseStatus;
  /** the video the analysis results describe (M2: always the sample scenario) */
  video: VideoMeta;
  /** what the user actually uploaded, when it differs from the analysed sample */
  upload?: UploadInfo;
  mode: PrivacyMode;
  costUsd: number;
  costCapUsd: number;
  autopilot: boolean;
  forkedFrom?: string;
  /** the graph node a run of only part of the pipeline ended after: COMPLETED, but without output or report */
  stoppedAfter?: string;
  /** the Lab section experiment this case is a run of (`/lab/experiments/{id}`) */
  experimentId?: string;
}

export interface Citation {
  id: string;
  label: string;
  authority: Authority;
  kind: string;
  note?: string;
  /** true when the item stands in for a source that is not in the corpus yet */
  placeholder?: boolean;
}

export interface Fact {
  key: string;
  label: string;
  value: string;
  valueLabel: string;
  provenance: Provenance;
  confidence?: number;
  source: string;
  editable: boolean;
  options?: { value: string; label: string }[];
}

export interface Target {
  id: string;
  kind: TargetKind;
  label: string;
  detail: string;
  /** when the target is on screen; empty for file metadata */
  ranges: Range[];
  cues: string[];
  attachedTo?: string;
  tracks: string[];
  personal: boolean;
}

export interface QuestionOption {
  value: string;
  label: string;
  detail?: string;
}

export interface Question {
  id: string;
  factKey: string;
  round: number;
  prompt: string;
  note?: string;
  options: QuestionOption[];
  basis: string;
  citations: Citation[];
  impact: { option: string; effect: string }[];
  status: "pending" | "answered" | "deferred" | "removed";
  answer?: string;
  removedReason?: string;
  targets: string[];
  frameAt?: number;
}

export interface IdentityQuestion {
  id: string;
  prompt: string;
  detail: string;
  candidates: { label: string; at: number; target: string; note: string }[];
  options: QuestionOption[];
  status: "pending" | "answered";
  answer?: string;
}

export interface Analysis {
  id: string;
  question: string;
  applicability: "applies" | "not_applies" | "uncertain";
  reasoning: string;
  citations: Citation[];
  dependsOn: string[];
  targets: string[];
}

export interface Requirement {
  id: string;
  statement: string;
  targets: string[];
  ranges: Range[];
  derivedFrom: string[];
  citations: Citation[];
  /** which part of each target must be unidentifiable */
  scope: Scope;
}

export interface Decision {
  id: string;
  targetId: string;
  targetLabel: string;
  scope: Scope;
  /** on screen: `keep` or `mosaic` · file metadata: `keep` or `remove` */
  method: Method;
  /** absent for file metadata, which has no place in the picture */
  shape?: Shape;
  /** empty for file metadata, which is not on the timeline */
  ranges: Range[];
  satisfies: string[];
  analysisId?: string;
  rationale: string;
  citations: Citation[];
  similarCases: Citation[];
  riskIfChanged: string;
  risk: "high" | "medium" | "low" | "uncertain";
  source: "planner" | "user" | "auditor";
  edited: boolean;
}

export interface Plan {
  version: number;
  decisions: Decision[];
  unsatisfied: { requirementId: string; reason: string }[];
}

export interface Finding {
  id: string;
  type: "residual_identifier" | "requirement_unmet" | "over_redaction" | "inconsistency";
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  targetId: string;
  targetLabel: string;
  ranges: Range[];
  citations: Citation[];
  recommendation: string;
  status: "open" | "applied" | "rejected";
  rejectReason?: string;
}

export interface VerificationRow {
  check: string;
  original: string;
  output: string;
  verdict: "pass" | "fail" | "info";
  note?: string;
}

export interface ScriptRow {
  start: number;
  end: number;
  /** the scene's label; "파일 전체" for a decision about file metadata (0 → duration) */
  scene: string;
  target: string;
  treatment: string;
  basis: string;
}

export interface Report {
  finishedAt: string;
  elapsedSec: number;
  costUsd: number;
  output: { filename: string; sizeBytes: number; codec: string };
  verification: VerificationRow[];
  script: ScriptRow[];
  history: string[];
  notices: string[];
  citations: Citation[];
}

export interface Activity {
  seq: number;
  t: number;
  text: string;
  tone: "info" | "wait" | "done" | "warn";
}

export interface PerceptionSummary {
  scenes: number;
  people: number;
  plates: number;
  texts: number;
  tracks: number;
}

export interface RenderStep {
  label: string;
  state: "pending" | "running" | "done";
  detail?: string;
}

export interface Segment {
  id: string;
  start: number;
  end: number;
  label: string;
}

/** Real media of a case (real backend only; the M2 mock has none and the screens draw the illustration). */
export interface CaseMedia {
  /** H.264 MP4 of the whole video: upright, short side ≤ 640, same duration as the original */
  proxy: string;
  /** JPEG still: `?t=<seconds>&w=<pixels>` */
  frame: string;
  /** where each decision's mosaic goes (`Geometry`): GET for the current plan, POST `GeometryRequest` for an edited one */
  geometry: string;
  /** true once the analysis proxy exists */
  ready: boolean;
  /** the rendered output (same codec as the original); `?download=true` downloads it */
  output?: string;
  /** printable HTML report */
  reportHtml?: string;
}

/** [x, y, w, h] as fractions (0–1) of the upright frame, already enlarged the way the renderer enlarges it */
export type FrameBox = [number, number, number, number];

/** where one decision's mosaic goes, frame by frame */
export interface DecisionGeometry {
  shape: "ellipse" | "box";
  /** multiplies the mosaic block size (text and plates: 2.5) */
  strength: number;
  /** the ranges the renderer will actually use: a boundary within 0.5 s of where the target appears or disappears is moved onto that moment, and everything is aligned to frames */
  ranges: Range[];
  /** runs of consecutive frames: [first frame index, one box per frame] */
  runs: [number, FrameBox[]][];
}

/**
 * Preview geometry, computed by the server with the renderer's own rules. The client draws exactly this and
 * never interpolates, pads or bridges on its own, so the preview and the output cannot disagree.
 */
export interface Geometry {
  width: number;
  height: number;
  durationSec: number;
  /** start time (s) of every frame of the original: frame i is shown from frames[i] to frames[i + 1], the last one to durationSec */
  frames: number[];
  /** decision id → its geometry. Present for every on-screen decision, kept ones too (the area a mosaic would cover); absent for file metadata. */
  decisions: Record<string, DecisionGeometry>;
  /** mosaic block = the region's short side × strength / mosaicBlocks */
  mosaicBlocks: number;
}

/** what the geometry of a decision depends on */
export type GeometryDecision = Pick<Decision, "id" | "targetId" | "scope" | "method" | "shape" | "ranges">;

/** `POST /api/cases/{id}/geometry`: the geometry of a plan that is not saved yet (the review screen's edits) */
export interface GeometryRequest {
  decisions: GeometryDecision[];
}

/** `GET /api/config`: what kind of backend answers, so the screens can tell a real run from the M2 mock */
export interface BackendConfig {
  backend: "mock" | "real";
  /** the server has an example video for `sample` cases and Lab demo runs */
  sampleAvailable: boolean;
  keys?: { openrouter: boolean; anthropic: boolean; langsmith: boolean };
  running?: number;
}

export interface CaseDetail extends CaseSummary {
  memo: string;
  facts: Fact[];
  perception: PerceptionSummary | null;
  segments: Segment[];
  targets: Target[];
  identity: IdentityQuestion | null;
  questions: Question[];
  analyses: Analysis[];
  requirements: Requirement[];
  plan: Plan | null;
  findings: Finding[];
  /** set while the case waits for the audit confirmation; false once the allowed returns to the review are used up */
  canReturnToReview?: boolean;
  renderSteps: RenderStep[];
  report: Report | null;
  activity: Activity[];
  error?: string;
  media?: CaseMedia;
  pauseReason?: PauseReason;
}

/* ---------- requests ---------- */

/** JSON body of `POST /api/cases` on the M2 mock. The real backend takes `multipart/form-data` (`UploadCaseRequest`). */
export interface CreateCaseRequest {
  filename: string;
  sizeBytes: number;
  durationSec?: number;
  width?: number;
  height?: number;
  memo: string;
  mode: PrivacyMode;
  sample: boolean;
}

/** `POST /api/cases` on the real backend: form fields `file`, `memo`, `mode`, `sample` */
export interface UploadCaseRequest {
  /** the video; omitted when `sample` runs the server's example video */
  file?: File;
  memo: string;
  mode: PrivacyMode;
  sample: boolean;
}

/** `POST /api/cases/{id}/resume`: a higher cap is required after a budget pause */
export interface ResumeRequest {
  costCapUsd?: number;
}

/** value may also be "unknown" (모름) or "later" (나중에) */
export interface AnswersRequest {
  answers: { questionId: string; value: string }[];
}

export interface IdentityRequest {
  questionId: string;
  value: string;
}

export interface FactUpdateRequest {
  key: string;
  value: string;
}

export interface ReviewRequest {
  decisions: Decision[];
}

export interface AuditRequest {
  resolutions: { findingId: string; action: "apply" | "reject"; reason?: string }[];
}

/* ---------- Lab: topology, run events, LLMOps ---------- */

export type NodeKind =
  | "start"
  | "end"
  | "step"
  | "subgraph"
  | "agent"
  | "model"
  | "tool"
  | "human"
  | "validator";

export type Side = "l" | "r" | "t" | "b";
export type GraphView = "main" | "perceive";

export interface GraphNodeSpec {
  id: string;
  view: GraphView;
  kind: NodeKind;
  label: string;
  sublabel?: string;
  parent?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  description: string;
  reads: string[];
  writes: string[];
  model?: string;
  drill?: GraphView;
}

export interface GraphEdgeSpec {
  id: string;
  view: GraphView;
  source: string;
  target: string;
  kind: "flow" | "conditional" | "tool" | "interrupt" | "retry" | "wrap";
  label?: string;
  sourceHandle: Side;
  targetHandle: Side;
  /** for "wrap" edges: the y of the horizontal corridor between rows */
  corridorY?: number;
}

export interface GraphSpec {
  graph: string;
  version: string;
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
}

export type BudgetKey = "tool_calls" | "searches" | "question_rounds" | "retries";

export type RunEventBody =
  | {
      kind: "run.start";
      title: string;
      /** profile display name; a fork shows its override ("baseline-v2 · 분석 Gemini 3.8 Flash") */
      profile: string;
      profileId?: string;
      configHash: string;
      /** role → model label (analyst, planner, review_guide, auditor, report, vlm, vlm_person, inspect) */
      models: Record<string, string>;
      /** every pipeline part this run uses, one per catalog slot */
      components?: RunComponent[];
      costCapUsd?: number;
      forkedFrom?: string;
      checkpoint?: string;
    }
  | { kind: "run.status"; status: CaseStatus }
  | { kind: "node.start"; node: string; task: string }
  | {
      kind: "node.end";
      node: string;
      task: string;
      result: "ok" | "cached" | "error";
      costUsd: number;
      writes: string[];
    }
  | { kind: "edge"; edge: string; dir: "fwd" | "back" }
  | {
      kind: "llm";
      node: string;
      agent: string;
      model: string;
      tokensIn: number;
      /** input tokens served from the provider's prompt cache (billed at ~10%) */
      cachedIn: number;
      tokensOut: number;
      costUsd: number;
      ms: number;
    }
  | { kind: "tool.call"; agent: string; tool: string; call: string; args: string }
  | {
      kind: "tool.result";
      agent: string;
      tool: string;
      call: string;
      ok: boolean;
      summary: string;
    }
  | {
      kind: "message";
      agent: string;
      role: "system" | "user" | "assistant" | "tool";
      text: string;
      call?: string;
      tool?: string;
    }
  | { kind: "state"; channel: string; writer: string; summary: string }
  | {
      kind: "interrupt";
      node: string;
      id: string;
      payload: "identity" | "questions" | "review" | "audit";
      summary: string;
    }
  | { kind: "resume"; node: string; id: string; summary: string; auto: boolean }
  | { kind: "checkpoint"; id: string; step: number; next: string[] }
  | { kind: "budget"; agent: string; key: BudgetKey; used: number; limit: number }
  | { kind: "validator"; node: string; ok: boolean; issues: string[] }
  | { kind: "activity"; text: string; tone: Activity["tone"] }
  | { kind: "run.end"; status: "COMPLETED" | "FAILED" };

export type RunEvent = { seq: number; t: number } & RunEventBody;

export interface RunSummary {
  id: string;
  title: string;
  status: CaseStatus;
  profile: string;
  startedAt: string;
  elapsedSec: number;
  costUsd: number;
  llmCalls: number;
  toolCalls: number;
  questions: number;
  live: boolean;
  autopilot: boolean;
  forkedFrom?: string;
  /** the graph node a fork entered at */
  forkedAt?: string;
  /** the graph node a run of only part of the pipeline ended after */
  stoppedAfter?: string;
  source: "live" | "history";
  scenario: string;
  policyF1?: number;
  profileId?: string;
  experimentId?: string;
  /** what the run has stopped for: a person's answer of that kind, or `paused` */
  waitingFor?: RunWaiting;
  /** a run whose questions and reviews a person answers on the service screen (no automatic answers) */
  interactive?: boolean;
}

export type RunWaiting = "identity" | "questions" | "review" | "audit" | "paused";

export interface StartRunRequest {
  /** profile id; the service default profile when omitted */
  profileId?: string;
}

export interface StrategyResult {
  id: string;
  label: string;
  description: string;
  policyF1: number;
  missRate: number;
  overRate: number;
  questions: number;
  counterfactualChange: number;
  costUsd: number;
  latencySec: number;
  proposed?: boolean;
}

export interface ScenarioScore {
  id: string;
  title: string;
  scores: Record<string, number>;
}

export interface CounterfactualResult {
  id: string;
  label: string;
  expected: "change" | "invariant";
  observed: number;
  passed: boolean;
}

export interface AblationResult {
  id: string;
  label: string;
  variable: string;
  deltaF1: number;
  deltaQuestions: number;
  deltaCost: number;
  note: string;
}

export interface ExperimentReport {
  id: string;
  title: string;
  dataset: string;
  repetitions: number;
  createdAt: string;
  gate: { metric: string; threshold: string; value: string; passed: boolean }[];
  strategies: StrategyResult[];
  scenarios: ScenarioScore[];
  counterfactuals: CounterfactualResult[];
  ablations: AblationResult[];
}

export interface DatasetInfo {
  id: string;
  name: string;
  purpose: string;
  items: number;
  version: string;
  updatedAt: string;
  owner: string;
  status: "ready" | "draft" | "planned";
  fields: string[];
}

export interface EvaluatorInfo {
  id: string;
  name: string;
  type: "deterministic" | "trajectory" | "llm_panel" | "human";
  target: string;
  metric: string;
  threshold?: string;
  lastValue?: string;
  description: string;
}

/* ---------- Lab: pipeline catalog and profiles (configs/pipeline) ---------- */

/** api = data leaves the machine · local = runs here · builtin = code path · option = a setting */
export type AdapterKind = "api" | "local" | "builtin" | "option";
export type Capability = "tools" | "json" | "image" | "video";
export type ModelTier = "frontier" | "efficient";

export interface CatalogAdapter {
  id: string;
  label: string;
  kind: AdapterKind;
  /** what `ref` means for this adapter */
  ref: string;
  example?: string;
  lookup?: "openrouter";
  /** false for code paths and settings: new ones need code, not a catalog entry */
  addable: boolean;
}

export interface CatalogCandidate {
  id: string;
  label: string;
  short?: string;
  adapter: string;
  ref?: string;
  license?: string;
  /** USD per 1M tokens */
  price?: { in: number; out: number };
  caps?: Capability[];
  tier?: ModelTier;
  lawirko?: number;
  status?: "planned";
  note?: string;
  source: "file" | "lab";
  addedAt?: string;
}

export interface CatalogSlot {
  id: string;
  stage: string;
  label: string;
  usedBy: string[];
  adapters: string[];
  requires: Capability[];
  default: string;
  candidates: CatalogCandidate[];
}

export interface CatalogStage {
  id: string;
  label: string;
  description: string;
  slots: CatalogSlot[];
}

export interface CatalogParam {
  id: string;
  stage: string;
  label: string;
  unit: string;
  default: number;
  min: number;
  max: number;
  step: number;
}

export interface Catalog {
  /** repository-relative path of the catalog file */
  path: string;
  modifiedAt: string;
  adapters: CatalogAdapter[];
  stages: CatalogStage[];
  params: CatalogParam[];
}

export interface AddCandidateRequest {
  slot: string;
  adapter: string;
  ref: string;
  label: string;
  license?: string;
  price?: { in: number; out: number };
  caps?: Capability[];
  tier?: ModelTier;
  note?: string;
}

/** model facts read from OpenRouter's public model list */
export interface CandidateLookup {
  ref: string;
  label: string;
  price: { in: number; out: number };
  caps: Capability[];
  tier: ModelTier;
  contextLength?: number;
}

export interface RunComponent {
  slot: string;
  stage: string;
  slotLabel: string;
  candidate: string;
  label: string;
  short: string;
  kind: AdapterKind;
  tier?: ModelTier;
  usedBy: string[];
}

export interface ProfileSpec {
  id: string;
  name: string;
  description: string;
  base?: string;
  /** slot id → candidate id */
  selections: Record<string, string>;
  /** param id → value */
  params: Record<string, number>;
}

export interface ProfileIssue {
  level: "error" | "warn" | "info";
  slot?: string;
  text: string;
}

export interface ProfileEstimate {
  /** simulated cost of the sample case (72 s) */
  costUsd: number;
  /** some video frame or crop is sent to an external API */
  framesLeaveDevice: boolean;
  apiParts: number;
  localParts: number;
}

export type ServiceTarget = "default" | "local_only";

export interface ProfileCheck {
  configHash: string;
  issues: ProfileIssue[];
  estimate: ProfileEstimate;
  components: RunComponent[];
}

export interface ProfileSummary extends ProfileSpec, ProfileCheck {
  path: string;
  source: "file" | "lab";
  updatedAt: string;
  /** where the service uses this profile */
  service: ServiceTarget[];
  gate: "passed" | "failed" | "not_run";
  lastExperiment?: { id: string; createdAt: string; policyF1: number; costUsd: number; passed: boolean };
}

/* ---------- Lab: experiments computed from runs ---------- */

export interface GateRow {
  metric: string;
  threshold: string;
  value: string;
  passed: boolean;
}

export interface ProfileMetrics {
  profileId: string;
  name: string;
  configHash: string;
  runs: number;
  policyF1: number;
  missRate: number;
  overRate: number;
  rangeErrors: number;
  requirementRecall: number;
  requirementPrecision: number;
  citationValidity: number;
  questions: number;
  toolCalls: number;
  coverageWarnings: number;
  costUsd: number;
  latencySec: number;
  gate: GateRow[];
  passed: boolean;
}

export interface DecisionDiff {
  profileId: string;
  target: string;
  expected: "모자이크" | "유지";
  actual: "모자이크" | "유지";
  kind: "omission" | "over" | "range";
  cause: string;
}

export interface ExperimentSummary {
  id: string;
  title: string;
  kind: "computed" | "example";
  createdAt: string;
  dataset: string;
  profiles: string[];
  runs: number;
  passed?: Record<string, boolean>;
}

export interface ComputedExperiment {
  kind: "computed";
  id: string;
  title: string;
  createdAt: string;
  dataset: string;
  scenarios: { id: string; title: string; executed: boolean }[];
  repetitions: number;
  baseline: string;
  metrics: ProfileMetrics[];
  diffs: DecisionDiff[];
  runs: RunSummary[];
  /** how the M2 mock turns profile choices into behaviour, shown next to the numbers */
  rules: string[];
}

export type ExperimentDetail = ComputedExperiment | ({ kind: "example" } & ExperimentReport);

export interface StartExperimentRequest {
  profiles: string[];
  baseline: string;
  dataset: string;
  repetitions: number;
  title?: string;
}

export interface LabSettings {
  speed: number;
}
