import { type CSSProperties, useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import type { Decision, Segment } from "../../api/contract";
import { methodLabel, rangeText, rangeTime, timecode } from "../../lib/format";
import { cx } from "../../ui";
import { isCovered, isOnScreen } from "../media/geometry";
import { SCENE_DURATION } from "./model";
import { type SceneMode, SceneView } from "./SceneView";

interface PlayerProps {
  t: number;
  onTime: (t: number) => void;
  decisions: Decision[];
  mode: SceneMode;
  highlight?: string | null;
  caption?: string;
}

export function ScenePlayer({ t, onTime, decisions, mode, highlight, caption }: PlayerProps) {
  const [playing, setPlaying] = useState(false);
  const tRef = useRef(t);
  tRef.current = t;

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const next = tRef.current + (now - last) / 1000;
      last = now;
      if (next >= SCENE_DURATION) {
        onTime(SCENE_DURATION);
        setPlaying(false);
        return;
      }
      onTime(next);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, onTime]);

  const toggle = () => {
    if (!playing && tRef.current >= SCENE_DURATION - 0.05) onTime(0);
    setPlaying((p) => !p);
  };

  return (
    <div className="player">
      <div className="player-frame">
        <SceneView t={t} decisions={decisions} mode={mode} highlight={highlight} />
        <span className="player-badge">{caption ?? "예시 장면 · 모의 분석"}</span>
      </div>
      <div className="player-controls">
        <button type="button" className="btn btn-secondary btn-icon" onClick={toggle} aria-label={playing ? "일시정지" : "재생"}>
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
        </button>
        <span className="player-time mono num">
          {timecode(t)} <span className="muted">/ {timecode(SCENE_DURATION)}</span>
        </span>
        <input
          className="player-scrub"
          type="range"
          min={0}
          max={SCENE_DURATION}
          step={0.1}
          value={t}
          onChange={(e) => onTime(Number(e.target.value))}
          aria-label="재생 위치"
        />
      </div>
    </div>
  );
}

interface TimelineProps {
  t: number;
  segments: Segment[];
  decisions: Decision[];
  selected?: string | null;
  onSeek: (t: number) => void;
  onSelect?: (id: string) => void;
  /** length of the timeline in seconds: the real video's, or the illustrated scene's by default */
  duration?: number;
}

export function DecisionTimeline({ t, segments, decisions, selected, onSeek, onSelect, duration = SCENE_DURATION }: TimelineProps) {
  const pct = (v: number) => `${Math.min(100, (v / duration) * 100)}%`;
  const onScreen = decisions.filter(isOnScreen);
  // decisions about the file itself are for the whole file: they are listed under the timeline, not on it
  const inFile = decisions.filter((d) => !isOnScreen(d));
  return (
    <>
      <div className="dtl" aria-label="결정 타임라인">
        <div className="dtl-row dtl-scenes">
          <span className="dtl-label">장면</span>
          <div className="dtl-track">
            {segments.map((seg) => (
              <button
                type="button"
                key={seg.id}
                className="dtl-scene"
                style={{ left: pct(seg.start), width: pct(seg.end - seg.start) }}
                onClick={() => onSeek(seg.start)}
                title={`${rangeText([seg.start, seg.end])} ${seg.label}`}
              >
                <span>{seg.label}</span>
              </button>
            ))}
          </div>
        </div>
        {onScreen.map((d) => (
          <div key={d.id} className={cx("dtl-row", selected === d.id && "is-selected")}>
            <button type="button" className="dtl-label" onClick={() => onSelect?.(d.id)} title={d.targetLabel}>
              <span className="mono">{d.id}</span> {d.targetLabel}
            </button>
            <div className="dtl-track">
              {d.ranges.map(([s, e]) => (
                <button
                  type="button"
                  key={`${s}-${e}`}
                  className={cx("dtl-seg", isCovered(d) ? "seg-mosaic" : "seg-keep")}
                  style={{ left: pct(s), width: pct(Math.max(duration / 180, e - s)) }}
                  onClick={() => {
                    onSeek(s);
                    onSelect?.(d.id);
                  }}
                  aria-label={`${d.id} ${methodLabel(d.method)} ${rangeTime(s)}부터 ${rangeTime(e)}까지`}
                />
              ))}
            </div>
          </div>
        ))}
        <div className="dtl-cursor" style={{ "--pos": Math.min(1, t / duration) } as CSSProperties} aria-hidden="true" />
      </div>
      {inFile.length ? (
        <ul className="dtl-file" aria-label="파일 정보에 대한 결정">
          {inFile.map((d) => (
            <li key={d.id} className={cx("dtl-file-row", selected === d.id && "is-selected")}>
              <button type="button" className="dtl-label" onClick={() => onSelect?.(d.id)} title={d.targetLabel}>
                <span className="mono">{d.id}</span> {d.targetLabel}
              </button>
              <span className="dtl-file-note">
                <span className={cx("seg-swatch", d.method === "keep" ? "swatch-keep" : "swatch-remove")} aria-hidden="true" />
                {methodLabel(d.method)} · 파일 전체에 적용 (화면에는 없음)
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
