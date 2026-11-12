// A still of a real case: one frame from the analysis proxy, optionally with targets outlined.
import { type CSSProperties, useMemo, useState } from "react";
import { FileVideo } from "lucide-react";
import type { CaseDetail } from "../../api/contract";
import { useGeometry } from "../../api/hooks";
import { timecode } from "../../lib/format";
import { cx } from "../../ui";
import { recallThumbnail } from "../upload";
import { markDecisions, targetMarks } from "./geometry";

export interface StillMark {
  targetId: string;
  label: string;
}

interface Props {
  c: CaseDetail;
  /** seconds on the video timeline */
  t: number;
  marks?: StillMark[];
  /** fill the box and crop (thumbnails); otherwise the whole frame is shown and may be letterboxed */
  cover?: boolean;
  /** requested width in pixels */
  width?: number;
}

const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

export function FrameStill({ c, t, marks = [], cover = false, width = 640 }: Props) {
  const media = c.media;
  // a mark names a target; file metadata and targets the analysis does not know have no place in the picture
  const marked = useMemo(
    () => marks.flatMap((m) => c.targets.filter((target) => target.id === m.targetId).map((target) => ({ target, label: m.label }))),
    [marks, c.targets],
  );
  const where = useMemo(() => marked.flatMap((m) => markDecisions(m.target)), [marked]);
  const geometry = useGeometry(c, where);
  const [failed, setFailed] = useState<string | null>(null);
  if (!media) return null;

  const at = Math.max(0, Math.min(t, Math.max(0, c.video.durationSec - 0.05)));
  const src = `${media.frame}?t=${at.toFixed(2)}&w=${width}`;
  if (!media.ready || failed === src) {
    // the proxy does not exist yet: the thumbnail the browser made at upload, or a neutral tile
    const remembered = recallThumbnail(c.id);
    return (
      <span className={cx("still", "is-cover", "is-placeholder")}>
        {remembered ? <img src={remembered} alt="" /> : <FileVideo size={cover ? 20 : 26} aria-hidden="true" />}
      </span>
    );
  }

  const ratio = c.video.width > 0 && c.video.height > 0 ? c.video.width / c.video.height : 16 / 9;
  const fit: CSSProperties = cover
    ? { inset: 0 }
    : ratio >= 16 / 9
      ? { left: 0, right: 0, top: "50%", transform: "translateY(-50%)", aspectRatio: `${ratio}` }
      : { top: 0, bottom: 0, left: "50%", transform: "translateX(-50%)", aspectRatio: `${ratio}` };
  const found = geometry.data;
  const boxes = found ? marked.flatMap((m) => targetMarks(found, m.target, at).map((mark) => ({ ...mark, label: m.label }))) : [];
  const names = marks.map((m) => m.label).join(", ");

  return (
    <span className={cx("still", cover && "is-cover")}>
      <span className="still-view" style={fit}>
        <img src={src} alt={cover ? "" : `${timecode(at)} 장면${names ? ` · 표시: ${names}` : ""}`} loading="lazy" draggable={false} onError={() => setFailed(src)} />
        {boxes.map((b, i) => (
          <span
            key={i}
            className={cx("still-mark", b.round && "is-round")}
            style={{ left: pct(b.box[0]), top: pct(b.box[1]), width: pct(b.box[2]), height: pct(b.box[3]) }}
            aria-hidden="true"
          >
            <span className={cx("still-mark-label", b.box[1] < 0.16 && "is-below")}>{b.label}</span>
          </span>
        ))}
      </span>
    </span>
  );
}
