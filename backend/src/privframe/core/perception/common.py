"""Shared pieces of the perception package: the error type, profile lookups and box arithmetic.

Boxes are `(x, y, w, h)`. Inside a detector they are proxy pixels; everything stored on a Track is in upright
display pixels of the original (VideoAsset.to_original).
"""
from __future__ import annotations

from collections.abc import Callable

from privframe.core.domain import Box, Track
from privframe.core.pipeline import Candidate, ResolvedProfile

Progress = Callable[[str, float], None]  # Korean text, 0..1


class PerceptionError(RuntimeError):
    pass


class PerceptionCancelled(PerceptionError):
    """`should_stop` asked for the work to end; no partial result is returned."""


# What this backend can run, per slot. Everything else in the catalog raises PerceptionError when chosen.
RFDETR_CLASSES = {"rf-detr-nano": "RFDETRNano", "rf-detr-small": "RFDETRSmall"}  # rfdetr (PyTorch) builds
TRACKER_CLASSES = {"botsort": "BoTSORTTracker", "bytetrack": "ByteTrackTracker", "cbiou": "CBIoUTracker"}  # roboflow/trackers
RUNNABLE: dict[str, Callable[[Candidate], bool]] = {
    "perception.scene_cut": lambda c: c.adapter == "scenedetect" and c.ref in ("AdaptiveDetector", "ContentDetector"),
    "perception.face_detector": lambda c: c.adapter == "opencv-dnn" and "yunet" in (c.ref or ""),
    "perception.object_detector": lambda c: (c.adapter == "open-image-models" and (c.ref or "").startswith("rf-detr")) or (c.adapter == "rfdetr" and c.ref in RFDETR_CLASSES),
    "perception.plate_detector": lambda c: c.adapter == "none" or (c.adapter == "open-image-models" and "license-plate" in (c.ref or "")),
    "perception.tracker": lambda c: (c.adapter == "builtin" and c.id == "iou-legacy") or (c.adapter == "trackers" and c.ref in TRACKER_CLASSES),
    "perception.face_embedding": lambda c: c.adapter == "opencv-dnn" and c.id == "sface",
    "perception.ocr": lambda c: c.adapter == "rapidocr",
}


def candidate(profile: ResolvedProfile, slot_id: str) -> Candidate:
    return profile.part(slot_id).candidate


def unsupported(profile: ResolvedProfile, slot_id: str) -> PerceptionError:
    part = profile.part(slot_id)
    chosen = part.candidate
    return PerceptionError(
        f"구현되지 않은 어댑터: '{part.slot.label}'({slot_id}) 부품의 후보 '{chosen.id}'(어댑터 {chosen.adapter})는 "
        "이 백엔드에서 실행할 수 없습니다. 프로필에서 다른 후보를 고르세요."
    )


def require(profile: ResolvedProfile, *slot_ids: str) -> None:
    """Fail for a part this backend cannot run, before any model is loaded or any frame is read."""
    for slot_id in slot_ids:
        if not RUNNABLE[slot_id](candidate(profile, slot_id)):
            raise unsupported(profile, slot_id)


def tell(progress: Progress | None, text: str, fraction: float) -> None:
    if progress:
        progress(text, max(0.0, min(1.0, fraction)))


def area(box: Box) -> float:
    return max(0.0, box[2]) * max(0.0, box[3])


def centre(box: Box) -> tuple[float, float]:
    return (box[0] + box[2] / 2, box[1] + box[3] / 2)


def intersection(a: Box, b: Box) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def iou(a: Box, b: Box) -> float:
    shared = intersection(a, b)
    union = area(a) + area(b) - shared
    return shared / union if union > 0 else 0.0


def share_inside(inner: Box, outer: Box) -> float:
    """Fraction of `inner` that lies inside `outer`."""
    return intersection(inner, outer) / area(inner) if area(inner) > 0 else 0.0


def contains(box: Box, point: tuple[float, float]) -> bool:
    return box[0] <= point[0] <= box[0] + box[2] and box[1] <= point[1] <= box[1] + box[3]


def lerp(a: Box, b: Box, r: float) -> Box:
    return (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r, a[2] + (b[2] - a[2]) * r, a[3] + (b[3] - a[3]) * r)


def box_at(track: Track, frame: int, max_gap_s: float = 1.0) -> Box | None:
    """The track's box at `frame`, interpolated between its two nearest points. None when the frame is outside
    the track or falls in a hole longer than `max_gap_s` seconds."""
    points = track.points
    if not points or frame < points[0].f or frame > points[-1].f:
        return None
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:  # points are sorted by frame
        mid = (lo + hi) // 2
        if points[mid].f <= frame:
            lo = mid
        else:
            hi = mid
    before, after = points[lo], points[hi]
    if after.f == frame:
        return after.box
    if before.f == frame or before.f == after.f:
        return before.box
    if after.t - before.t > max_gap_s:
        return None
    return lerp(before.box, after.box, (frame - before.f) / (after.f - before.f))
