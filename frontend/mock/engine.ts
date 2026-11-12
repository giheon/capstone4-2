// Mock run engine: one CaseRun per case thread. It emits the normalized run events the Lab draws
// and keeps the CaseDetail snapshot the service screens read. M4 replaces this with LangGraph stream events.
import type {
  BudgetKey,
  CaseDetail,
  CaseStatus,
  LabSettings,
  PrivacyMode,
  RunEvent,
  RunEventBody,
  RunSummary,
  UploadInfo,
} from "../src/api/contract";
import { WAITING_FOR } from "../src/lib/format";
import { runCase } from "./flow";
import type { ResolvedPart, ResolvedProfile } from "./pipeline";
import { type Answers, SAMPLE_VIDEO } from "./scenario";

type Listener = (event: RunEvent) => void;

interface RunOptions {
  title: string;
  memo: string;
  mode: PrivacyMode;
  upload?: UploadInfo;
  autopilot: boolean;
  /** compute instantly on a virtual clock (used for the pre-recorded demo run) */
  virtual?: boolean;
  /** the run whose checkpoint this one continued from (section experiments) */
  forkedFrom?: string;
  idPrefix?: string;
  /** the pipeline parts this run uses (models, retrieval, render) */
  profile: ResolvedProfile;
  experimentId?: string;
  scenario?: string;
  /** varies latency and token counts a little between repetitions */
  seed?: number;
  /** false for cost simulations that should not appear in the run list */
  register?: boolean;
}

/** model roles and the catalog slot that fills each */
const ROLE_SLOTS = {
  analyst: "llm.analyst",
  planner: "llm.planner",
  review_guide: "llm.review_guide",
  auditor: "llm.auditor",
  report: "llm.report",
  vlm: "vlm.scene",
  vlm_person: "vlm.person",
  inspect: "vlm.inspect",
  ocr: "perception.ocr",
} as const;
type Role = keyof typeof ROLE_SLOTS;

/** deterministic 0..1 sequence so repeated experiment runs differ the same way every time */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export class Hub {
  readonly runs = new Map<string, CaseRun>();
  readonly settings: LabSettings = { speed: 1 };
  private counter = 0;

  nextId(prefix = "case"): string {
    this.counter += 1;
    const stamp = Date.now().toString(36).slice(-4);
    return `${prefix}_${stamp}${this.counter}`;
  }

  create(opts: RunOptions): CaseRun {
    const run = new CaseRun(this, this.nextId(opts.idPrefix ?? "case"), opts);
    if (opts.register !== false) this.runs.set(run.id, run);
    run.start();
    return run;
  }

  ordered(): CaseRun[] {
    return [...this.runs.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
}

export class CaseRun {
  readonly events: RunEvent[] = [];
  readonly listeners = new Set<Listener>();
  readonly detail: CaseDetail;
  readonly createdAt = new Date().toISOString();
  readonly profile: ResolvedProfile;
  /** resolves when the scripted run has finished (or failed) */
  done: Promise<void> = Promise.resolve();
  edits: string[] = [];
  answers?: Answers;
  /** why a target ended up differently than a careful run would treat it (read by experiments) */
  readonly causes = new Map<string, string>();
  /** counters the experiment evaluators read */
  readonly trace = { analystTools: 0, searches: 0, coverageWarnings: 0, dedupeBlocks: 0, unresolvedCitations: 0, citations: 0, humanWaitMs: 0 };
  private readonly startWall = Date.now();
  private vnow = 0;
  private taskSeq = 0;
  private callSeq = 0;
  private stepSeq = 0;
  private readonly waiters = new Map<string, (value: { value: unknown; auto: boolean }) => void>();
  private stats = { llm: 0, tools: 0, questions: 0 };
  private readonly turns = new Map<string, number>();
  private readonly random: () => number;
  private capWarned = false;

  constructor(
    readonly hub: Hub,
    readonly id: string,
    readonly opts: RunOptions,
  ) {
    this.profile = opts.profile;
    this.random = mulberry32(opts.seed ?? 0);
    this.detail = {
      id,
      title: opts.title,
      createdAt: this.createdAt,
      status: "QUEUED",
      video: SAMPLE_VIDEO,
      upload: opts.upload,
      mode: opts.mode,
      costUsd: 0,
      costCapUsd: opts.profile.params["case.cost_cap_usd"] ?? 0.3,
      autopilot: opts.autopilot,
      forkedFrom: opts.forkedFrom,
      memo: opts.memo,
      facts: [],
      perception: null,
      segments: [],
      targets: [],
      identity: null,
      questions: [],
      analyses: [],
      requirements: [],
      plan: null,
      findings: [],
      renderSteps: [],
      report: null,
      activity: [],
    };
  }

  get virtual(): boolean {
    return !!this.opts.virtual;
  }

  /** role → model label, as written into run.start */
  get models(): Record<string, string> {
    return Object.fromEntries(Object.entries(ROLE_SLOTS).map(([role, slot]) => [role, this.profile.part[slot]?.label ?? "—"]));
  }

  part(slot: string): ResolvedPart {
    return this.profile.part[slot];
  }

  /** ±spread around 1, seeded; 0 spread when no seed is given */
  jitter(spread: number): number {
    if (!this.opts.seed) return 1;
    return 1 + (this.random() * 2 - 1) * spread;
  }

  start(): void {
    this.done = runCase(this).catch((error: unknown) => {
      this.detail.error = error instanceof Error ? error.message : String(error);
      this.setStatus("FAILED");
      this.emit({ kind: "activity", text: "처리 중 오류가 발생했습니다.", tone: "warn" });
      this.emit({ kind: "run.end", status: "FAILED" });
    });
  }

  now(): number {
    return this.virtual ? this.vnow : Date.now() - this.startWall;
  }

  emit(body: RunEventBody): RunEvent {
    const event = { seq: this.events.length + 1, t: Math.round(this.now()), ...body } as RunEvent;
    this.events.push(event);
    if (body.kind === "activity") {
      this.detail.activity.push({ seq: event.seq, t: event.t, text: body.text, tone: body.tone });
    }
    if (body.kind === "llm") this.stats.llm += 1;
    if (body.kind === "tool.call") this.stats.tools += 1;
    for (const listener of this.listeners) listener(event);
    return event;
  }

  async wait(baseMs: number): Promise<void> {
    const ms = baseMs * this.jitter(0.08);
    if (this.virtual) {
      this.vnow += ms;
      await Promise.resolve();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, ms / Math.max(0.25, this.hub.settings.speed)));
  }

  /** run branches that LangGraph would execute in the same super-step */
  async parallel(branches: (() => Promise<void>)[]): Promise<void> {
    if (!this.virtual) {
      await Promise.all(branches.map((branch) => branch()));
      return;
    }
    const start = this.vnow;
    let end = start;
    for (const branch of branches) {
      this.vnow = start;
      await branch();
      end = Math.max(end, this.vnow);
    }
    this.vnow = end;
  }

  setStatus(status: CaseStatus): void {
    this.detail.status = status;
    this.emit({ kind: "run.status", status });
  }

  activity(text: string, tone: "info" | "wait" | "done" | "warn" = "info"): void {
    this.emit({ kind: "activity", text, tone });
  }

  state(channel: string, writer: string, summary: string): void {
    this.emit({ kind: "state", channel, writer, summary });
  }

  edge(source: string, target: string, dir: "fwd" | "back" = "fwd"): void {
    this.emit({ kind: "edge", edge: `${source}->${target}`, dir });
  }

  checkpoint(next: string[]): void {
    this.stepSeq += 1;
    this.emit({ kind: "checkpoint", id: `ckpt-${String(this.stepSeq).padStart(2, "0")}`, step: this.stepSeq, next });
  }

  budget(agent: string, key: BudgetKey, used: number, limit: number): void {
    this.emit({ kind: "budget", agent, key, used, limit });
  }

  message(agent: string, role: "system" | "user" | "assistant" | "tool", text: string, tool?: string): void {
    this.emit({ kind: "message", agent, role, text, tool });
  }

  llm(node: string, agent: string, role: Role, baseIn: number, baseOut: number, ms: number, baseCached = 0): number {
    const part = this.part(ROLE_SLOTS[role]);
    const scale = this.jitter(0.05);
    const tokensIn = Math.round(baseIn * scale);
    const cachedIn = Math.round(baseCached * scale);
    const tokensOut = Math.round(baseOut * this.jitter(0.1));
    const price = part?.price ?? { in: 0, out: 0 };
    const cost = ((tokensIn - cachedIn) * price.in + cachedIn * price.in * 0.1 + tokensOut * price.out) / 1_000_000;
    this.detail.costUsd = Number((this.detail.costUsd + cost).toFixed(5));
    this.emit({ kind: "llm", node, agent, model: part?.label ?? role, tokensIn, cachedIn, tokensOut, costUsd: cost, ms });
    if (!this.capWarned && this.detail.costUsd > this.detail.costCapUsd) {
      this.capWarned = true;
      this.emit({ kind: "activity", text: `비용이 프로필 상한 $${this.detail.costCapUsd.toFixed(2)}을 넘었습니다. 실제 실행에서는 여기서 멈추고 재개를 기다립니다.`, tone: "warn" });
    }
    return cost;
  }

  async node(
    id: string,
    ms: number,
    options: { body?: () => Promise<void> | void; writes?: string[]; cached?: boolean; cost?: () => number } = {},
  ): Promise<void> {
    const task = `${id}#${++this.taskSeq}`;
    const before = this.detail.costUsd;
    this.emit({ kind: "node.start", node: id, task });
    if (options.body) await options.body();
    await this.wait(options.cached ? Math.min(ms, 120) : ms);
    this.emit({
      kind: "node.end",
      node: id,
      task,
      result: options.cached ? "cached" : "ok",
      costUsd: Number((this.detail.costUsd - before).toFixed(5)),
      writes: options.writes ?? [],
    });
  }

  /** one LLM turn of an agent's model node */
  async think(agent: string, text: string, tokensIn: number, tokensOut: number, ms: number): Promise<void> {
    const node = `${agent}/model`;
    const role: Role = agent === "auditor" ? "auditor" : "analyst";
    const task = `${node}#${++this.taskSeq}`;
    const turn = (this.turns.get(agent) ?? 0) + 1;
    this.turns.set(agent, turn);
    this.emit({ kind: "node.start", node, task });
    await this.wait(ms);
    // agent loops resend the growing message history; after the first turn most of it is a prompt-cache hit
    const cost = this.llm(node, agent, role, tokensIn, tokensOut, ms, turn > 1 ? Math.round(tokensIn * 0.85) : 0);
    this.message(agent, "assistant", text);
    this.emit({ kind: "node.end", node, task, result: "ok", costUsd: Number(cost.toFixed(5)), writes: ["messages"] });
  }

  /** a tool call made by an agent; returns the call id */
  async tool(
    agent: string,
    tool: string,
    args: string,
    ms: number,
    result: string | (() => string),
    options: { ok?: boolean; writes?: string[]; during?: () => Promise<void> } = {},
  ): Promise<string> {
    const node = `${agent}/${tool}`;
    const call = `call_${String(++this.callSeq).padStart(2, "0")}`;
    const task = `${node}#${++this.taskSeq}`;
    this.edge(`${agent}/model`, node, "fwd");
    this.emit({ kind: "tool.call", agent, tool, call, args });
    this.emit({ kind: "node.start", node, task });
    if (agent === "legal_analyst") this.trace.analystTools += 1;
    const before = this.detail.costUsd;
    if (options.during) await options.during();
    if (tool === "inspect_visual") {
      // the inspection model looks at a crop or a short clip; a local model is slower but free
      const local = this.part(ROLE_SLOTS.inspect)?.kind !== "api";
      this.llm(node, agent, "inspect", 2600, 90, ms);
      await this.wait(local ? ms * 1.5 : 0);
    }
    await this.wait(ms);
    const ok = options.ok ?? true;
    const summary = typeof result === "function" ? result() : result;
    this.emit({ kind: "tool.result", agent, tool, call, ok, summary });
    this.emit({ kind: "node.end", node, task, result: ok ? "ok" : "error", costUsd: Number((this.detail.costUsd - before).toFixed(5)), writes: options.writes ?? [] });
    this.message(agent, "tool", summary, tool);
    this.edge(`${agent}/model`, node, "back");
    return call;
  }

  /** pause the thread until the user (or autopilot) answers */
  async interrupt<T>(
    node: string,
    payload: "identity" | "questions" | "review" | "audit",
    summary: string,
    autoValue: () => T,
  ): Promise<{ value: T; auto: boolean }> {
    const id = `int-${payload}-${this.events.length + 1}`;
    this.emit({ kind: "node.start", node, task: `${node}#${++this.taskSeq}` });
    this.emit({ kind: "interrupt", node, id, payload, summary });
    this.checkpoint([node]);
    const waitStart = this.now();
    if (this.virtual) {
      this.vnow += payload === "review" ? 21000 : payload === "audit" ? 9000 : 12000;
      this.trace.humanWaitMs += this.now() - waitStart;
      return { value: autoValue(), auto: true };
    }
    const pending = new Promise<{ value: unknown; auto: boolean }>((resolve) => this.waiters.set(payload, resolve));
    if (this.opts.autopilot) {
      setTimeout(() => this.resolve(payload, autoValue(), true), 2800 / Math.max(0.25, this.hub.settings.speed));
    }
    const result = await pending;
    this.trace.humanWaitMs += this.now() - waitStart;
    return result as { value: T; auto: boolean };
  }

  isWaiting(payload: string): boolean {
    return this.waiters.has(payload);
  }

  resolve(payload: string, value: unknown, auto = false): boolean {
    const waiter = this.waiters.get(payload);
    if (!waiter) return false;
    this.waiters.delete(payload);
    waiter({ value, auto });
    return true;
  }

  endInterrupt(node: string, summary: string, auto: boolean): void {
    const last = [...this.events].reverse().find((e) => e.kind === "interrupt" && e.node === node);
    const id = last && last.kind === "interrupt" ? last.id : node;
    this.emit({ kind: "resume", node, id, summary, auto });
    this.emit({ kind: "node.end", node, task: `${node}#resume`, result: "ok", costUsd: 0, writes: [] });
  }

  countQuestion(n: number): void {
    this.stats.questions += n;
  }

  summary(): RunSummary {
    const last = this.events[this.events.length - 1];
    const done = this.detail.status === "COMPLETED" || this.detail.status === "FAILED";
    return {
      id: this.id,
      title: this.detail.title,
      status: this.detail.status,
      profile: this.profile.name,
      profileId: this.profile.id,
      experimentId: this.opts.experimentId,
      startedAt: this.createdAt,
      elapsedSec: Math.round(((done ? last?.t : this.now()) ?? 0) / 100) / 10,
      costUsd: this.detail.costUsd,
      llmCalls: this.stats.llm,
      toolCalls: this.stats.tools,
      questions: this.stats.questions,
      live: !done && !this.virtual,
      autopilot: this.opts.autopilot,
      forkedFrom: this.opts.forkedFrom,
      // an autopilot run answers itself, so it never waits for a person; it can still be paused
      waitingFor: this.opts.autopilot && this.detail.status !== "PAUSED" ? undefined : WAITING_FOR[this.detail.status],
      interactive: this.opts.experimentId && !this.opts.autopilot ? true : undefined,
      source: "live",
      scenario: this.opts.scenario ?? "카페 인터뷰",
    };
  }
}
