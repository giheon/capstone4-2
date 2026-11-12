// One entry point for "show this case's video": real cases (c.media, real backend) get real frames,
// the M2 mock keeps its illustrated scene.
import type { CaseDetail, Decision, Target } from "../../api/contract";
import { ScenePlayer } from "../scene/Player";
import { SCENE_DURATION } from "../scene/model";
import { type SceneMode, SceneView } from "../scene/SceneView";
import { FrameStill, type StillMark } from "./FrameStill";
import { type PlayerMode, VideoPlayer } from "./VideoPlayer";

/** length of the timeline the screens draw: the real video's, or the illustrated scene's */
export function caseDuration(c: CaseDetail): number {
  return c.media ? Math.max(0.1, c.video.durationSec) : SCENE_DURATION;
}

/** a short name for a target on a still: people keep their number, things their first word (명찰, 번호판) */
export function shortTargetLabel(target: Target | undefined, fallback: string): string {
  if (!target) return fallback;
  return target.kind === "person" ? target.label : (target.label.split(" ")[0] ?? fallback);
}

interface StillProps {
  c: CaseDetail;
  t: number;
  focus?: StillMark[];
}

/** a frame with the targets under discussion outlined (identity candidates, question and finding cards) */
export function CaseStill({ c, t, focus = [] }: StillProps) {
  if (c.media) return <FrameStill c={c} t={t} marks={focus} />;
  return <SceneView compact t={t} mode="original" focus={focus} />;
}

interface PlayerProps {
  c: CaseDetail;
  t: number;
  onTime: (t: number) => void;
  decisions: Decision[];
  /** review = the plan while it is edited · output = the result · original = untouched */
  mode: SceneMode;
  highlight?: string | null;
  /** badge on the illustrated scene (mock only; the real player labels itself) */
  caption?: string;
}

const REAL_MODE: Record<SceneMode, PlayerMode> = { review: "plan", output: "output", original: "original" };

export function CasePlayer({ c, t, onTime, decisions, mode, highlight, caption }: PlayerProps) {
  if (c.media) return <VideoPlayer c={c} t={t} onTime={onTime} decisions={decisions} mode={REAL_MODE[mode]} highlight={highlight} />;
  return <ScenePlayer t={t} onTime={onTime} decisions={decisions} mode={mode} highlight={highlight} caption={caption} />;
}
