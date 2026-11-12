"""From decisions and analysis-rate tracks to the regions redacted on every original frame (design §6.2 · §6.3).

One rule set decides on which frames a target is covered, and everything that needs to know asks here: the
target's visible ranges (plan, review screen), the regions the renderer draws, the review preview and the
output check. A frame that shows the original where a mosaic was decided defeats the whole system, so every
rule errs on the side of covering:

- between two samples of a track the box is interpolated and never shrinks;
- a gap of at most BRIDGE_S inside one track, or between two nearby tracks of the same target in the same
  scene, is covered too: the detector lost the thing, the thing did not leave the picture;
- every appearance is held for `Timeline.pad` before its first and after its last sample (detection starts
  late and ends early), but never across a cut;
- a time range that starts or ends within SNAP_S of where an appearance starts or ends means that boundary
  (a model or a person writing "9.9" for a video that ends at 9.97);
- inside its ranges a decision is drawn on every frame: where the target has no tracked box (the user
  extended a range) the nearest box in time is held.

Time ranges are half-open and aligned to frames (see core/domain.py); `Timeline` is the only place that
decides whether a frame is inside one.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .domain import Box, Decision, Range, TrackPoint, VideoUnderstanding, merge_ranges

# enlargement of a tracked box before redaction (deface uses 1.3 for faces, EgoBlur 1.1)
SPATIAL_SCALE: dict[str, float] = {"face": 1.3, "body": 1.08, "text": 1.2, "plate": 1.25, "region": 1.0}
# how much coarser than a face's the mosaic is (ADR-0007: the level follows the target, the user cannot change
# it). A line of text stays readable at a block size that already hides a face of the same height, so text and
# plates get blocks 2.5 times as large.
STRENGTH: dict[str, float] = {"face": 1.0, "body": 1.0, "text": 2.5, "plate": 2.5, "region": 1.0}
BRIDGE_S = 1.5  # a target lost for at most this long is treated as still there
MIN_PAD_S = 0.2  # every appearance is held at least this long beyond its first and last sample
SNAP_S = 0.5  # a range boundary this close to an appearance boundary is moved onto it
TOLERANCE_S = 0.002  # ranges are stored in milliseconds; no frame is this short
SAME_THING = 3.0  # two appearances are bridged when their boxes are at most this many box sizes apart
MIN_REGION_PX = 4.0  # no region is smaller than this in either direction


@dataclass(frozen=True)
class Region:
    """One mosaic on one frame, in upright display pixels of the original."""

    x: float
    y: float
    w: float
    h: float
    shape: Literal["ellipse", "box"]
    ref: str = ""  # decision id
    strength: float = 1.0  # multiplies the mosaic block size

    def as_list(self) -> list[Any]:
        return [round(self.x, 1), round(self.y, 1), round(self.w, 1), round(self.h, 1), self.shape, self.ref, self.strength]


@dataclass(frozen=True)
class Timeline:
    """The original's frames in time: when each starts, where the video ends, and where its scenes are cut."""

    times: tuple[float, ...]  # start of every frame, seconds from the first
    duration: float  # end of the last frame
    interval: float  # seconds between analysis samples
    scenes: tuple[tuple[int, int], ...]  # (first frame, last frame) of every scene, in order

    @classmethod
    def build(cls, times: Sequence[float], duration: float, interval: float, scenes: Iterable[tuple[int, int]] = ()) -> Timeline:
        times = tuple(float(t) for t in times)
        last = len(times) - 1
        cuts = sorted({max(0, min(last, int(first))) for first, _ in scenes} | {0}) if times else []
        bounds = tuple((first, (cuts[i + 1] - 1) if i + 1 < len(cuts) else last) for i, first in enumerate(cuts))
        end = max(float(duration), times[-1] + 2 * TOLERANCE_S) if times else float(duration)
        return cls(times, end, float(interval), bounds)

    @property
    def count(self) -> int:
        return len(self.times)

    @property
    def pad(self) -> float:
        return max(2 * self.interval, MIN_PAD_S)

    def end(self, frame: int) -> float:
        """When the frame stops being shown."""
        return self.times[frame + 1] if frame + 1 < len(self.times) else self.duration

    def scene(self, frame: int) -> tuple[int, int]:
        starts = [first for first, _ in self.scenes]
        return self.scenes[max(0, bisect_right(starts, frame) - 1)]

    def frames(self, ranges: Iterable[Range]) -> list[int]:
        """Frames whose timestamp lies in one of the ranges."""
        found: set[int] = set()
        for start, end in ranges:
            found.update(range(bisect_left(self.times, start - TOLERANCE_S), bisect_left(self.times, end - TOLERANCE_S)))
        return sorted(found)

    def ranges(self, frames: Iterable[int]) -> list[Range]:
        """The time ranges that contain exactly these frames."""
        out: list[Range] = []
        first = previous = None
        for frame in sorted(set(frames)):
            if previous is not None and frame != previous + 1:
                out.append((self.times[first], self.end(previous)))
                first = None
            first = frame if first is None else first
            previous = frame
        if previous is not None:
            out.append((self.times[first], self.end(previous)))
        return merge_ranges(out)

    def align(self, ranges: Iterable[Range]) -> list[Range]:
        """The ranges widened to whole frames: every frame shown at any moment inside a range is included."""
        frames: set[int] = set()
        for start, end in ranges:
            if end - start <= TOLERANCE_S:
                continue
            first = max(0, bisect_right(self.times, start + TOLERANCE_S) - 1)  # the frame on screen at `start`
            if self.times and self.end(first) <= start + TOLERANCE_S:
                first += 1  # `start` is at or after the end of the video: no frame is on screen
            last = bisect_left(self.times, end - TOLERANCE_S) - 1
            frames.update(range(first, last + 1))
        return self.ranges(frames)

    def snap(self, ranges: Iterable[Range], visible: Sequence[Range]) -> list[Range]:
        """`ranges` as the renderer applies them: aligned to frames, and where a range overlaps a visible range
        and one of its boundaries lies within SNAP_S of that visible range's own, the boundary is moved
        outward onto it. Never smaller than what was given; a boundary moves at most SNAP_S."""
        out: list[Range] = []
        for start, end in ranges:
            if end - start <= TOLERANCE_S:
                continue
            new_start, new_end = start, end
            for seen_start, seen_end in visible:
                if end <= seen_start + TOLERANCE_S or start >= seen_end - TOLERANCE_S:
                    continue  # the range does not reach into this appearance
                if 0 < start - seen_start <= SNAP_S + TOLERANCE_S:
                    new_start = min(new_start, seen_start)
                if 0 < seen_end - end <= SNAP_S + TOLERANCE_S:
                    new_end = max(new_end, seen_end)
            out.append((new_start, new_end))
        return self.align(out)


def scale_box(box: Box, factor: float, width: float, height: float) -> Box:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    w2, h2 = w * factor, h * factor
    x0, y0 = max(0.0, cx - w2 / 2), max(0.0, cy - h2 / 2)
    x1, y1 = min(float(width), cx + w2 / 2), min(float(height), cy + h2 / 2)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def at_least(box: Box, size: float, width: float, height: float) -> Box:
    """The box grown around its centre to at least `size` pixels each way (a mosaic needs something to cover)."""
    x, y, w, h = box
    w2, h2 = max(w, size), max(h, size)
    x0 = min(max(0.0, x + w / 2 - w2 / 2), max(0.0, float(width) - w2))
    y0 = min(max(0.0, y + h / 2 - h2 / 2), max(0.0, float(height) - h2))
    return (x0, y0, min(w2, float(width)), min(h2, float(height)))


def _lerp(a: Box, b: Box, r: float) -> Box:
    mixed = tuple(a[i] + (b[i] - a[i]) * r for i in range(4))
    # never smaller than the smaller neighbour: grow around the interpolated centre
    w, h = max(mixed[2], min(a[2], b[2])), max(mixed[3], min(a[3], b[3]))
    cx, cy = mixed[0] + mixed[2] / 2, mixed[1] + mixed[3] / 2
    return (cx - w / 2, cy - h / 2, w, h)


def _near(a: Box, b: Box) -> bool:
    """Could `b` be where the thing in `a` went: centres at most SAME_THING box sizes apart."""
    reach = SAME_THING * max(a[2], a[3], b[2], b[3])
    return abs(a[0] + a[2] / 2 - b[0] - b[2] / 2) <= reach and abs(a[1] + a[3] / 2 - b[1] - b[3] / 2) <= reach


def _same(a: Box, b: Box) -> bool:
    return all(abs(a[i] - b[i]) <= 0.5 for i in range(4))


def cover(understanding: VideoUnderstanding, target_id: str, scope: str, timeline: Timeline) -> dict[int, list[Box]]:
    """Where redacting `scope` of the target puts a box, for every original frame it covers (unscaled boxes;
    usually one per frame, more when the target is tracked in two places at once)."""
    count = timeline.count
    runs: list[list[TrackPoint]] = []  # appearances: samples of one track without a long gap or a cut
    for track in understanding.tracks_for(target_id, scope):
        current: list[TrackPoint] = []
        for point in sorted(track.points, key=lambda p: p.f):
            if not 0 <= point.f < count:
                continue
            if current and (point.t - current[-1].t > BRIDGE_S + TOLERANCE_S or timeline.scene(point.f) != timeline.scene(current[-1].f)):
                runs.append(current)
                current = []
            current.append(point)
        if current:
            runs.append(current)
    out: dict[int, list[Box]] = {}

    def add(frame: int, box: Box) -> None:
        here = out.setdefault(frame, [])
        if not any(_same(box, other) for other in here):
            here.append(box)

    def between(a: TrackPoint, b: TrackPoint) -> None:
        for frame in range(a.f + 1, b.f):
            add(frame, _lerp(a.box, b.box, (frame - a.f) / (b.f - a.f)))

    def hold(point: TrackPoint, direction: int) -> None:
        first, last = timeline.scene(point.f)
        frame = point.f + direction
        while first <= frame <= last and abs(timeline.times[frame] - timeline.times[point.f]) <= timeline.pad + TOLERANCE_S:
            add(frame, point.box)
            frame += direction

    for run in runs:
        for a, b in zip(run, run[1:]):
            add(a.f, a.box)
            between(a, b)
        add(run[-1].f, run[-1].box)
        hold(run[0], -1)
        hold(run[-1], +1)
    for run in runs:  # the same thing picked up again by another track a moment later
        tail = run[-1]
        later = [other[0] for other in runs if other is not run and other[0].f > tail.f and other[0].t - tail.t <= BRIDGE_S + TOLERANCE_S]
        later = [head for head in later if timeline.scene(head.f) == timeline.scene(tail.f) and _near(tail.box, head.box)]
        if later:
            between(tail, min(later, key=lambda head: head.f))
    return out


def target_scopes(understanding: VideoUnderstanding) -> list[tuple[str, str]]:
    """Every (target id, scope) that can be redacted on screen."""
    pairs = [(person.id, scope) for person in understanding.persons for scope in ("face", "body")]
    pairs += [(text.id, "text") for text in understanding.texts]
    pairs += [(plate.id, "plate") for plate in understanding.plates]
    return pairs


def visible_ranges(understanding: VideoUnderstanding, timeline: Timeline) -> dict[str, list[Range]]:
    """"<target id>:<scope>" → the time ranges that redacting it covers (stored as `VideoUnderstanding.visible`)."""
    return {f"{target}:{scope}": timeline.ranges(cover(understanding, target, scope, timeline)) for target, scope in target_scopes(understanding)}


def seen(understanding: VideoUnderstanding, target_id: str, scope: str) -> list[Range]:
    """The stored visible ranges of one part of a target. A person without body tracks is covered by the face."""
    return [(float(a), float(b)) for a, b in understanding.visible.get(f"{target_id}:{scope}") or []]


def applied_ranges(decision: Decision, understanding: VideoUnderstanding, timeline: Timeline) -> list[Range]:
    """The decision's ranges as the renderer applies them."""
    return timeline.snap(decision.ranges, seen(understanding, decision.target_id, decision.scope))


def render_shape(decision: Decision) -> Literal["ellipse", "box"]:
    """Outline masks need segmentation (M5); until then an outline decision is drawn as its bounding shape."""
    if decision.shape == "ellipse":
        return "ellipse"
    if decision.shape == "outline":
        return "ellipse" if decision.scope == "face" else "box"
    return "box"


def build_regions(understanding: VideoUnderstanding, decisions: list[Decision], timeline: Timeline, *, width: int, height: int) -> dict[int, list[Region]]:
    """Regions per original frame index for every mosaic decision. Each frame inside a decision's applied
    ranges gets at least one region for it, as long as the target was tracked anywhere."""
    regions: dict[int, list[Region]] = {}
    for decision in decisions:
        if decision.method != "mosaic":
            continue
        boxes = cover(understanding, decision.target_id, decision.scope, timeline)
        if not boxes:
            continue
        tracked = sorted(boxes)
        factor = SPATIAL_SCALE.get(decision.scope, 1.1)
        shape, strength = render_shape(decision), STRENGTH.get(decision.scope, 1.0)
        for frame in timeline.frames(applied_ranges(decision, understanding, timeline)):
            here = boxes.get(frame)
            if here is None:  # inside the range but never tracked here: hold the nearest box in time
                at = bisect_left(tracked, frame)
                near = [tracked[i] for i in (at - 1, at) if 0 <= i < len(tracked)]
                here = boxes[min(near, key=lambda other: abs(timeline.times[other] - timeline.times[frame]))]
            for box in here:
                # a target tracked a pixel or two wide still gets a region: every frame of the range is covered
                x, y, w, h = at_least(scale_box(box, factor, width, height), MIN_REGION_PX, width, height)
                regions.setdefault(frame, []).append(Region(x, y, w, h, shape, decision.id, strength))
    return regions


def expected_frames(understanding: VideoUnderstanding, decisions: list[Decision], timeline: Timeline) -> dict[str, list[int]]:
    """Decision id → the frames its mosaic must be on, worked out from the plan alone (the output check
    compares this with what the renderer was given and with the pixels it wrote)."""
    out: dict[str, list[int]] = {}
    for decision in decisions:
        if decision.method == "mosaic" and understanding.tracks_for(decision.target_id, decision.scope):
            out[decision.id] = timeline.frames(applied_ranges(decision, understanding, timeline))
    return out


def regions_to_json(regions: dict[int, list[Region]]) -> dict[str, list[list[Any]]]:
    return {str(frame): [r.as_list() for r in items] for frame, items in sorted(regions.items())}


def regions_from_json(data: dict[str, list[list[Any]]]) -> dict[int, list[Region]]:
    return {int(frame): [Region(*item) for item in items] for frame, items in data.items()}
