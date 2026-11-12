// Folds the normalized run events into what the Lab draws: node states, edge traffic, task spans,
// agent memory (messages + tool calls), state channels, budgets and checkpoints.
import type { BudgetKey, CaseStatus, RunComponent, RunEvent } from "../api/contract";

export type NodeStatus = "idle" | "running" | "waiting" | "done" | "cached" | "error";

export interface NodeRun {
  status: NodeStatus;
  runs: number;
  totalMs: number;
  startedAt?: number;
  endedAt?: number;
  costUsd: number;
  lastWrites: string[];
}

export interface TaskSpan {
  task: string;
  node: string;
  start: number;
  end?: number;
  result?: "ok" | "cached" | "error";
  costUsd: number;
}

export interface ToolSpan {
  call: string;
  agent: string;
  tool: string;
  args: string;
  start: number;
  end?: number;
  ok?: boolean;
  summary?: string;
  seq: number;
}

export interface WaitSpan {
  id: string;
  node: string;
  payload: string;
  summary: string;
  start: number;
  end?: number;
  resume?: string;
  auto?: boolean;
}

export interface MemoryItem {
  seq: number;
  t: number;
  role: "system" | "user" | "assistant" | "tool" | "call";
  text: string;
  tool?: string;
  ok?: boolean;
}

export interface ChannelState {
  name: string;
  version: number;
  writer: string;
  summary: string;
  t: number;
  seq: number;
  history: { t: number; seq: number; writer: string; summary: string }[];
}

export interface Budget {
  used: number;
  limit: number;
}

export interface Checkpoint {
  id: string;
  step: number;
  next: string[];
  t: number;
  seq: number;
}

export interface EdgeTraffic {
  count: number;
  lastT: number;
  lastDir: "fwd" | "back";
}

export interface RunState {
  title: string;
  profile: string;
  profileId?: string;
  configHash: string;
  models: Record<string, string>;
  /** pipeline parts this run uses, one per catalog slot */
  components: RunComponent[];
  costCapUsd: number;
  forkedFrom?: string;
  status: CaseStatus | null;
  ended: boolean;
  nodes: Record<string, NodeRun>;
  edges: Record<string, EdgeTraffic>;
  tasks: TaskSpan[];
  tools: ToolSpan[];
  waits: WaitSpan[];
  memory: Record<string, MemoryItem[]>;
  channels: Record<string, ChannelState>;
  budgets: Record<string, Partial<Record<BudgetKey, Budget>>>;
  checkpoints: Checkpoint[];
  validations: { node: string; ok: boolean; issues: string[]; t: number }[];
  costUsd: number;
  llmCalls: number;
  tokensIn: number;
  cachedIn: number;
  tokensOut: number;
  toolCalls: number;
  lastT: number;
  lastSeq: number;
}

const emptyNode = (): NodeRun => ({ status: "idle", runs: 0, totalMs: 0, costUsd: 0, lastWrites: [] });

export function foldRun(events: RunEvent[], uptoSeq = Number.POSITIVE_INFINITY): RunState {
  const s: RunState = {
    title: "",
    profile: "",
    configHash: "",
    models: {},
    components: [],
    costCapUsd: 0.3,
    status: null,
    ended: false,
    nodes: {},
    edges: {},
    tasks: [],
    tools: [],
    waits: [],
    memory: {},
    channels: {},
    budgets: {},
    checkpoints: [],
    validations: [],
    costUsd: 0,
    llmCalls: 0,
    tokensIn: 0,
    cachedIn: 0,
    tokensOut: 0,
    toolCalls: 0,
    lastT: 0,
    lastSeq: 0,
  };
  const node = (id: string) => (s.nodes[id] ??= emptyNode());
  const openTasks = new Map<string, TaskSpan>();
  const memory = (agent: string) => (s.memory[agent] ??= []);

  for (const e of events) {
    if (e.seq > uptoSeq) break;
    s.lastT = e.t;
    s.lastSeq = e.seq;
    switch (e.kind) {
      case "run.start":
        s.title = e.title;
        s.profile = e.profile;
        s.profileId = e.profileId;
        s.configHash = e.configHash;
        s.models = e.models;
        s.components = e.components ?? [];
        s.costCapUsd = e.costCapUsd ?? 0.3;
        s.forkedFrom = e.forkedFrom;
        break;
      case "run.status":
        s.status = e.status;
        break;
      case "node.start": {
        const n = node(e.node);
        n.status = "running";
        n.startedAt = e.t;
        n.runs += 1;
        const span: TaskSpan = { task: e.task, node: e.node, start: e.t, costUsd: 0 };
        s.tasks.push(span);
        openTasks.set(e.task, span);
        break;
      }
      case "node.end": {
        const n = node(e.node);
        n.status = e.result === "cached" ? "cached" : e.result === "error" ? "error" : "done";
        n.endedAt = e.t;
        n.costUsd += e.costUsd;
        n.lastWrites = e.writes;
        // tasks are matched by id: two calls of the same tool run in parallel and end in any order.
        // An end whose id was never started (the mock ends a wait with its own id) closes the node's latest open span.
        const span = openTasks.get(e.task) ?? [...openTasks.values()].reverse().find((open) => open.node === e.node);
        if (span) {
          span.end = e.t;
          span.result = e.result;
          span.costUsd = e.costUsd;
          n.totalMs += e.t - span.start;
          openTasks.delete(span.task);
        }
        break;
      }
      case "edge": {
        const traffic = (s.edges[e.edge] ??= { count: 0, lastT: 0, lastDir: "fwd" });
        if (e.dir === "fwd") traffic.count += 1;
        traffic.lastT = e.t;
        traffic.lastDir = e.dir;
        break;
      }
      case "llm":
        s.llmCalls += 1;
        s.tokensIn += e.tokensIn;
        s.cachedIn += e.cachedIn ?? 0;
        s.tokensOut += e.tokensOut;
        s.costUsd += e.costUsd;
        break;
      case "tool.call":
        s.toolCalls += 1;
        s.tools.push({ call: e.call, agent: e.agent, tool: e.tool, args: e.args, start: e.t, seq: e.seq });
        memory(e.agent).push({ seq: e.seq, t: e.t, role: "call", text: e.args, tool: e.tool });
        break;
      case "tool.result": {
        const span = [...s.tools].reverse().find((x) => x.call === e.call);
        if (span) {
          span.end = e.t;
          span.ok = e.ok;
          span.summary = e.summary;
        }
        break;
      }
      case "message":
        memory(e.agent).push({ seq: e.seq, t: e.t, role: e.role, text: e.text, tool: e.tool });
        break;
      case "state": {
        const prev = s.channels[e.channel];
        const entry = { t: e.t, seq: e.seq, writer: e.writer, summary: e.summary };
        s.channels[e.channel] = {
          name: e.channel,
          version: (prev?.version ?? 0) + 1,
          writer: e.writer,
          summary: e.summary,
          t: e.t,
          seq: e.seq,
          history: [...(prev?.history ?? []), entry],
        };
        break;
      }
      case "interrupt":
        node(e.node).status = "waiting";
        s.waits.push({ id: e.id, node: e.node, payload: e.payload, summary: e.summary, start: e.t });
        break;
      case "resume": {
        const wait = [...s.waits].reverse().find((w) => w.node === e.node && w.end === undefined);
        if (wait) {
          wait.end = e.t;
          wait.resume = e.summary;
          wait.auto = e.auto;
        }
        break;
      }
      case "checkpoint":
        // a run that continues after an interrupt reports the checkpoint it resumes from again: list it once
        if (!s.checkpoints.some((c) => c.id === e.id)) s.checkpoints.push({ id: e.id, step: e.step, next: e.next, t: e.t, seq: e.seq });
        break;
      case "budget": {
        const agent = (s.budgets[e.agent] ??= {});
        agent[e.key] = { used: e.used, limit: e.limit };
        break;
      }
      case "validator":
        s.validations.push({ node: e.node, ok: e.ok, issues: e.issues, t: e.t });
        break;
      case "run.end":
        s.ended = true;
        break;
      default:
        break;
    }
  }
  return s;
}

/** the agent that owns a node id, if any (legal_analyst/search_law → legal_analyst) */
export function agentOf(nodeId: string | null | undefined): "legal_analyst" | "auditor" | null {
  if (!nodeId) return null;
  if (nodeId === "legal_analyst" || nodeId.startsWith("legal_analyst/")) return "legal_analyst";
  if (nodeId === "auditor" || nodeId.startsWith("auditor/")) return "auditor";
  return null;
}

/* ---------- which node a record belongs to ---------- */

/** true when `owner` is the node `nodeId` or sits inside it: legal_analyst holds legal_analyst/model and its tools */
export function withinNode(nodeId: string | null | undefined, owner: string | null | undefined): boolean {
  if (!nodeId || !owner) return false;
  return owner === nodeId || owner.startsWith(`${nodeId}/`);
}

/** the node an agent message comes from: the model for its turns, the tool for calls and results, the agent for what it was given */
export function messageNode(agent: string, role: MemoryItem["role"], tool?: string): string {
  if (role === "assistant") return `${agent}/model`;
  if ((role === "tool" || role === "call") && tool) return `${agent}/${tool}`;
  return agent;
}

/**
 * A checkpoint is saved between super-steps and names the nodes that run next, so it is the state a node starts
 * from. The real graph only reports top-level steps (`legal_analyst`), the mock also inner ones
 * (`legal_analyst/model`): a node is matched with the step it is, the step it contains, or the step that contains it.
 */
export function checkpointBefore(nodeId: string | null | undefined, next: string[]): boolean {
  return next.some((n) => withinNode(nodeId, n) || withinNode(n, nodeId));
}

/** the node ids an event is a record of; none for run-level events (status, activity, start and end of the run) */
function eventNodes(e: RunEvent): string[] {
  switch (e.kind) {
    case "node.start":
    case "node.end":
    case "llm":
    case "interrupt":
    case "resume":
    case "validator":
      return [e.node];
    case "tool.call":
    case "tool.result":
      return [`${e.agent}/${e.tool}`];
    case "message":
      return [messageNode(e.agent, e.role, e.tool)];
    case "state":
      return [e.writer];
    case "budget":
      return [e.agent];
    case "edge":
      // a traversal is a record of both ends: the node it leaves and the node it enters
      return e.edge.split("->");
    default:
      return [];
  }
}

/** the one rule the Lab uses to tell whether an event is a record of the selected node (or of something inside it) */
export function eventOfNode(e: RunEvent, nodeId: string | null | undefined): boolean {
  if (!nodeId) return false;
  if (e.kind === "checkpoint") return checkpointBefore(nodeId, e.next);
  return eventNodes(e).some((owner) => withinNode(nodeId, owner));
}

/** what an agent's model is doing right now, for the node caption */
export function agentActivity(state: RunState, agent: string): string | null {
  const open = [...state.tools].reverse().find((t) => t.agent === agent && t.end === undefined);
  if (open) return `${open.tool} 호출 중`;
  const model = state.nodes[`${agent}/model`];
  if (model?.status === "running") return "추론 중";
  return null;
}
