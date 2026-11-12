"""Detection and tracking of faces, people and licence plates (slots face_detector, object_detector,
plate_detector, tracker).

Frames are sampled per segment at the profile's analysis rate, so a track never crosses a scene cut. Each
(segment, kind) has its own tracker. Detector boxes are proxy pixels; tracks are stored in original pixels.
"""
from __future__ import annotations

import bisect
from collections.abc import Callable
from typing import NamedTuple, Protocol

import numpy as np

from privframe.core import video
from privframe.core.domain import Box, SegmentInfo, Track, TrackPoint, VideoAsset
from privframe.core.pipeline import ResolvedProfile

from .common import TRACKER_CLASSES, PerceptionCancelled, PerceptionError, Progress, candidate, iou, lerp, require, tell
from .models import Det, face_detector, person_detector, plate_detector

KINDS = ("face", "person", "plate")
# Lowest detector score handed to the tracker. Below `START_SCORE` a detection can only continue a track
# (ByteTrack's low-score association), it cannot start one. Recall matters more than precision here: a missed
# face is an unredacted face, while a false track is at worst an extra target the user ignores.
MIN_SCORE = {"face": 0.5, "person": 0.35, "plate": 0.3}
START_SCORE = {"face": 0.7, "person": 0.5, "plate": 0.5}
# A track seen in a single sample is kept only when the detector was this sure (brief: "very high").
SOLO_SCORE = {"face": 0.9, "person": 0.9, "plate": 0.8}
LOST_S = 1.0  # a track that reappears within this time keeps its id; the missed samples are interpolated
# A plate box that is square or taller than wide is not a plate in an upright frame: Korean plates are 1.8:1 to
# 4.7:1 and stay above 1.2:1 up to about 50° of yaw. On the team clips the plate model fired (up to 0.8) on an
# upright bright window and on a square reflection; this shape test removes both. Not tuned on real plates.
PLATE_MIN_ASPECT = 1.2


class Sample(NamedTuple):
    f: int
    t: float
    box: Box  # proxy pixels
    score: float  # 0 marks a box interpolated across a short detection gap


class Tracker(Protocol):
    def feed(self, f: int, t: float, detections: list[Det], image: np.ndarray | None = None) -> None: ...

    def result(self) -> list[list[Sample]]: ...


class IouTracker:
    """Builtin `iou-legacy`: greedy one-to-one matching on box IoU against each track's last box, with a
    reconnect window of `max_gap` missed samples (the 1.0 code used IoU 0.3 and gap 2)."""

    def __init__(self, iou_threshold: float = 0.3, max_gap: int = 2) -> None:
        self.iou_threshold, self.max_gap = iou_threshold, max_gap
        self._tracks: list[list[Sample]] = []
        self._last: list[int] = []  # step of each track's latest sample
        self._step = 0

    def feed(self, f: int, t: float, detections: list[Det], image: np.ndarray | None = None) -> None:
        self._step += 1
        live = [n for n, last in enumerate(self._last) if self._step - last <= self.max_gap + 1]
        pairs = sorted(
            ((iou(self._tracks[n][-1].box, box), n, i) for n in live for i, (box, _) in enumerate(detections)),
            key=lambda p: (-p[0], p[1], p[2]),
        )
        used_tracks: set[int] = set()
        used: set[int] = set()
        for overlap, n, i in pairs:
            if overlap < self.iou_threshold:
                break
            if n in used_tracks or i in used:
                continue
            used_tracks.add(n)
            used.add(i)
            self._tracks[n].append(Sample(f, t, *detections[i]))
            self._last[n] = self._step
        for i, (box, score) in enumerate(detections):
            if i not in used:
                self._tracks.append([Sample(f, t, box, score)])
                self._last.append(self._step)

    def result(self) -> list[list[Sample]]:
        return self._tracks


class LibraryTracker:
    """roboflow/trackers (BoT-SORT, ByteTrack, C-BIoU) behind the same interface.

    The library gives a track its number only at the second matched sample, so the first detection of every
    track comes back unnumbered. Those are kept aside and claimed by the track that starts on top of them.
    Frame times are passed in, so the motion model follows real time on variable-frame-rate video.
    """

    def __init__(self, ref: str, fps: float, start_score: float, camera_motion: bool = False) -> None:
        import trackers

        options = {
            "frame_rate": fps,
            "lost_track_buffer": round(LOST_S * 30),  # the library counts this in 1/30 s
            "track_activation_threshold": start_score,
            "high_conf_det_threshold": start_score,
            "minimum_consecutive_frames": 1,
        }
        self._uses_frame = ref == "botsort" and camera_motion
        if ref == "botsort":
            options["enable_cmc"] = self._uses_frame
        self._tracker = getattr(trackers, TRACKER_CLASSES[ref])(**options)
        self._start_score = start_score
        self._tracks: dict[int, list[Sample]] = {}
        self._loose: list[Sample] = []

    def feed(self, f: int, t: float, detections: list[Det], image: np.ndarray | None = None) -> None:
        import supervision as sv

        found = sv.Detections.empty()
        if detections:
            found = sv.Detections(
                xyxy=np.array([[x, y, x + w, y + h] for (x, y, w, h), _ in detections], dtype=np.float32),
                confidence=np.array([score for _, score in detections], dtype=np.float32),
                data={"i": np.arange(len(detections))},
            )
        out = self._tracker.update(found, frame=image if self._uses_frame else None, timestamp=t)
        if len(out) == 0:
            return
        for number, i in zip(out.tracker_id.tolist(), out.data["i"].tolist()):
            sample = Sample(f, t, *detections[i])
            if number < 0:
                if sample.score >= self._start_score:
                    self._loose.append(sample)
            elif number in self._tracks:
                self._tracks[number].append(sample)
            else:
                first = self._claim(sample)
                self._tracks[number] = [first, sample] if first else [sample]

    def _claim(self, sample: Sample) -> Sample | None:
        """The unnumbered detection this track started from: the best-overlapping recent one."""
        recent = [s for s in self._loose if s.f < sample.f and sample.t - s.t <= LOST_S + 1e-6]
        best = max(recent, key=lambda s: (iou(s.box, sample.box), s.f), default=None)
        if best is None or iou(best.box, sample.box) < 0.2:  # the library's own first-association minimum
            return None
        self._loose.remove(best)
        return best

    def result(self) -> list[list[Sample]]:
        return [self._tracks[n] for n in sorted(self._tracks)] + [[s] for s in self._loose]


def tracker_factory(profile: ResolvedProfile, fps: float) -> Callable[[str], Tracker]:
    """`make(kind)` gives a fresh tracker for one (segment, kind)."""
    require(profile, "perception.tracker")
    chosen = candidate(profile, "perception.tracker")
    if chosen.adapter == "builtin":
        return lambda kind: IouTracker()
    try:
        import trackers
    except ImportError as error:
        raise PerceptionError("추적 패키지 trackers가 설치되어 있지 않습니다 (`uv sync --group models`).") from error
    if not hasattr(trackers, TRACKER_CLASSES[str(chosen.ref)]):
        raise PerceptionError(f"설치된 trackers 패키지에 '{chosen.ref}' 추적기가 없습니다. 프로필에서 다른 추적기를 고르세요.")
    # BoT-SORT's camera-motion compensation shifts every predicted box by the background's motion. That suits
    # large boxes; for faces and plates (tens of pixels) one bad estimate on a hand-held shot breaks the IoU
    # match and splits the track (team clip: 6 face tracks per shot instead of 3), so only people use it.
    return lambda kind: LibraryTracker(str(chosen.ref), fps, START_SCORE[kind], camera_motion=kind == "person")


def keep_track(samples: list[Sample], kind: str) -> bool:
    """Two or more samples with at least one confident detection, or a single very confident one."""
    best = max(s.score for s in samples)
    return best >= (START_SCORE[kind] if len(samples) >= 2 else SOLO_SCORE[kind])


def fill_gaps(samples: list[Sample], sampled: list[int], times: list[float]) -> list[Sample]:
    """Add interpolated boxes (score 0) at the sampled frames between two consecutive samples of a track, so
    that redaction does not blink off while a face is turned away for a moment."""
    out: list[Sample] = []
    for current, following in zip(samples, samples[1:]):
        out.append(current)
        for f in sampled[bisect.bisect_right(sampled, current.f) : bisect.bisect_left(sampled, following.f)]:
            out.append(Sample(f, times[f], lerp(current.box, following.box, (f - current.f) / (following.f - current.f)), 0.0))
    out.append(samples[-1])
    return out


def to_points(samples: list[Sample], asset: VideoAsset) -> list[TrackPoint]:
    """Track points in upright original pixels, clipped to the frame."""
    width, height = float(asset.meta.display_width), float(asset.meta.display_height)
    points = []
    for s in samples:
        x, y, w, h = asset.to_original(s.box)
        x0, y0, x1, y1 = max(0.0, x), max(0.0, y), min(width, x + w), min(height, y + h)
        box = (round(x0, 1), round(y0, 1), round(max(0.0, x1 - x0), 1), round(max(0.0, y1 - y0), 1))
        points.append(TrackPoint(f=s.f, t=s.t, box=box, score=round(s.score, 3)))
    return points


def build_tracks(found: list[tuple[str, str, list[Sample]]], asset: VideoAsset) -> list[Track]:
    """Number the tracks T1.. by first appearance and convert them to original pixels."""
    found = sorted(found, key=lambda item: (item[2][0].f, KINDS.index(item[1]), item[2][0].box))
    out: list[Track] = []
    for number, (segment, kind, samples) in enumerate(found, 1):
        scores = [s.score for s in samples if s.score > 0]
        out.append(Track(id=f"T{number}", kind=kind, segment=segment, points=to_points(samples, asset), quality=round(sum(scores) / len(scores), 3)))  # type: ignore[arg-type]
    return out


def detect_and_track(
    asset: VideoAsset,
    times: list[float],
    segments: list[SegmentInfo],
    profile: ResolvedProfile,
    *,
    progress: Progress | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Track]:
    require(profile, "perception.tracker", "perception.face_detector", "perception.object_detector", "perception.plate_detector")
    fps = float(profile.params["perception.analysis_fps"])
    make = tracker_factory(profile, fps)
    detectors = {"face": face_detector(profile).detect, "person": person_detector(profile).detect}
    plates = plate_detector(profile)
    if plates is not None:
        detectors["plate"] = lambda image, floor: [d for d in plates.detect(image, floor) if d[0][2] >= PLATE_MIN_ASPECT * d[0][3]]
    sampled = {s.id: video.sample_indices(times, fps, s.start_frame, s.end_frame) for s in segments}
    owner = {f: s.id for s in segments for f in sampled[s.id]}
    order = sorted(owner)
    trackers: dict[tuple[str, str], Tracker] = {}
    for done, (f, image) in enumerate(video.iter_proxy(asset, order, should_stop)):
        for kind, detect in detectors.items():
            key = (owner[f], kind)
            if key not in trackers:
                trackers[key] = make(kind)
            trackers[key].feed(f, times[f], detect(image, MIN_SCORE[kind]), image)
        if done % 12 == 0:
            tell(progress, f"얼굴 · 사람 · 번호판을 찾는 중 ({times[f]:.0f}초)", done / max(1, len(order)))
    if should_stop and should_stop():
        raise PerceptionCancelled("지각 처리가 중단되었습니다.")
    found: list[tuple[str, str, list[Sample]]] = []
    for (segment, kind), tracker in trackers.items():
        for samples in tracker.result():
            if keep_track(samples, kind):
                found.append((segment, kind, fill_gaps(samples, sampled[segment], times)))
    tracks = build_tracks(found, asset)
    tell(progress, f"트랙 {len(tracks)}개", 1.0)
    return tracks
