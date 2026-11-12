// Player for real cases: the analysis proxy (or the rendered output) in a <video>. In the plan preview a canvas
// over it shows each frame with the plan's mosaics applied, at the places the server worked out for that frame
// (geometry.ts). The canvas is repainted for every frame the video presents, outside React, so editing a decision
// or scrubbing shows the result at once and playback does not re-render the page per frame.
import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import type { CaseDetail, Decision, DecisionGeometry, FrameBox, Geometry } from "../../api/contract";
import { useGeometry } from "../../api/hooks";
import { methodLabel, timecode } from "../../lib/format";
import { ErrorState, Spinner, cx } from "../../ui";
import { boxesAt, frameAt, isCovered, isOnScreen } from "./geometry";

/** original = no effect · plan = the plan drawn over the proxy · output = the rendered file itself */
export type PlayerMode = "original" | "plan" | "output";

interface Props {
  c: CaseDetail;
  t: number;
  onTime: (t: number) => void;
  decisions: Decision[];
  mode: PlayerMode;
  highlight?: string | null;
}

type FrameSource = HTMLVideoElement | HTMLImageElement;

interface Live {
  geometry: Geometry | undefined;
  decisions: Decision[];
  highlight: string | null;
  effects: boolean;
  stills: boolean;
}

const STILL_STEP = 0.25;
const NO_DECISIONS: Decision[] = [];

const scratchCanvases = new Map<string, HTMLCanvasElement>();
/** a reusable off-screen canvas of exactly this size (a few sizes are kept, so regions do not fight over one) */
function scratch(width: number, height: number): HTMLCanvasElement {
  const key = `${width}x${height}`;
  let canvas = scratchCanvases.get(key);
  if (!canvas) {
    if (scratchCanvases.size >= 24) scratchCanvases.delete(scratchCanvases.keys().next().value as string);
    canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    scratchCanvases.set(key, canvas);
  }
  return canvas;
}

function trace(ctx: CanvasRenderingContext2D, shape: DecisionGeometry["shape"], x: number, y: number, w: number, h: number): void {
  ctx.beginPath();
  if (shape === "ellipse") ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
  else ctx.rect(x, y, w, h);
}

/**
 * A coarse copy of the crop, `cols`×`rows` cells, drawn back over it as sharp cells.
 * The crop is reduced by halving, so every cell is the mean of the pixels it covers, as in the renderer; one big
 * bilinear step would look at only a few of them and shimmer from frame to frame.
 */
function pixelate(ctx: CanvasRenderingContext2D, el: FrameSource, sx: number, sy: number, sw: number, sh: number, x: number, y: number, w: number, h: number, cols: number, rows: number): void {
  const halvings = Math.min(6, Math.max(0, Math.ceil(Math.log2(Math.max(sw / cols, sh / rows, 1)))));
  let cw = cols << halvings;
  let ch = rows << halvings;
  const work = scratch(cw, ch);
  const wctx = work.getContext("2d");
  if (!wctx) return;
  wctx.imageSmoothingEnabled = true;
  wctx.clearRect(0, 0, cw, ch);
  wctx.drawImage(el, sx, sy, sw, sh, 0, 0, cw, ch);
  for (let i = 0; i < halvings; i += 1) {
    wctx.drawImage(work, 0, 0, cw, ch, 0, 0, cw >> 1, ch >> 1);
    cw >>= 1;
    ch >>= 1;
  }
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(work, 0, 0, cols, rows, x, y, w, h);
  ctx.imageSmoothingEnabled = true;
}

/** the mosaic of one box. `perPixel`: canvas pixels per pixel of the original video, so block sizes match the renderer's */
function mosaic(ctx: CanvasRenderingContext2D, el: FrameSource, box: FrameBox, entry: DecisionGeometry, blocks: number, W: number, H: number, srcW: number, srcH: number, perPixel: number): void {
  const x = box[0] * W;
  const y = box[1] * H;
  const w = box[2] * W;
  const h = box[3] * H;
  if (w < 2 || h < 2) return;
  const kx = srcW / W;
  const ky = srcH / H;
  // the renderer's block: ceil((short side + 4) × strength / blocks) original pixels, made even
  const original = Math.ceil(((Math.min(w, h) / perPixel + 4) * entry.strength) / blocks);
  const block = Math.max(2, (original + (original % 2)) * perPixel);
  const cols = Math.max(1, Math.ceil(w / block));
  const rows = Math.max(1, Math.ceil(h / block));
  // the grid starts at the region's corner; the last column and row stop at the frame's edge
  const gw = Math.min(cols * block, W - x);
  const gh = Math.min(rows * block, H - y);
  ctx.save();
  trace(ctx, entry.shape, x, y, w, h);
  ctx.clip();
  pixelate(ctx, el, x * kx, y * ky, gw * kx, gh * ky, x, y, gw, gh, cols, rows);
  ctx.restore();
}

/** the outline and label of the selected decision: dashed where a mosaic goes, solid where the target is kept */
function outline(ctx: CanvasRenderingContext2D, box: FrameBox, shape: DecisionGeometry["shape"], kept: boolean, W: number, H: number, dpr: number, label: string, style: CSSStyleDeclaration): void {
  const x = box[0] * W;
  const y = box[1] * H;
  const w = box[2] * W;
  const h = box[3] * H;
  const token = kept ? "--m-keep" : "--m-mosaic";
  const color = style.getPropertyValue(token).trim() || "#ffffff";
  const ink = style.getPropertyValue(`${token}-ink`).trim() || "#121619";
  ctx.save();
  trace(ctx, shape, x, y, w, h);
  ctx.lineWidth = 4.5 * dpr;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
  ctx.stroke();
  ctx.lineWidth = 2.5 * dpr;
  ctx.strokeStyle = color;
  if (!kept) ctx.setLineDash([7 * dpr, 5 * dpr]);
  ctx.stroke();
  ctx.setLineDash([]);

  const size = 12 * dpr;
  ctx.font = `700 ${size}px ${style.getPropertyValue("--font-sans").trim() || "sans-serif"}`;
  const padX = 7 * dpr;
  const boxW = ctx.measureText(label).width + padX * 2;
  const boxH = size + 8 * dpr;
  const left = Math.min(Math.max(2 * dpr, x + w / 2 - boxW / 2), Math.max(2 * dpr, W - boxW - 2 * dpr));
  const above = y - boxH - 6 * dpr;
  const top = above >= 2 * dpr ? above : Math.min(H - boxH - 2 * dpr, y + h + 6 * dpr);
  ctx.fillStyle = ink;
  ctx.beginPath();
  ctx.roundRect(left, top, boxW, boxH, 4 * dpr);
  ctx.fill();
  ctx.fillStyle = "#ffffff";
  ctx.textBaseline = "middle";
  ctx.fillText(label, left + padX, top + boxH / 2 + dpr * 0.5);
  ctx.restore();
}

export function VideoPlayer({ c, t, onTime, decisions, mode, highlight = null }: Props) {
  const media = c.media;
  const duration = c.video.durationSec;
  const [playing, setPlaying] = useState(false);
  const [outputBroken, setOutputBroken] = useState(false);
  const [proxyBroken, setProxyBroken] = useState(false);
  const [stillBroken, setStillBroken] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [muted, setMuted] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tRef = useRef(t);
  tRef.current = t;
  const lastReported = useRef(t);
  const stillTime = useRef(t);
  /** timestamp of the frame the video last presented; null when it is not known (seeking, a new source, no frame callback) */
  const presented = useRef<number | null>(null);
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;

  const usingOutput = mode === "output" && !!media?.output && !outputBroken;
  const effects = mode === "plan" || (mode === "output" && !usingOutput);
  const stills = !usingOutput && proxyBroken;
  const src = usingOutput ? media?.output : media?.proxy;
  /** the <video> is mounted only once the proxy exists */
  const ready = !!media?.ready;
  // the mosaics' places are needed only while this player draws the plan itself (not for the original or the output file)
  const geometry = useGeometry(c, effects ? decisions : NO_DECISIONS);

  const live = useRef<Live>({ geometry: undefined, decisions, highlight, effects, stills });
  live.current = { geometry: geometry.data, decisions, highlight, effects, stills };

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const state = live.current;
    const el: FrameSource | null = state.stills ? imgRef.current : videoRef.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(canvas.clientWidth * dpr);
    const H = Math.round(canvas.clientHeight * dpr);
    if (W === 0 || H === 0) return;
    const active = state.effects && !!state.geometry && !!el;
    if (!active) {
      if (canvas.width !== W || canvas.height !== H) {
        canvas.width = W;
        canvas.height = H;
      }
      ctx.clearRect(0, 0, W, H);
      return;
    }
    const video = el instanceof HTMLVideoElement ? el : null;
    const srcW = video ? video.videoWidth : (el as HTMLImageElement).naturalWidth;
    const srcH = video ? video.videoHeight : (el as HTMLImageElement).naturalHeight;
    const hasFrame = srcW > 0 && (video ? video.readyState >= 2 : (el as HTMLImageElement).complete);
    // no frame to read from (seeking, loading): keep what is painted so the original never flashes through
    if (!hasFrame && canvas.width === W && canvas.height === H) return;
    if (canvas.width !== W || canvas.height !== H) {
      canvas.width = W;
      canvas.height = H;
    }
    ctx.clearRect(0, 0, W, H);
    if (!hasFrame) return;
    const geo = state.geometry as Geometry;
    // which frame of the original this picture is: the server lists every frame's mosaics, the last frame's too
    const frame = frameAt(geo, video ? (presented.current ?? video.currentTime) : stillTime.current);
    const perPixel = geo.width > 0 ? W / geo.width : 1;
    // The whole frame goes through the canvas, not only the redacted regions: the picture and its redaction are
    // then always the same frame, and both get the same colour conversion (a browser may show a <video> and a
    // canvas copy of it in slightly different colours).
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(el as FrameSource, 0, 0, W, H);
    for (const d of state.decisions) {
      const entry = geo.decisions[d.id];
      if (!entry || !isCovered(d)) continue;
      for (const box of boxesAt(entry, frame)) mosaic(ctx, el as FrameSource, box, entry, geo.mosaicBlocks, W, H, srcW, srcH, perPixel);
    }
    const selected = state.highlight ? state.decisions.find((d) => d.id === state.highlight) : undefined;
    const entry = selected ? geo.decisions[selected.id] : undefined;
    if (selected && entry) {
      const style = getComputedStyle(canvas);
      for (const box of boxesAt(entry, frame)) outline(ctx, box, entry.shape, !isCovered(selected), W, H, dpr, `${selected.id} ${methodLabel(selected.method)}`, style);
    }
  }, []);

  const report = useCallback((time: number, force = false) => {
    // while playing, the page only needs the clock a few times a second (timecode, timeline cursor)
    if (!force && Math.abs(time - lastReported.current) < 0.08) return;
    lastReported.current = time;
    onTimeRef.current(time);
  }, []);

  // repaint whenever what is drawn changes
  useEffect(draw, [draw, decisions, highlight, effects, stills, geometry.data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [draw]);

  // one repaint per presented video frame (also the frame shown after a seek while paused)
  useEffect(() => {
    const video = videoRef.current;
    if (!video || stills) return;
    let cancelled = false;
    let handle = 0;
    const precise = typeof video.requestVideoFrameCallback === "function";
    presented.current = null;
    const forget = () => {
      presented.current = null;
    };
    const tick = (mediaTime: number | null) => {
      if (cancelled) return;
      // the frame callback names the frame it presents; without it the playback clock has to do
      if (mediaTime !== null && !video.seeking) presented.current = mediaTime;
      draw();
      if (!video.seeking) report(video.currentTime, video.paused);
      schedule();
    };
    const schedule = () => {
      handle = precise ? video.requestVideoFrameCallback((_now, metadata) => tick(metadata.mediaTime)) : requestAnimationFrame(() => tick(null));
    };
    // a seek shows another frame before the next callback says which: until then, go by the clock
    video.addEventListener("seeking", forget);
    schedule();
    return () => {
      cancelled = true;
      video.removeEventListener("seeking", forget);
      if (precise) video.cancelVideoFrameCallback(handle);
      else cancelAnimationFrame(handle);
    };
  }, [src, stills, ready, draw, report]);

  // a time that did not come from this player (timeline, "장면 보기", script rows) is a seek
  useEffect(() => {
    if (Math.abs(t - lastReported.current) < 0.001) return;
    lastReported.current = t;
    const video = videoRef.current;
    if (video && !stills && video.readyState >= 1) video.currentTime = Math.min(t, Math.max(0, (video.duration || duration) - 0.001));
  }, [t, stills, duration]);

  // a new source (output ↔ proxy) starts paused at the current time
  useEffect(() => {
    setLoaded(false);
    setPlaying(false);
  }, [src, stills]);

  // stills: step the clock while "playing"
  useEffect(() => {
    if (!stills || !playing) return;
    const id = window.setInterval(() => {
      const next = Math.min(duration, tRef.current + STILL_STEP);
      report(next, true);
      if (next >= duration) setPlaying(false);
    }, STILL_STEP * 1000);
    return () => window.clearInterval(id);
  }, [stills, playing, duration, report]);

  if (!media) return null;

  const toggle = () => {
    if (stills) {
      if (!playing && tRef.current >= duration - 0.05) report(0, true);
      setPlaying((p) => !p);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      if (video.currentTime >= (video.duration || duration) - 0.05) video.currentTime = 0;
      void video.play().catch(() => setPlaying(false));
    } else {
      video.pause();
    }
  };

  const scrub = (value: number) => {
    const video = videoRef.current;
    if (video && !stills && video.readyState >= 1) video.currentTime = value;
    report(value, true);
  };

  const fail = () => {
    if (usingOutput) setOutputBroken(true);
    else setProxyBroken(true);
  };

  const ratio = c.video.width > 0 && c.video.height > 0 ? c.video.width / c.video.height : 16 / 9;
  const stage = Math.max(ratio, 4 / 3);
  const fit: CSSProperties =
    ratio >= stage
      ? { left: 0, right: 0, top: "50%", transform: "translateY(-50%)", aspectRatio: `${ratio}` }
      : { top: 0, bottom: 0, left: "50%", transform: "translateX(-50%)", aspectRatio: `${ratio}` };
  const stillAt = Math.round(Math.min(t, Math.max(0, duration - 0.05)) * 10) / 10;
  const geo = geometry.data;
  const frameNow = geo ? frameAt(geo, t) : -1;
  const activeCount = effects && geo ? decisions.filter((d) => isCovered(d) && boxesAt(geo.decisions[d.id], frameNow).length > 0).length : 0;
  const badge = usingOutput
    ? "출력 영상"
    : mode === "original"
      ? "원본 · 분석용 사본"
      : mode === "output"
        ? "출력 미리보기 · 분석용 사본"
        : "처리 미리보기";
  // Until the mosaics' places are known the preview shows no picture at all, also when they cannot be loaded:
  // a preview of the plan must never be the bare original. (Nothing to wait for when no decision is about the picture.)
  const veiled = effects && !geo && decisions.some(isOnScreen);
  const waitingForRegions = veiled && !geometry.isError;
  const updatingRegions = effects && !!geo && geometry.isPlaceholderData;

  return (
    <div className="player">
      <div className="player-frame is-real" style={{ aspectRatio: `${stage}` }}>
        {media.ready ? (
          <div className={cx("player-view", veiled && "is-veiled")} style={fit} onClick={toggle}>
            {stills ? (
              stillBroken ? null : (
                <img
                  ref={imgRef}
                  src={`${media.frame}?t=${stillAt.toFixed(1)}&w=960`}
                  data-t={stillAt}
                  alt={`${c.title} ${timecode(stillAt)} 장면`}
                  draggable={false}
                  onLoad={(event) => {
                    stillTime.current = Number(event.currentTarget.dataset.t ?? stillAt);
                    setLoaded(true);
                    draw();
                  }}
                  onError={() => setStillBroken(true)}
                />
              )
            ) : (
              <video
                ref={videoRef}
                src={src}
                muted={usingOutput ? muted : true}
                playsInline
                preload="auto"
                aria-label={`${c.title} ${timecode(t)}${activeCount ? `, 처리 ${activeCount}건 표시` : ""}`}
                onLoadedMetadata={(event) => {
                  const video = event.currentTarget;
                  // a container the browser opens but whose video codec it cannot decode reports no picture
                  if (video.videoWidth === 0) return fail();
                  video.currentTime = Math.min(tRef.current, Math.max(0, (video.duration || duration) - 0.001));
                }}
                onLoadedData={() => {
                  setLoaded(true);
                  draw();
                }}
                onSeeked={(event) => {
                  draw();
                  report(event.currentTarget.currentTime, true);
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={(event) => {
                  setPlaying(false);
                  report(event.currentTarget.currentTime, true);
                }}
                onError={fail}
              />
            )}
            <canvas ref={canvasRef} className="player-overlay" aria-hidden="true" />
            {(!loaded || waitingForRegions) && !(stills && stillBroken) ? (
              <span className="player-wait">
                <Spinner size={18} label={loaded ? "처리 영역 불러오는 중" : "영상 불러오는 중"} />
              </span>
            ) : null}
            {stills && stillBroken ? <span className="player-wait player-wait-text">이 장면을 불러오지 못했습니다.</span> : null}
          </div>
        ) : (
          <span className="player-wait player-wait-text">
            <Spinner size={16} /> 분석용 영상을 준비하고 있습니다.
          </span>
        )}
        <span className={cx("player-badge", usingOutput && "is-output")}>
          {badge}
          {waitingForRegions ? " · 처리 영역 불러오는 중" : updatingRegions ? " · 처리 영역 다시 계산하는 중" : ""}
        </span>
      </div>
      <div className={cx("player-controls", usingOutput && "has-volume")}>
        <button type="button" className="btn btn-secondary btn-icon" onClick={toggle} disabled={!media.ready} aria-label={playing ? "일시정지" : "재생"}>
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
        </button>
        <span className="player-time mono num">
          {timecode(t)} <span className="muted">/ {timecode(duration)}</span>
        </span>
        <input
          className="player-scrub"
          type="range"
          min={0}
          max={duration}
          step={0.01}
          value={Math.min(t, duration)}
          onChange={(e) => scrub(Number(e.target.value))}
          disabled={!media.ready}
          aria-label="재생 위치"
        />
        {usingOutput ? (
          <button type="button" className="btn btn-ghost btn-icon" onClick={() => setMuted((m) => !m)} aria-label={muted ? "소리 켜기" : "소리 끄기"} aria-pressed={muted}>
            {muted ? <VolumeX size={16} aria-hidden="true" /> : <Volume2 size={16} aria-hidden="true" />}
          </button>
        ) : null}
      </div>
      {mode === "output" && !usingOutput ? (
        <p className="player-note" role="status">
          {media.output
            ? "이 브라우저는 출력 파일의 코덱을 재생하지 못합니다. 분석용 사본에 최종 계획을 입힌 미리보기를 보여 주며, 실제 출력은 내려받아 확인하세요."
            : "출력 파일이 없어 분석용 사본에 계획을 입힌 미리보기를 보여 줍니다."}
        </p>
      ) : null}
      {stills ? (
        <p className="player-note" role="status">
          이 브라우저가 분석용 영상을 재생하지 못해 정지 화면을 {STILL_STEP}초 간격으로 보여 줍니다.
        </p>
      ) : null}
      {veiled && geometry.isError ? <ErrorState error={new Error("모자이크할 영역을 불러오지 못해 미리보기를 보여 줄 수 없습니다.")} onRetry={() => void geometry.refetch()} /> : null}
    </div>
  );
}
