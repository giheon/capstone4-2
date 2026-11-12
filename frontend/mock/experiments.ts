// Experiments over profiles: every profile × repetition runs the executable scenario on a virtual clock, then
// the runs are scored against that scenario's gold plan with the evaluators M5 will run in LangSmith.
import type {
  ComputedExperiment,
  DecisionDiff,
  ExperimentDetail,
  ExperimentSummary,
  GateRow,
  ProfileMetrics,
  StartExperimentRequest,
} from "../src/api/contract";
import type { CaseRun, Hub } from "./engine";
import { MOCK_RULES } from "./flow";
import { HttpError } from "./http";
import { EXPERIMENT, SCENARIO_TITLES } from "./lab-data";
import { FRAME_SLOTS, type PipelineStore, type ResolvedProfile } from "./pipeline";

export const DEMO_MEMO = "유튜브에 올릴 카페 인터뷰 영상입니다. 인터뷰 대상(안경 쓴 분)은 촬영에 동의했습니다.";
export const DATASET_ID = "scenarios-v3";

/** gold plan of S01 given the dataset's facts (public upload, interviewee consented to publication) */
const GOLD = {
  redact: ["person-3", "text-1", "plate-1", "person-3-tattoo"],
  keep: ["person-2", "text-2", "text-3"],
  requirements: ["REQ-1", "REQ-2", "REQ-3"],
  plate: [40.2, 48.5] as const,
};
const TARGET_LABEL: Record<string, string> = {
  "person-3": "person-3 직원 얼굴",
  "text-1": "명찰 \"김○○ 바리스타\"",
  "plate-1": "창밖 차량 번호판",
  "person-3-tattoo": "person-3 왼팔 문신",
  "person-2": "person-2 인터뷰 대상 얼굴",
  "text-2": "간판 \"○○커피\"",
  "text-3": "메뉴판",
};

interface RunScore {
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
  diffs: Omit<DecisionDiff, "profileId">[];
}

interface Stored {
  detail: Omit<ComputedExperiment, "runs">;
  runIds: string[];
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const pct = (v: number) => `${(v * 100).toFixed(v > 0 && v < 0.1 ? 1 : 0)}%`;

function scoreRun(run: CaseRun): RunScore {
  const plan = run.detail.plan;
  const redacted = new Set((plan?.decisions ?? []).filter((d) => d.method !== "keep").map((d) => d.targetId));
  const tp = GOLD.redact.filter((t) => redacted.has(t)).length;
  const fn = GOLD.redact.length - tp;
  const fp = GOLD.keep.filter((t) => redacted.has(t)).length;
  const plate = plan?.decisions.find((d) => d.targetId === "plate-1" && d.method !== "keep");
  const rangeErrors = plate && !plate.ranges.some(([s, e]) => s <= GOLD.plate[0] + 0.05 && e >= GOLD.plate[1] - 0.05) ? 1 : 0;
  // the gold plan is about what is on screen; the requirement about the file's location tag is not part of it
  const reqs = new Set(run.detail.requirements.filter((r) => r.scope !== "metadata").map((r) => r.id));
  const hit = GOLD.requirements.filter((r) => reqs.has(r)).length;
  const last = run.events[run.events.length - 1];
  const diffs: Omit<DecisionDiff, "profileId">[] = [];
  const cause = (target: string, fallback: string) => run.causes.get(target) ?? fallback;
  for (const t of GOLD.redact) {
    if (!redacted.has(t)) diffs.push({ target: TARGET_LABEL[t] ?? t, expected: "모자이크", actual: "유지", kind: "omission", cause: cause(t, "원인을 기록하지 못함") });
  }
  for (const t of GOLD.keep) {
    if (redacted.has(t)) diffs.push({ target: TARGET_LABEL[t] ?? t, expected: "유지", actual: "모자이크", kind: "over", cause: cause(t, "원인을 기록하지 못함") });
  }
  if (rangeErrors) {
    diffs.push({ target: TARGET_LABEL["plate-1"], expected: "모자이크", actual: "모자이크", kind: "range", cause: cause("plate-1", "번호판 진입 0.8초가 결정 구간 밖에 남음") });
  }
  return {
    policyF1: tp + fp + fn === 0 ? 1 : (2 * tp) / (2 * tp + fp + fn),
    missRate: fn / GOLD.redact.length,
    overRate: fp / GOLD.keep.length,
    rangeErrors,
    requirementRecall: hit / GOLD.requirements.length,
    requirementPrecision: reqs.size ? hit / reqs.size : 1,
    citationValidity: run.trace.citations ? 1 - run.trace.unresolvedCitations / run.trace.citations : 1,
    questions: run.detail.questions.filter((q) => q.status === "answered" || q.status === "deferred").length,
    toolCalls: run.trace.analystTools,
    coverageWarnings: run.trace.coverageWarnings,
    costUsd: run.detail.costUsd,
    latencySec: Math.max(0, ((last?.t ?? 0) - run.trace.humanWaitMs) / 1000),
    diffs,
  };
}

function gateRows(m: { policyF1: number; missRate: number; citationValidity: number; costUsd: number }): GateRow[] {
  return [
    { metric: "정책 F1", threshold: "≥ 0.85", value: m.policyF1.toFixed(2), passed: m.policyF1 >= 0.85 },
    { metric: "누락률 (미처리 개인정보)", threshold: "≤ 3%", value: pct(m.missRate), passed: m.missRate <= 0.03 },
    { metric: "인용 유효율", threshold: "100%", value: pct(m.citationValidity), passed: m.citationValidity >= 0.9999 },
    { metric: "케이스 비용", threshold: "≤ $0.30", value: `$${m.costUsd.toFixed(3)}`, passed: m.costUsd <= 0.3 },
  ];
}

function framesLocal(profile: ResolvedProfile): boolean {
  return FRAME_SLOTS.every((s) => profile.part[s]?.kind !== "api");
}

export class ExperimentStore {
  private readonly records: Stored[] = [];
  private counter = 0;

  constructor(
    private readonly hub: Hub,
    private readonly store: PipelineStore,
  ) {}

  async start(req: StartExperimentRequest): Promise<ComputedExperiment> {
    const ids = [...new Set((req.profiles ?? []).map(String))];
    if (ids.length === 0 || ids.length > 4) throw new HttpError(400, "프로필을 1~4개 고르세요.");
    if (req.dataset !== DATASET_ID) throw new HttpError(400, `데이터셋 '${req.dataset}'은(는) 아직 실행할 수 없습니다.`);
    const repetitions = Math.min(3, Math.max(1, Math.round(Number(req.repetitions) || 1)));
    const baseline = ids.includes(req.baseline) ? req.baseline : ids[0];
    const profiles = ids.map((id) => {
      const stored = this.store.getProfile(id);
      const errors = this.store.issues(stored.spec).filter((i) => i.level === "error");
      if (errors.length) throw new HttpError(400, `프로필 '${id}'에 오류가 있습니다: ${errors[0].text}`);
      return this.store.resolve(stored.spec);
    });
    this.counter += 1;
    const stamp = new Date();
    const id = `exp-${String(stamp.getMonth() + 1).padStart(2, "0")}${String(stamp.getDate()).padStart(2, "0")}-${String(this.counter).padStart(2, "0")}`;
    const runs: { profile: ResolvedProfile; run: CaseRun }[] = [];
    for (const [pi, profile] of profiles.entries()) {
      for (let rep = 1; rep <= repetitions; rep += 1) {
        const run = this.hub.create({
          title: `S01 카페 인터뷰 · ${profile.name} · 반복 ${rep}`,
          memo: DEMO_MEMO,
          mode: framesLocal(profile) ? "local_only" : "anonymized_upload",
          autopilot: true,
          virtual: true,
          idPrefix: "exp",
          profile,
          experimentId: id,
          scenario: "S01",
          seed: (this.counter * 97 + pi * 13 + rep) * 2654435761,
        });
        runs.push({ profile, run });
      }
    }
    await Promise.all(runs.map((r) => r.run.done));
    const metrics: ProfileMetrics[] = [];
    const diffs: DecisionDiff[] = [];
    for (const profile of profiles) {
      const mine = runs.filter((r) => r.profile === profile).map((r) => r.run);
      const scores = mine.map(scoreRun);
      const avg = (key: keyof Omit<RunScore, "diffs">) => mean(scores.map((s) => s[key]));
      const m = {
        policyF1: avg("policyF1"),
        missRate: avg("missRate"),
        citationValidity: avg("citationValidity"),
        costUsd: avg("costUsd"),
      };
      const gate = gateRows(m);
      metrics.push({
        profileId: profile.id,
        name: profile.name,
        configHash: profile.configHash,
        runs: mine.length,
        ...m,
        overRate: avg("overRate"),
        rangeErrors: avg("rangeErrors"),
        requirementRecall: avg("requirementRecall"),
        requirementPrecision: avg("requirementPrecision"),
        questions: avg("questions"),
        toolCalls: avg("toolCalls"),
        coverageWarnings: avg("coverageWarnings"),
        latencySec: avg("latencySec"),
        gate,
        passed: gate.every((g) => g.passed),
      });
      // decisions are deterministic across repetitions, so the first run explains them
      for (const d of scores[0]?.diffs ?? []) diffs.push({ profileId: profile.id, ...d });
    }
    const record: Stored = {
      detail: {
        kind: "computed",
        id,
        title: req.title?.trim() || `${profiles.map((p) => p.name).join(" vs ")}`,
        createdAt: stamp.toISOString(),
        dataset: `${DATASET_ID} · 시나리오 13개 중 S01 실행`,
        scenarios: SCENARIO_TITLES.map((title, i) => ({ id: `S${String(i + 1).padStart(2, "0")}`, title, executed: i === 0 })),
        repetitions,
        baseline,
        metrics,
        diffs,
        rules: MOCK_RULES,
      },
      runIds: runs.map((r) => r.run.id),
    };
    this.records.unshift(record);
    return this.detail(record);
  }

  private detail(record: Stored): ComputedExperiment {
    const runs = record.runIds.map((id) => this.hub.runs.get(id)).filter((r): r is CaseRun => !!r).map((r) => ({ ...r.summary(), policyF1: scoreRun(r).policyF1 }));
    return { ...record.detail, runs };
  }

  list(): ExperimentSummary[] {
    const computed: ExperimentSummary[] = this.records.map((r) => ({
      id: r.detail.id,
      title: r.detail.title,
      kind: "computed",
      createdAt: r.detail.createdAt,
      dataset: r.detail.dataset,
      profiles: r.detail.metrics.map((m) => m.profileId),
      runs: r.runIds.length,
      passed: Object.fromEntries(r.detail.metrics.map((m) => [m.profileId, m.passed])),
    }));
    return [
      ...computed,
      { id: EXPERIMENT.id, title: EXPERIMENT.title, kind: "example", createdAt: EXPERIMENT.createdAt, dataset: EXPERIMENT.dataset, profiles: [], runs: 0 },
    ];
  }

  get(id: string): ExperimentDetail {
    if (id === EXPERIMENT.id) return { kind: "example", ...EXPERIMENT };
    const record = this.records.find((r) => r.detail.id === id);
    if (!record) throw new HttpError(404, "실험을 찾을 수 없습니다.");
    return this.detail(record);
  }

  /** latest metrics for this exact configuration (an edited profile has a new hash and needs a new experiment) */
  latest(profileId: string, configHash: string): { id: string; createdAt: string; metrics: ProfileMetrics } | undefined {
    for (const r of this.records) {
      const m = r.detail.metrics.find((x) => x.profileId === profileId && x.configHash === configHash);
      if (m) return { id: r.detail.id, createdAt: r.detail.createdAt, metrics: m };
    }
    return undefined;
  }
}
