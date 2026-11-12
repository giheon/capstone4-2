// Geometry of the illustrated sample scene ("카페 인터뷰", 72 s) in a 1600×900 frame.
// It stands in for real frames in the M2 mock: every target id used by the mock backend has a shape here.

export const SCENE_W = 1600;
export const SCENE_H = 900;
export const SCENE_DURATION = 72;

export type Shape2D =
  | { kind: "ellipse"; cx: number; cy: number; rx: number; ry: number }
  | { kind: "rect"; x: number; y: number; w: number; h: number };

export interface SceneState {
  t: number;
  p2: { cx: number; gesture: number };
  p3: { cx: number; view: "front" | "back" | "hidden"; opacity: number; tattoo: boolean };
  car: { x: number; visible: boolean; opacity: number };
}

const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const lerp = (a: number, b: number, k: number) => a + (b - a) * clamp(k);
const ease = (k: number) => (k <= 0 ? 0 : k >= 1 ? 1 : k * k * (3 - 2 * k));

function seg(t: number, t0: number, t1: number) {
  return ease((t - t0) / (t1 - t0));
}

export function sceneAt(t: number): SceneState {
  const gesture = t >= 29.5 && t <= 34.5 ? Math.min(seg(t, 29.5, 30.3), 1 - seg(t, 33.7, 34.5)) : 0;
  const p2 = { cx: 640 + 8 * Math.sin(t * 0.7), gesture };

  let cx = 1500;
  let view: SceneState["p3"]["view"] = "hidden";
  let opacity = 0;
  if (t >= 5 && t < 38) {
    view = "front";
    if (t < 12) cx = lerp(1520, 1250, seg(t, 5, 12));
    else if (t < 26) cx = 1250 + 10 * Math.sin((t - 12) * 0.8);
    else if (t < 34) cx = lerp(1250, 985, seg(t, 26, 34));
    else cx = lerp(985, 900, seg(t, 34, 38));
    opacity = Math.min(seg(t, 5, 5.6), 1 - seg(t, 37.4, 38));
  } else if (t >= 49 && t < 58.5) {
    view = "back";
    cx = lerp(1110, 1190, seg(t, 49, 58.5));
    opacity = Math.min(seg(t, 49, 49.6), 1 - seg(t, 57.9, 58.5));
  }
  const tattoo = view === "back" && t >= 51.7 && t <= 58.3;

  const carVisible = t >= 40.2 && t <= 48.5;
  const car = {
    x: lerp(330, -210, (t - 40.2) / 8.3),
    visible: carVisible,
    opacity: carVisible ? Math.min(seg(t, 40.2, 40.6), 1 - seg(t, 48.1, 48.5)) : 0,
  };
  return { t, p2, p3: { cx, view, opacity, tattoo }, car };
}

/** where a decision's target is at time t (null when not on screen) */
export function targetShape(targetId: string, s: SceneState, outline = false): Shape2D | null {
  const k = outline ? 0.82 : 1;
  switch (targetId) {
    case "person-2":
      return { kind: "ellipse", cx: s.p2.cx, cy: 392, rx: 70 * k, ry: 82 * k };
    case "person-3":
      if (s.p3.view === "hidden" || s.p3.opacity < 0.2) return null;
      return { kind: "ellipse", cx: s.p3.cx, cy: 326, rx: 60 * k, ry: 70 * k };
    case "text-1":
      if (s.p3.view !== "front" || s.p3.opacity < 0.2) return null;
      return { kind: "rect", x: s.p3.cx - 40, y: 404, w: 56, h: 26 };
    case "person-3-tattoo":
      if (!s.p3.tattoo) return null;
      return { kind: "rect", x: s.p3.cx - 112, y: 414, w: 38, h: 50 };
    case "plate-1": {
      if (!s.car.visible) return null;
      const x = s.car.x + 276;
      if (x + 56 < 124 || x > 656) return null;
      return { kind: "rect", x, y: 410, w: 58, h: 34 };
    }
    case "text-2":
      return { kind: "rect", x: 140, y: 54, w: 320, h: 76 };
    default:
      return null;
  }
}

export function shapeCenter(shape: Shape2D): { x: number; y: number; top: number } {
  if (shape.kind === "ellipse") return { x: shape.cx, y: shape.cy, top: shape.cy - shape.ry };
  return { x: shape.x + shape.w / 2, y: shape.y + shape.h / 2, top: shape.y };
}

/** a deterministic pseudo-random value in [0,1) for mosaic cells */
export function hash2(i: number, j: number, salt: number): number {
  const v = Math.sin(i * 127.1 + j * 311.7 + salt * 74.7) * 43758.5453;
  return v - Math.floor(v);
}

export const MOSAIC_PALETTE: Record<string, string[]> = {
  "person-2": ["#e3c3a3", "#d4b08f", "#3a2e28", "#cfc6b8", "#c9a483", "#2b2b2b"],
  "person-3": ["#d9ae8c", "#c79b78", "#2a211c", "#e8e4dc", "#b88a69"],
  "text-1": ["#ffffff", "#e9ecef", "#9aa1a8", "#5e646a"],
  "person-3-tattoo": ["#d9ae8c", "#3a3f46", "#c79b78", "#5e646a"],
  "plate-1": ["#ffffff", "#e6e9ec", "#1f2328", "#6b7178"],
  "text-2": ["#2f3a40", "#f1e7d3", "#56636b", "#c8bba2"],
};
