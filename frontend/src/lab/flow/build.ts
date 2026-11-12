import { MarkerType } from "@xyflow/react";
import type { GraphSpec, GraphView } from "../../api/contract";
import { type RunState, agentActivity } from "../../lib/runState";
import type { FlowEdgeType } from "./edges";
import type { TopoNode } from "./nodes";

const ACTIVE_MS = 1500;
const EDGE_COLORS = { idle: "#4a535c", traversed: "#7d8994", active: "#4fb8d0", human: "#e3a447" };

export function buildFlow(
  graph: GraphSpec,
  view: GraphView,
  state: RunState,
  now: number,
  onDrill: (view: GraphView) => void,
): { nodes: TopoNode[]; edges: FlowEdgeType[] } {
  const specs = graph.nodes.filter((n) => n.view === view);
  const perceiveInner = graph.nodes.filter((n) => n.view === "perceive");
  const calls = new Map<string, number>();
  for (const t of state.tools) calls.set(`${t.agent}/${t.tool}`, (calls.get(`${t.agent}/${t.tool}`) ?? 0) + 1);

  const part = (slot: string) => state.components.find((c) => c.slot === slot);
  const short = (...slots: string[]) => slots.map((s) => part(s)?.short).filter(Boolean).join(" · ");
  const auditorSkipped = part("llm.auditor")?.kind === "option";
  // node captions name the parts the run's profile chose (a fork may swap one)
  const caption = (id: string): { model?: string; sublabel?: string } | undefined => {
    if (state.components.length === 0) return undefined;
    switch (id) {
      case "legal_analyst":
      case "legal_analyst/model":
        return { model: part("llm.analyst")?.label, sublabel: id.endsWith("/model") ? part("llm.analyst")?.label : undefined };
      case "auditor":
        return auditorSkipped ? { model: "감사 생략", sublabel: "이 프로필에서 생략" } : { model: part("llm.auditor")?.label };
      case "auditor/model":
        return { model: part("llm.auditor")?.label, sublabel: auditorSkipped ? "생략" : part("llm.auditor")?.label };
      case "plan":
        return { model: part("llm.planner")?.label, sublabel: `${part("llm.planner")?.short} + 검증기` };
      case "review_guide":
        return { model: part("llm.review_guide")?.label, sublabel: `${part("llm.review_guide")?.short} · 근거 · 위험` };
      case "report":
        return { model: part("llm.report")?.label, sublabel: `${part("llm.report")?.short} · 초 단위 스크립트` };
      case "render_original":
        return { sublabel: `PyAV · ${part("render.encoder")?.short}` };
      case "legal_analyst/search_law":
      case "auditor/search_law":
        return { sublabel: `${short("rag.embedding", "rag.retrieval")} → ${part("rag.reranker")?.kind === "option" ? "리랭커 없음" : part("rag.reranker")?.short}` };
      case "legal_analyst/inspect_visual":
      case "auditor/inspect_visual":
        return { sublabel: part("vlm.inspect")?.short };
      case "perceive/detect_track":
        return { sublabel: short("perception.face_detector", "perception.object_detector", "perception.tracker") };
      case "perceive/group_identities":
        return { sublabel: `${part("perception.face_embedding")?.short} · 완료 시 삭제` };
      case "perceive/ocr":
        return { sublabel: part("perception.ocr")?.short };
      case "perceive/describe_segments":
        return { model: part("vlm.scene")?.label, sublabel: `${part("vlm.scene")?.short} · ${part("vlm.input_mode")?.candidate.toUpperCase().replace("V", "V-")}` };
      case "perceive/describe_persons":
        return { model: part("vlm.person")?.label, sublabel: part("vlm.person")?.short };
      default:
        return undefined;
    }
  };

  const nodes: TopoNode[] = specs.map((raw) => {
    const named = caption(raw.id);
    const spec = named ? { ...raw, model: named.model ?? raw.model, sublabel: named.sublabel ?? raw.sublabel } : raw;
    const agent = spec.id === "legal_analyst" || spec.id === "auditor" ? spec.id : null;
    const modelAgent = spec.kind === "model" ? spec.id.split("/")[0] : null;
    const validations = state.validations.filter((v) => v.node === spec.id);
    return {
      id: spec.id,
      type: spec.kind,
      position: { x: spec.x, y: spec.y },
      width: spec.w,
      height: spec.h,
      parentId: spec.parent,
      extent: spec.parent ? ("parent" as const) : undefined,
      draggable: false,
      connectable: false,
      selectable: true,
      zIndex: spec.kind === "agent" ? 0 : 2,
      data: {
        spec,
        run: state.nodes[spec.id],
        now,
        caption: modelAgent ? agentActivity(state, modelAgent) : null,
        budgets: agent ? state.budgets[agent] : undefined,
        calls: spec.kind === "tool" ? calls.get(spec.id) : undefined,
        validation: validations.length ? { ok: validations[validations.length - 1].ok, count: validations.length } : null,
        inner:
          spec.kind === "subgraph"
            ? perceiveInner.map((n) => ({ id: n.id, label: n.label, status: state.nodes[n.id]?.status ?? "idle" }))
            : undefined,
        onDrill: spec.drill ? () => onDrill(spec.drill as GraphView) : undefined,
      },
    };
  });

  const edges: FlowEdgeType[] = graph.edges
    .filter((e) => e.view === view)
    .map((spec) => {
      const traffic = state.edges[spec.id];
      const active = !!traffic && now - traffic.lastT < ACTIVE_MS;
      const color = active
        ? EDGE_COLORS.active
        : spec.kind === "interrupt"
          ? EDGE_COLORS.human
          : traffic
            ? EDGE_COLORS.traversed
            : EDGE_COLORS.idle;
      return {
        id: spec.id,
        source: spec.source,
        target: spec.target,
        sourceHandle: `s-${spec.sourceHandle}`,
        targetHandle: `t-${spec.targetHandle}`,
        type: "flow",
        zIndex: 1,
        selectable: false,
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
        data: { spec, count: traffic?.count ?? 0, active, dir: traffic?.lastDir ?? "fwd", traversed: !!traffic },
      };
    });
  return { nodes, edges };
}

/** absolute centre of a node in flow coordinates (children are positioned relative to their parent) */
export function nodeCenter(graph: GraphSpec, id: string): { x: number; y: number } | null {
  const spec = graph.nodes.find((n) => n.id === id);
  if (!spec) return null;
  const parent = spec.parent ? graph.nodes.find((n) => n.id === spec.parent) : undefined;
  return { x: (parent?.x ?? 0) + spec.x + spec.w / 2, y: (parent?.y ?? 0) + spec.y + spec.h / 2 };
}

/** the node to follow: the most recently started node that is still running or waiting */
export function focusNode(state: RunState, view: GraphView): string | null {
  for (let i = state.tasks.length - 1; i >= 0; i -= 1) {
    const task = state.tasks[i];
    const status = state.nodes[task.node]?.status;
    if (status !== "running" && status !== "waiting") continue;
    if (task.node === "legal_analyst" || task.node === "auditor") continue;
    const inPerceive = task.node.startsWith("perceive/");
    if (view === "main" && inPerceive) return "perceive";
    if (view === "perceive" && !inPerceive) continue;
    if (task.node === "perceive" && view === "main") return "perceive";
    return task.node;
  }
  return null;
}
