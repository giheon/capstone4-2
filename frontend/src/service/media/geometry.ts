// Where a decision is drawn on a real frame. Nothing is worked out here: the server computes every frame's boxes
// with the renderer's own rules (backend core/geometry.py, `GET|POST /api/cases/{id}/geometry`) and this file only
// looks them up, so the preview has the mosaic on exactly the frames the output will have it on, the last one included.
import type { Decision, DecisionGeometry, FrameBox, Geometry, GeometryDecision, Target } from "../../api/contract";

/** frame times arrive rounded to 0.1 ms, so a frame's own timestamp may be a hair before its rounded start */
const TIME_EPS = 1e-4;

/** a decision about something in the picture (file metadata has no place on screen or on the timeline) */
export function isOnScreen(d: Pick<Decision, "scope">): boolean {
  return d.scope !== "metadata";
}

/** a decision that covers its target with the mosaic */
export function isCovered(d: Pick<Decision, "scope" | "method">): boolean {
  return isOnScreen(d) && d.method !== "keep";
}

/** index of the frame shown at time `t`: the last one that has started. The last frame stays up to the end of the video. */
export function frameAt(geometry: Geometry, t: number): number {
  const frames = geometry.frames;
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid] <= t + TIME_EPS) lo = mid;
    else hi = mid - 1;
  }
  return hi < 0 ? -1 : lo;
}

/** the boxes a decision has on one frame (usually one; none when the frame is outside its applied ranges) */
export function boxesAt(entry: DecisionGeometry | undefined, frame: number): FrameBox[] {
  const out: FrameBox[] = [];
  if (!entry || frame < 0) return out;
  for (const [first, boxes] of entry.runs) {
    if (frame >= first && frame < first + boxes.length) out.push(boxes[frame - first]);
  }
  return out;
}

/* ---------- pointing at a target on a still ---------- */

interface Mark {
  box: FrameBox;
  /** a face is outlined round, everything else as a box */
  round: boolean;
}

const markId = (target: Target, scope: string) => `${target.id}:${scope}`;

/**
 * The geometry request that says where a target is: a mosaic over everything the target is visible in, per part
 * that can be pointed at (a person's face and whole figure; a text; a plate). File metadata is nowhere.
 */
export function markDecisions(target: Target): GeometryDecision[] {
  const scopes = target.kind === "person" ? (["face", "body"] as const) : target.kind === "text" || target.kind === "plate" ? ([target.kind] as const) : [];
  return scopes.map((scope) => ({ id: markId(target, scope), targetId: target.id, scope, method: "mosaic", shape: scope === "face" ? "ellipse" : "box", ranges: target.ranges }));
}

/** how much of `box` lies inside `area` (0–1) */
function inside(box: FrameBox, area: FrameBox): number {
  const w = Math.min(box[0] + box[2], area[0] + area[2]) - Math.max(box[0], area[0]);
  const h = Math.min(box[1] + box[3], area[1] + area[3]) - Math.max(box[1], area[1]);
  return w > 0 && h > 0 && box[2] > 0 && box[3] > 0 ? (w * h) / (box[2] * box[3]) : 0;
}

/** the marks of a target on one frame */
function marksOn(geometry: Geometry, target: Target, frame: number): Mark[] {
  if (target.kind !== "person") return boxesAt(geometry.decisions[markId(target, target.kind)], frame).map((box) => ({ box, round: false }));
  // The figure's geometry is where the person was tracked on this frame. A face mosaic is also held where only
  // the figure was tracked (at the face's last known place): a face mark counts only while it lies on the person.
  const figure = boxesAt(geometry.decisions[markId(target, "body")], frame);
  const faces = boxesAt(geometry.decisions[markId(target, "face")], frame).filter((box) => figure.length === 0 || figure.some((area) => inside(box, area) >= 0.5));
  return faces.length ? faces.map((box) => ({ box, round: true })) : figure.map((box) => ({ box, round: false }));
}

/**
 * Where a target is at time `t`, for outlining it on a still. A still is one moment, and the moment a question
 * or a finding names may lie a little outside the target's range: then the nearest frame within `within` seconds.
 */
export function targetMarks(geometry: Geometry, target: Target, t: number, within = 0.75): Mark[] {
  const frame = frameAt(geometry, t);
  const here = marksOn(geometry, target, frame);
  if (here.length || frame < 0) return here;
  let nearest = -1;
  let away = within;
  for (const { id } of markDecisions(target)) {
    for (const [first, boxes] of geometry.decisions[id]?.runs ?? []) {
      const at = Math.min(first + boxes.length - 1, Math.max(first, frame));
      const distance = Math.abs(geometry.frames[at] - geometry.frames[frame]);
      if (distance <= away) [nearest, away] = [at, distance];
    }
  }
  return nearest < 0 ? [] : marksOn(geometry, target, nearest);
}
