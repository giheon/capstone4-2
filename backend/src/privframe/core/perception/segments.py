"""Scene cuts (slot perception.scene_cut).

PySceneDetect's detectors are fed proxy frames decoded by PyAV, one call per frame with the frame index, so
no OpenCV capture and no frame-rate assumption is involved (design §6.2): a cut is a frame index and its
time comes from `times`.
"""
from __future__ import annotations

import statistics

import cv2

from privframe.core import video
from privframe.core.domain import SegmentInfo, VideoAsset
from privframe.core.pipeline import ResolvedProfile

from .common import Progress, candidate, require, tell

MIN_SCENE_S = 0.5  # shorter shots are joined to their neighbour (PySceneDetect's default is 15 frames ≈ 0.5 s at 30 fps)
DETECT_WIDTH = 320  # PySceneDetect's own manager also scores a reduced frame (at least 256 px wide)
MAX_KEYFRAMES = 8  # design §7.4: at most 8 keyframes per scene go to the VLM


def _detector(profile: ResolvedProfile, min_frames: int):
    require(profile, "perception.scene_cut")
    from scenedetect.detectors import AdaptiveDetector, ContentDetector

    kind = AdaptiveDetector if candidate(profile, "perception.scene_cut").ref == "AdaptiveDetector" else ContentDetector
    return kind(min_scene_len=min_frames)


def keyframes(start: int, end: int, limit: int = MAX_KEYFRAMES) -> list[int]:
    """Up to `limit` evenly spaced frame indices of a segment, taken at the middle of equal parts."""
    count = min(limit, end - start + 1)
    return [start + int((i + 0.5) * (end - start + 1) / count) for i in range(count)]


def segments_from_cuts(cuts: list[int], times: list[float], duration: float) -> list[SegmentInfo]:
    """Segments between cut frames (a cut is the first frame of a new shot)."""
    last = len(times) - 1
    starts = [0] + sorted({c for c in cuts if 0 < c <= last})
    out: list[SegmentInfo] = []
    for number, start in enumerate(starts, 1):
        end = starts[number] - 1 if number < len(starts) else last
        end_time = times[end + 1] if end < last else max(duration, times[last])
        out.append(SegmentInfo(id=f"S{number}", start=round(times[start], 3), end=round(end_time, 3), start_frame=start, end_frame=end, keyframes=keyframes(start, end)))
    return out


def find_segments(asset: VideoAsset, times: list[float], profile: ResolvedProfile, *, progress: Progress | None = None) -> list[SegmentInfo]:
    if not times:
        return []
    interval = statistics.median(b - a for a, b in zip(times, times[1:])) if len(times) > 1 else 1 / 30
    detector = _detector(profile, max(2, round(MIN_SCENE_S / max(interval, 1e-3))))
    cuts: set[int] = set()
    seen = -1
    for index, image in video.iter_proxy(asset):
        if image.shape[1] > DETECT_WIDTH:
            image = cv2.resize(image, (DETECT_WIDTH, round(image.shape[0] * DETECT_WIDTH / image.shape[1])), interpolation=cv2.INTER_AREA)
        cuts.update(detector.process_frame(index, image))
        seen = index
        if index % 60 == 0:
            tell(progress, "장면 경계를 찾는 중", index / len(times))
    if seen >= 0:
        cuts.update(detector.post_process(seen))
    found = segments_from_cuts(sorted(cuts), times, asset.meta.duration_s)
    tell(progress, f"장면 {len(found)}개", 1.0)
    return found
