import { memo } from "react";
import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, Position, getSmoothStepPath } from "@xyflow/react";
import type { GraphEdgeSpec } from "../../api/contract";
import { cx } from "../../ui";

interface FlowEdgeData extends Record<string, unknown> {
  spec: GraphEdgeSpec;
  count: number;
  active: boolean;
  dir: "fwd" | "back";
  traversed: boolean;
}

export type FlowEdgeType = Edge<FlowEdgeData>;

/** orthogonal path through a fixed horizontal corridor (row wrap, retry loops) */
function corridorPath(sx: number, sy: number, tx: number, ty: number, cy: number, sp: Position, tp: Position) {
  const r = 10;
  if (sp === Position.Right && tp === Position.Left) {
    const out = sx + 24;
    const back = tx - 24;
    const d = [
      `M${sx},${sy}`,
      `H${out - r}`,
      `Q${out},${sy} ${out},${sy + r}`,
      `V${cy - r}`,
      `Q${out},${cy} ${out - r},${cy}`,
      `H${back + r}`,
      `Q${back},${cy} ${back},${cy + (ty > cy ? r : -r)}`,
      `V${ty + (ty > cy ? -r : r)}`,
      `Q${back},${ty} ${back + r},${ty}`,
      `H${tx}`,
    ].join(" ");
    return { d, lx: (out + back) / 2, ly: cy };
  }
  const dir = tx < sx ? -1 : 1;
  // the corridor may lie below both ends (retry loops under a row) or between them (a loop back to an upper row)
  const fromSide = sy <= cy ? -r : r;
  const toSide = ty <= cy ? -r : r;
  const d = [
    `M${sx},${sy}`,
    `V${cy + fromSide}`,
    `Q${sx},${cy} ${sx + dir * r},${cy}`,
    `H${tx - dir * r}`,
    `Q${tx},${cy} ${tx},${cy + toSide}`,
    `V${ty}`,
  ].join(" ");
  return { d, lx: (sx + tx) / 2, ly: cy };
}

const FlowEdge = memo(function FlowEdge(props: EdgeProps<FlowEdgeType>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  if (!data) return null;
  const spec = data.spec;
  let path: string;
  let lx: number;
  let ly: number;
  if (spec.corridorY !== undefined) {
    const c = corridorPath(sourceX, sourceY, targetX, targetY, spec.corridorY, sourcePosition, targetPosition);
    path = c.d;
    lx = c.lx;
    ly = c.ly;
  } else {
    const [p, x, y] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 10, offset: 14 });
    path = p;
    lx = x;
    ly = y;
  }
  if (spec.kind === "tool") {
    lx = targetX - 22;
    ly = targetY - 11;
  }
  const showCount = data.count > 1;
  const showLabel = !!spec.label && spec.kind !== "tool";
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={cx("fe", `fe-${spec.kind}`, data.traversed && "is-traversed", data.active && "is-active")}
      />
      {data.active ? (
        <circle r={4} className="fe-dot">
          <animateMotion dur="0.9s" repeatCount="indefinite" path={path} keyPoints={data.dir === "back" ? "1;0" : "0;1"} keyTimes="0;1" calcMode="linear" />
        </circle>
      ) : null}
      {showLabel || showCount ? (
        <EdgeLabelRenderer>
          <div
            className={cx("fe-label nodrag nopan", `fe-label-${spec.kind}`, data.active && "is-active", data.traversed && "is-traversed")}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
          >
            {showLabel ? <span>{spec.label}</span> : null}
            {showCount ? <span className="fe-count num">×{data.count}</span> : null}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
});

export const edgeTypes = { flow: FlowEdge };
