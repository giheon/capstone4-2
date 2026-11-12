// Illustrated stand-in for video frames (M2 mock). Draws the sample café scene at time t and
// the decisions on top: as outlines for review, or as the rendered mosaic for output.
import { type SVGProps, useId } from "react";
import type { Decision } from "../../api/contract";
import { inRanges, methodLabel, timecode } from "../../lib/format";
import { cx } from "../../ui";
import { isCovered } from "../media/geometry";
import {
  MOSAIC_PALETTE,
  SCENE_DURATION,
  SCENE_H,
  SCENE_W,
  type SceneState,
  type Shape2D,
  hash2,
  sceneAt,
  shapeCenter,
  targetShape,
} from "./model";

export type SceneMode = "review" | "output" | "original";

interface Props {
  t: number;
  decisions?: Decision[];
  mode: SceneMode;
  highlight?: string | null;
  /** neutral markers for targets under discussion (questions, findings) */
  focus?: { targetId: string; label: string }[];
  /** thumbnails: outlines only, labels are given by the surrounding caption */
  compact?: boolean;
  className?: string;
}

export function SceneView({ t, decisions = [], mode, highlight, focus = [], compact = false, className }: Props) {
  const uid = useId().replace(/:/g, "");
  const s = sceneAt(t);
  // ranges are half-open, and the last picture stays up when the clock reaches the end: it still belongs to a range that ends there
  const at = Math.min(t, SCENE_DURATION - 0.001);
  // a decision about file metadata has no ranges, so it is never drawn
  const active = mode === "original" ? [] : decisions.filter((d) => inRanges(at, d.ranges));
  return (
    <svg
      viewBox={`0 0 ${SCENE_W} ${SCENE_H}`}
      className={cx("scene", compact && "is-compact", className)}
      role="img"
      aria-label={`예시 장면 ${timecode(t)}${active.length ? `, 처리 ${active.length}건 표시` : ""}`}
    >
      <defs>
        <clipPath id={`${uid}-win`}>
          <rect x="124" y="164" width="532" height="302" />
        </clipPath>
      </defs>
      <Room />
      <g clipPath={`url(#${uid}-win)`}>
        <Street />
        <Car s={s} />
      </g>
      <WindowFrame />
      <Person3 s={s} />
      <Counter />
      <Person2 s={s} />
      {active.map((d) => (
        <Overlay key={d.id} d={d} s={s} mode={mode} uid={uid} highlight={highlight === d.id} compact={compact} />
      ))}
      {focus.map((f) => (
        <FocusMark key={f.targetId} targetId={f.targetId} label={f.label} s={s} compact={compact} />
      ))}
    </svg>
  );
}

/* ---------- illustration ---------- */

function Room() {
  return (
    <g>
      <rect width={SCENE_W} height={SCENE_H} fill="#d8d2c8" />
      <rect y="560" width={SCENE_W} height="80" fill="#cbc3b6" />
      <rect y="640" width={SCENE_W} height="260" fill="#b9ad9d" />
      {[700, 770, 850].map((y) => (
        <line key={y} x1="0" x2={SCENE_W} y1={y} y2={y} stroke="#ab9f8f" strokeWidth="3" />
      ))}
      <rect x="150" y="62" width="300" height="60" rx="4" fill="#2f3a40" />
      <text x="300" y="104" textAnchor="middle" fill="#f1e7d3" fontSize="34" fontWeight="700" fontFamily="var(--font-sans)">
        ○○커피
      </text>
      <rect x="1140" y="70" width="330" height="170" rx="6" fill="#2c3238" />
      {[0, 1, 2, 3, 4].map((i) => (
        <g key={i}>
          <rect x="1166" y={98 + i * 28} width={150 + ((i * 37) % 50)} height="8" rx="4" fill="#8d969e" />
          <rect x="1400" y={98 + i * 28} width="44" height="8" rx="4" fill="#c8bba2" />
        </g>
      ))}
      {[980, 1260].map((x) => (
        <g key={x}>
          <line x1={x} y1="0" x2={x} y2="148" stroke="#3a3f45" strokeWidth="3" />
          <path d={`M${x - 40} 150 h80 l-14 30 h-52 z`} fill="#2f3a40" />
          <ellipse cx={x} cy="182" rx="22" ry="5" fill="#f3e3b5" />
        </g>
      ))}
      <rect x="860" y="262" width="740" height="10" fill="#8a7360" />
      {[900, 960, 1020, 1520].map((x, i) => (
        <rect key={x} x={x} y={222 - (i % 2) * 10} width="40" height={40 + (i % 2) * 10} rx="5" fill={i % 2 ? "#b5a27e" : "#9e8f73"} />
      ))}
    </g>
  );
}

function Street() {
  return (
    <g>
      <rect x="124" y="164" width="532" height="302" fill="#b9c8d1" />
      <rect x="124" y="232" width="120" height="150" fill="#a2b1bb" />
      <rect x="252" y="198" width="150" height="184" fill="#94a5b0" />
      <rect x="410" y="246" width="110" height="136" fill="#a9b7c0" />
      <rect x="528" y="214" width="140" height="168" fill="#97a8b2" />
      {[0, 1, 2, 3].map((r) =>
        [0, 1, 2].map((c) => <rect key={`${r}-${c}`} x={268 + c * 44} y={214 + r * 38} width="24" height="20" fill="#b6c4cd" />),
      )}
      <rect x="124" y="372" width="532" height="12" fill="#9aa3aa" />
      <rect x="124" y="384" width="532" height="82" fill="#686e75" />
      {[150, 270, 390, 510, 630].map((x) => (
        <rect key={x} x={x} y="430" width="60" height="6" fill="#c9cdd1" />
      ))}
    </g>
  );
}

function WindowFrame() {
  return (
    <g fill="#4a5056">
      <path d="M110 150 h560 v330 h-560 z M124 164 v302 h532 v-302 z" fillRule="evenodd" />
      <rect x="384" y="164" width="10" height="302" />
      <rect x="100" y="476" width="580" height="14" rx="3" fill="#5a6066" />
    </g>
  );
}

function Car({ s }: { s: SceneState }) {
  if (!s.car.visible) return null;
  const x = s.car.x;
  return (
    <g opacity={s.car.opacity}>
      <path
        d={`M${x} 452 L${x} 418 Q${x + 6} 402 ${x + 40} 398 L${x + 82} 370 Q${x + 100} 360 ${x + 150} 360 L${x + 212} 360 Q${x + 248} 362 ${x + 264} 394 L${x + 300} 402 Q${x + 320} 408 ${x + 320} 428 L${x + 320} 452 Z`}
        fill="#3e5c7a"
      />
      <path d={`M${x + 92} 372 L${x + 150} 370 L${x + 150} 396 L${x + 66} 398 Z`} fill="#9fb4c4" />
      <path d={`M${x + 160} 370 L${x + 214} 370 Q${x + 236} 372 ${x + 248} 396 L${x + 160} 396 Z`} fill="#9fb4c4" />
      <circle cx={x + 66} cy="456" r="22" fill="#1f2328" />
      <circle cx={x + 66} cy="456" r="9" fill="#8d969e" />
      <circle cx={x + 250} cy="456" r="22" fill="#1f2328" />
      <circle cx={x + 250} cy="456" r="9" fill="#8d969e" />
      <rect x={x + 280} y="414" width="50" height="26" rx="3" fill="#ffffff" stroke="#1f2328" strokeWidth="2" />
      <text x={x + 305} y="432" textAnchor="middle" fontSize="11" fontWeight="700" fill="#1f2328" fontFamily="var(--font-sans)">
        12가3456
      </text>
    </g>
  );
}

function Counter() {
  return (
    <g>
      <rect x="850" y="488" width="760" height="18" fill="#8a7360" />
      <rect x="860" y="506" width="740" height="134" fill="#6e5a49" />
      <rect x="1360" y="392" width="170" height="96" rx="6" fill="#9aa1a8" />
      <rect x="1376" y="408" width="138" height="34" rx="4" fill="#6b7178" />
      <rect x="1400" y="452" width="22" height="30" fill="#4a5056" />
      <rect x="1470" y="452" width="22" height="30" fill="#4a5056" />
    </g>
  );
}

function Person2({ s }: { s: SceneState }) {
  const c = s.p2.cx;
  const g = s.p2.gesture;
  const hx = c + 64 + g * 220;
  const hy = 688 - g * 300;
  return (
    <g>
      <rect x={c - 112} y="468" width="224" height="210" rx="18" fill="#5d4b3e" />
      <path d={`M${c - 94} 700 L${c - 88} 522 Q${c - 82} 478 ${c - 30} 470 L${c + 30} 470 Q${c + 82} 478 ${c + 88} 522 L${c + 94} 700 Z`} fill="#cfc6b8" />
      <rect x="360" y="680" width="560" height="26" rx="8" fill="#7b6553" />
      <rect x="380" y="706" width="520" height="12" fill="#6a5545" />
      <rect x="420" y="718" width="18" height="160" fill="#5d4b3e" />
      <rect x="842" y="718" width="18" height="160" fill="#5d4b3e" />
      <rect x="470" y="648" width="38" height="34" rx="6" fill="#f3efe8" />
      <rect x="800" y="652" width="34" height="30" rx="6" fill="#e9e1d6" />
      <path d={`M${c - 86} 540 Q${c - 124} 640 ${c - 62} 690`} stroke="#cfc6b8" strokeWidth="40" strokeLinecap="round" fill="none" />
      <path
        d={`M${c + 86} 540 Q${c + 128 + g * 70} ${640 - g * 120} ${hx} ${hy}`}
        stroke="#cfc6b8"
        strokeWidth="40"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx={hx} cy={hy} r="20" fill="#e3c3a3" />
      <rect x={c - 18} y="434" width="36" height="42" fill="#d9b796" />
      <ellipse cx={c} cy="392" rx="56" ry="64" fill="#e3c3a3" />
      <path d={`M${c - 58} 388 Q${c - 62} 318 ${c} 320 Q${c + 62} 318 ${c + 58} 390 Q${c + 40} 350 ${c} 352 Q${c - 36} 350 ${c - 58} 388 Z`} fill="#3a2e28" />
      <circle cx={c - 22} cy="398" r="15" fill="none" stroke="#2b2b2b" strokeWidth="4" />
      <circle cx={c + 22} cy="398" r="15" fill="none" stroke="#2b2b2b" strokeWidth="4" />
      <line x1={c - 7} y1="398" x2={c + 7} y2="398" stroke="#2b2b2b" strokeWidth="4" />
      <circle cx={c - 22} cy="399" r="3.5" fill="#2b2b2b" />
      <circle cx={c + 22} cy="399" r="3.5" fill="#2b2b2b" />
      <path d={`M${c - 14} 428 Q${c} 437 ${c + 14} 428`} stroke="#9c5f4f" strokeWidth="4" fill="none" strokeLinecap="round" />
    </g>
  );
}

function Person3({ s }: { s: SceneState }) {
  if (s.p3.view === "hidden" || s.p3.opacity <= 0) return null;
  const c = s.p3.cx;
  const back = s.p3.view === "back";
  return (
    <g opacity={s.p3.opacity}>
      <path d={`M${c - 76} 500 L${c - 72} 410 Q${c - 66} 380 ${c - 24} 374 L${c + 24} 374 Q${c + 66} 380 ${c + 72} 410 L${c + 76} 500 Z`} fill="#e8e4dc" />
      {back ? (
        <path d={`M${c - 46} 382 L${c + 46} 470 M${c + 46} 382 L${c - 46} 470`} stroke="#2f5d50" strokeWidth="8" />
      ) : (
        <path d={`M${c - 48} 500 L${c - 48} 420 L${c + 48} 420 L${c + 48} 500 Z M${c - 40} 420 L${c - 22} 378 M${c + 40} 420 L${c + 22} 378`} fill="#2f5d50" stroke="#2f5d50" strokeWidth="6" />
      )}
      <path d={`M${c - 72} 398 L${c - 96} 476`} stroke="#e8e4dc" strokeWidth="26" strokeLinecap="round" />
      {back ? <path d={`M${c - 90} 428 L${c - 98} 478`} stroke="#d9ae8c" strokeWidth="22" strokeLinecap="round" /> : null}
      {s.p3.tattoo ? (
        <path d={`M${c - 100} 432 l8 9 l-7 8 l9 10 l-6 8`} stroke="#2e3338" strokeWidth="3.5" fill="none" strokeLinecap="round" />
      ) : null}
      <path d={`M${c + 72} 398 L${c + 92} 476`} stroke="#e8e4dc" strokeWidth="26" strokeLinecap="round" />
      <rect x={c - 14} y="354" width="28" height="26" fill="#cfa07e" />
      <ellipse cx={c} cy="326" rx="46" ry="52" fill={back ? "#2a211c" : "#d9ae8c"} />
      {back ? (
        <circle cx={c} cy="286" r="16" fill="#2a211c" />
      ) : (
        <g>
          <path d={`M${c - 47} 322 Q${c - 44} 268 ${c} 270 Q${c + 46} 268 ${c + 47} 322 Q${c + 30} 292 ${c} 294 Q${c - 28} 292 ${c - 47} 322 Z`} fill="#2a211c" />
          <circle cx={c - 16} cy="330" r="4" fill="#2a211c" />
          <circle cx={c + 16} cy="330" r="4" fill="#2a211c" />
          <path d={`M${c - 12} 352 Q${c} 359 ${c + 12} 352`} stroke="#8e5444" strokeWidth="3.5" fill="none" strokeLinecap="round" />
          <rect x={c - 40} y="406" width="56" height="24" rx="3" fill="#ffffff" />
          <rect x={c - 34} y="413" width="30" height="4" fill="#5e646a" />
          <rect x={c - 34} y="421" width="40" height="3" fill="#9aa1a8" />
        </g>
      )}
    </g>
  );
}

/* ---------- overlays ---------- */

function ShapeEl({ shape, ...rest }: { shape: Shape2D } & SVGProps<SVGElement>) {
  if (shape.kind === "ellipse") {
    return <ellipse cx={shape.cx} cy={shape.cy} rx={shape.rx} ry={shape.ry} {...(rest as SVGProps<SVGEllipseElement>)} />;
  }
  return <rect x={shape.x} y={shape.y} width={shape.w} height={shape.h} rx="4" {...(rest as SVGProps<SVGRectElement>)} />;
}

function bbox(shape: Shape2D) {
  return shape.kind === "ellipse"
    ? { x: shape.cx - shape.rx, y: shape.cy - shape.ry, w: shape.rx * 2, h: shape.ry * 2 }
    : { x: shape.x, y: shape.y, w: shape.w, h: shape.h };
}

/** the mosaic over a target: coarse cells in the target's own colours */
function Mosaic({ d, shape, uid }: { d: Decision; shape: Shape2D; uid: string }) {
  const clip = `${uid}-clip-${d.id}`;
  const box = bbox(shape);
  const cell = Math.max(10, Math.round(Math.min(box.w, box.h) / 4));
  const palette = MOSAIC_PALETTE[d.targetId] ?? ["#9aa1a8", "#6b7178", "#d0d5d9"];
  const cells = [];
  for (let i = 0; i * cell < box.w; i += 1) {
    for (let j = 0; j * cell < box.h; j += 1) {
      const color = palette[Math.floor(hash2(i, j, d.targetId.length) * palette.length)];
      cells.push(<rect key={`${i}-${j}`} x={box.x + i * cell} y={box.y + j * cell} width={cell + 0.5} height={cell + 0.5} fill={color} />);
    }
  }
  return (
    <g>
      <clipPath id={clip}>
        <ShapeEl shape={shape} />
      </clipPath>
      <g clipPath={`url(#${clip})`}>{cells}</g>
    </g>
  );
}

function Overlay({ d, s, mode, uid, highlight, compact }: { d: Decision; s: SceneState; mode: SceneMode; uid: string; highlight: boolean; compact: boolean }) {
  const shape = targetShape(d.targetId, s, d.shape === "outline");
  if (!shape) return null;
  const covered = isCovered(d);
  if (mode === "output") return covered ? <Mosaic d={d} shape={shape} uid={uid} /> : null;
  const center = shapeCenter(shape);
  const label = `${d.id} ${methodLabel(d.method)}`;
  const width = labelWidth(label);
  return (
    <g className={cx("ov", covered ? "ov-mosaic" : "ov-keep", highlight && "is-highlight")}>
      {covered ? (
        <g opacity="0.55">
          <Mosaic d={d} shape={shape} uid={uid} />
        </g>
      ) : null}
      <ShapeEl shape={shape} className="ov-shape" fill="none" />
      {compact ? null : (
        <g transform={`translate(${center.x - width / 2}, ${Math.max(6, center.top - 46)})`}>
          <rect width={width} height="38" rx="7" className="ov-label-bg" />
          <text x={width / 2} y="27" textAnchor="middle" className="ov-label">
            {label}
          </text>
        </g>
      )}
    </g>
  );
}

function FocusMark({ targetId, label, s, compact }: { targetId: string; label: string; s: SceneState; compact: boolean }) {
  const shape = targetShape(targetId, s);
  if (!shape) return null;
  const center = shapeCenter(shape);
  const width = labelWidth(label);
  return (
    <g className="ov ov-focus">
      <ShapeEl shape={shape} className="ov-shape" fill="none" />
      {compact ? null : (
        <g transform={`translate(${center.x - width / 2}, ${Math.max(6, center.top - 46)})`}>
          <rect width={width} height="38" rx="7" className="ov-label-bg" />
          <text x={width / 2} y="27" textAnchor="middle" className="ov-label">
            {label}
          </text>
        </g>
      )}
    </g>
  );
}

/** rough width for a label in viewBox units: Hangul glyphs are ~1em, Latin/digits ~0.6em at 24px */
function labelWidth(text: string): number {
  let w = 28;
  for (const ch of text) w += /[\u3131-\uD79D]/.test(ch) ? 24 : ch === " " ? 8 : 15;
  return w;
}
