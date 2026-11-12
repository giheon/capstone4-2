"""Checks around redaction, where a missed face matters most.

- `face_finder`: low-threshold face boxes, used to blur every face before a frame goes to a cloud VLM (design
  §7.3 V-2).
- `output_checker`: output verification (design §6.4). Two questions about the rendered video:
  inside the redacted areas, can the thing still be *identified* (a face that still matches the original face, a
  text that can still be read)? A mosaic is usually still *detected* as a face, so detection alone would fail
  every correctly redacted video. And anywhere else, on every frame: does a face of a person who was to be
  redacted, or a text that was to be redacted, show in the clear? That second check does not use the regions,
  the tracks or the analysis sampling grid at all, so it also catches what tracking missed.
- `redetector`: the plain "is anything detected inside a region" count, kept for comparison.

Face features computed here are compared and dropped with the checker; nothing is written to disk.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

import cv2
import numpy as np

from privframe.core import video
from privframe.core.domain import Box, Decision, Track, VideoAsset, VideoUnderstanding
from privframe.core.geometry import Region, Timeline, applied_ranges, seen
from privframe.core.pipeline import ResolvedProfile
from privframe.core.verify import Leak

from .common import PerceptionError, centre, iou
from .identity import MERGE_SIMILARITY
from .models import FaceDetector, FaceEmbedder, TextReader, face_detector, face_embedder, plate_detector, text_reader
from .text import MIN_TEXT_SCORE, normalize, similarity

# Recall first: these uses must notice a face or plate the analysis thresholds (0.5 · 0.3) would skip.
LOW_FACE_SCORE = 0.3
LOW_PLATE_SCORE = 0.25
READABLE_SIMILARITY = 0.5  # a redacted text counts as still readable when OCR gets half of the original back
# A redacted face counts as still identifiable when its feature is as close to the original's as the grouping
# step needs to call two tracks the same person. Measured in M4 (SFace, 289 redacted faces of the team clip and
# the sample): the similarity of a redacted face to its original stays at or below 0.37 whatever the blur
# width (0.30× to 0.60×) or mosaic size (4 to 6 blocks) — a floor set by head shape, hair and skin tone, far
# from the 0.82–0.99 of the same person unredacted. A bound inside that floor (0.30, or SFace's own 0.363)
# fails correctly redacted videos at random; an uncovered or barely blurred face is well above 0.5.
IDENTIFIABLE_SIMILARITY = MERGE_SIMILARITY
EXPOSED_SIMILARITY = 0.8  # a text read elsewhere on the frame counts as the redacted one only when it is nearly the same
REFERENCE_FACES = 5  # original faces per redacted person the whole-frame scan compares against
TEXT_SCAN_FRAMES = 24  # frames read in full for redacted text: the frames around every range first, then an even spread
TEXT_MIN_ASPECT = 1.2  # box regions at least this wide for their height are read as text or plates …
TEXT_MAX_AREA = 0.02  # … and so are small boxes of any shape (share of the frame); large upright boxes are bodies

Finder = Callable[[np.ndarray], list[Box]]  # boxes in pixels of the image it is given


def in_region(point: tuple[float, float], region: Region) -> bool:
    if region.w <= 0 or region.h <= 0:
        return False
    dx, dy = point[0] - (region.x + region.w / 2), point[1] - (region.y + region.h / 2)
    if region.shape == "ellipse":
        return (dx / (region.w / 2)) ** 2 + (dy / (region.h / 2)) ** 2 <= 1.0
    return abs(dx) <= region.w / 2 and abs(dy) <= region.h / 2


def count_inside(boxes: Iterable[Box], regions: Sequence[Region]) -> int:
    """How many boxes have their centre inside at least one region."""
    return sum(1 for box in boxes if any(in_region(centre(box), region) for region in regions))


def _reduced(frame: np.ndarray, ratio: float) -> np.ndarray:
    if ratio >= 1.0:
        return frame
    return cv2.resize(frame, (round(frame.shape[1] * ratio), round(frame.shape[0] * ratio)), interpolation=cv2.INTER_AREA)


def face_finder(profile: ResolvedProfile, *, detector: FaceDetector | None = None) -> Finder:
    """`find(image)` returns the faces of a BGR image as `(x, y, w, h)` in that image's own pixels, at a low
    threshold. Used to blur every face before a frame is sent to a cloud VLM. The returned function may be
    called from several threads and with images of different sizes: the shared detector sets its input size
    and runs under one lock per call. `detector` replaces the profile's face detector (tests)."""
    faces = detector or face_detector(profile)
    return lambda image: [box for box, _ in faces.detect(image, LOW_FACE_SCORE)]


def redetector(profile: ResolvedProfile, *, finders: Sequence[Finder] | None = None) -> Callable[[np.ndarray, int, Sequence[Region]], int]:
    """`check(frame, frame_index, regions)` returns how many faces (and plates, when the profile detects
    them) are still *detected* with their centre inside a redacted region of a rendered frame.

    A mosaic rarely brings this to zero (a mosaicked face often still looks like a face to a detector); use
    `output_checker` to decide whether a render passes. The frame is the upright output at original size; it is reduced to the
    analysis proxy size first. `finders` replaces the profile's detectors (tests).
    """
    short_side = float(profile.params.get("perception.proxy_short_side", 640))
    if finders is None:
        plates = plate_detector(profile)
        finders = [face_finder(profile)]
        if plates is not None:
            finders.append(lambda image: [box for box, _ in plates.detect(image, LOW_PLATE_SCORE)])

    def check(frame: np.ndarray, frame_index: int, regions: Sequence[Region]) -> int:
        if not regions:
            return 0
        ratio = min(1.0, short_side / min(frame.shape[:2]))
        found = [(x / ratio, y / ratio, w / ratio, h / ratio) for find in finders for x, y, w, h in find(_reduced(frame, ratio))]
        return count_inside(found, regions)

    return check


# ---------- is it still identifiable? ----------


def _face_rows(frame: np.ndarray, faces: FaceDetector, ratio: float) -> list[np.ndarray]:
    """Low-threshold YuNet rows (box, landmarks, score) in pixels of `frame`, detected at proxy size."""
    out = []
    for row in faces.rows(_reduced(frame, ratio), LOW_FACE_SCORE):
        row = np.array(row, dtype=np.float32)
        row[:14] /= ratio
        out.append(row)
    return out


def remaining_faces(
    output: np.ndarray, original: np.ndarray, regions: Sequence[Region], faces: FaceDetector, embedder: FaceEmbedder, short_side: float = 640.0
) -> list[float]:
    """For every original face that is still detected inside a redacted region of the output: the cosine
    similarity between the face as it looks in the output and in the original (each aligned by its own
    landmarks, at full resolution). A detection with no face at the same place in the original, or one that
    cannot be embedded, is left out."""
    ratio = min(1.0, short_side / min(output.shape[:2]))
    shown = [row for row in _face_rows(output, faces, ratio) if any(in_region(centre(tuple(row[:4])), r) for r in regions)]
    if not shown:
        return []
    before = _face_rows(original, faces, ratio)
    best: dict[int, float] = {}
    for row in shown:
        box = tuple(float(v) for v in row[:4])
        overlaps = [iou(box, tuple(float(v) for v in other[:4])) for other in before]
        index = max(range(len(before)), key=lambda i: overlaps[i], default=None)
        if index is None or overlaps[index] < 0.3:  # blur can move the detected box: fall back to the nearest centre
            (cx, cy), reach = centre(box), 0.5 * max(box[2], box[3])
            near = [(float(np.hypot(cx - o[0] - o[2] / 2, cy - o[1] - o[3] / 2)), i) for i, o in enumerate(before)]
            index = min(near)[1] if near and min(near)[0] <= reach else None
        if index is None:
            continue
        try:
            value = float(embedder.feature(output, row) @ embedder.feature(original, before[index]))
        except (PerceptionError, cv2.error):
            continue
        best[index] = max(best.get(index, -1.0), value)
    return [best[index] for index in sorted(best)]


def looks_like_text(region: Region, frame_area: float) -> bool:
    """Box regions that can hold a line of text or a plate. Ellipses are faces, large upright boxes are bodies."""
    if region.shape != "box" or region.w < 8 or region.h < 8:
        return False
    return region.w >= TEXT_MIN_ASPECT * region.h or region.w * region.h <= TEXT_MAX_AREA * frame_area


def _padded(crop: np.ndarray, side: int = 320) -> np.ndarray:
    """The crop on a gray canvas: text detection fails on a crop that is nothing but one large text."""
    h, w = crop.shape[:2]
    canvas = np.full((max(side, 3 * h), max(side, w + 2 * h), 3), 127, dtype=np.uint8)
    y, x = (canvas.shape[0] - h) // 2, (canvas.shape[1] - w) // 2
    canvas[y : y + h, x : x + w] = crop
    return canvas


def remaining_texts(output: np.ndarray, original: np.ndarray, regions: Sequence[Region], reader: TextReader) -> list[float]:
    """For every text-like region whose original content can be read (2+ characters): how much of that reading
    OCR still gets from the output (0..1, normalized text similarity).

    The region is read as one line first (recognition only: about 10 ms and the more sensitive reader); when
    that gives nothing for the original it is read as a block with text detection (two-line plates). The
    output is read the same way as the original. Regions covering the same pixels are read once.
    """
    height, width = output.shape[:2]
    out: list[float] = []
    seen: set[tuple[int, int, int, int]] = set()
    for region in regions:
        x0, y0 = max(0, int(region.x)), max(0, int(region.y))
        x1, y1 = min(width, int(region.x + region.w + 0.5)), min(height, int(region.y + region.h + 0.5))
        if not looks_like_text(region, float(width * height)) or x1 - x0 < 8 or y1 - y0 < 8 or (x0, y0, x1, y1) in seen:
            continue
        seen.add((x0, y0, x1, y1))
        before, after = np.ascontiguousarray(original[y0:y1, x0:x1]), np.ascontiguousarray(output[y0:y1, x0:x1])
        text, score = reader.read_line(before)
        if score >= MIN_TEXT_SCORE and len(normalize(text)) >= 2:
            wanted, got = [text], [reader.read_line(after)[0]]
        else:
            wanted = [t for _, t, _ in reader.read(_padded(before)) if len(normalize(t)) >= 2]
            if not wanted:
                continue  # nothing readable was there in the first place
            got = [t for _, t, _ in reader.read(_padded(after))]
            got.append(" ".join(got))
        out.append(max((similarity(a, b) for a in wanted for b in got), default=0.0))
    return out


def face_references(asset: VideoAsset, tracks: Sequence[Track], faces: FaceDetector, embedder: FaceEmbedder, short_side: float = 640.0, count: int = REFERENCE_FACES) -> list[np.ndarray]:
    """Features of a person's face taken from the original: the largest samples, at least half a second apart."""
    points = sorted((p for track in tracks for p in track.points), key=lambda p: -(p.box[2] * p.box[3]))
    chosen: list[float] = []
    out: list[np.ndarray] = []
    for point in points[: 6 * count]:
        if len(out) >= count:
            break
        if any(abs(point.t - t) < 0.5 for t in chosen):
            continue
        frame = video.read_frame(asset, point.f, "original")
        ratio = min(1.0, short_side / min(frame.shape[:2]))
        rows = _face_rows(frame, faces, ratio)
        best = max(rows, key=lambda row: iou(point.box, tuple(float(v) for v in row[:4])), default=None)
        if best is None or iou(point.box, tuple(float(v) for v in best[:4])) < 0.3:
            continue
        try:
            out.append(embedder.feature(frame, best))
            chosen.append(point.t)
        except (PerceptionError, cv2.error):
            continue
    return out


def output_checker(
    profile: ResolvedProfile,
    asset: VideoAsset,
    understanding: VideoUnderstanding,
    decisions: Sequence[Decision],
    timeline: Timeline,
    *,
    faces: FaceDetector | None = None,
    embedder: FaceEmbedder | None = None,
    reader: TextReader | None = None,
) -> OutputChecker:
    """The checker for one rendered case: what the plan redacts is what must not be recognisable.

    Uses the profile's face detector, face embedding and OCR (the arguments replace them in tests); raises
    PerceptionError when the profile names a part this backend cannot run.
    """
    faces, embedder, reader = faces or face_detector(profile), embedder or face_embedder(profile), reader or text_reader(profile)
    short_side = float(profile.params.get("perception.proxy_short_side", 640))
    references: dict[str, tuple[str, list[np.ndarray]]] = {}
    texts: dict[str, tuple[str, str]] = {}
    around: set[int] = set()
    allowed: dict[str, set[int]] = {}
    for decision in decisions:
        if decision.method != "mosaic":
            continue
        # frames where the target is known to be visible and the plan does not redact it (a requirement limited
        # in time, a range the reviewer shortened): seeing it there is what was decided, not a leak
        shown = set(timeline.frames(seen(understanding, decision.target_id, decision.scope))) - set(timeline.frames(applied_ranges(decision, understanding, timeline)))
        if shown:
            allowed[decision.id] = shown
        if decision.scope in ("face", "body"):
            person = next((p for p in understanding.persons if p.id == decision.target_id), None)
            tracks = [t for t in understanding.tracks if person is not None and t.id in set(person.face_tracks)]
            features = face_references(asset, tracks, faces, embedder, short_side) if tracks else []
            if features:
                references[decision.id] = (decision.target_label, features)
            continue
        text = next((t.text for t in understanding.texts if t.id == decision.target_id), None) or next((p.text for p in understanding.plates if p.id == decision.target_id), None)
        if text and len(normalize(text)) >= 2:
            texts[decision.id] = (decision.target_label, text)
            for start, end in applied_ranges(decision, understanding, timeline):
                inside = timeline.frames([(start, end)])
                if inside:
                    around.update(f for f in (inside[0] - 1, inside[0], inside[-1], inside[-1] + 1) if 0 <= f < timeline.count)
    text_frames: list[int] = []
    if texts and timeline.count:
        spread = TEXT_SCAN_FRAMES - min(len(around), TEXT_SCAN_FRAMES // 2)
        even = {round(k * (timeline.count - 1) / max(1, spread - 1)) for k in range(spread)}
        text_frames = sorted(set(sorted(around)[: TEXT_SCAN_FRAMES // 2]) | even)
    return OutputChecker(faces, embedder, reader, short_side, references=references, texts=texts, text_frames=text_frames, frame_count=timeline.count, allowed=allowed)


class OutputChecker:
    """See `output_checker`. `inside` and `scan` are called frame by frame by core/verify.py; `leaks` and the
    highest similarities seen are read afterwards."""

    def __init__(self, faces: FaceDetector, embedder: FaceEmbedder, reader: TextReader, short_side: float = 640.0, *,
                 references: dict[str, tuple[str, list[np.ndarray]]] | None = None, texts: dict[str, tuple[str, str]] | None = None,
                 text_frames: Sequence[int] = (), frame_count: int = 0, allowed: dict[str, set[int]] | None = None):  # fmt: skip
        self.faces, self.embedder, self.reader, self.short_side = faces, embedder, reader, short_side
        self.references = references or {}  # decision id → (target label, original face features)
        self.texts = texts or {}  # decision id → (target label, the text)
        self.allowed = allowed or {}  # decision id → frames on which the plan leaves its target visible
        self.text_frames = sorted(text_frames)
        self.frame_count = frame_count
        self.leaks: list[Leak] = []
        self.worst_face: float | None = None  # inside the regions, against the same frame of the original
        self.worst_text: float | None = None
        self.worst_seen: float | None = None  # anywhere else, against the redacted persons' reference faces
        self.texts_read = 0

    def inside(self, output: np.ndarray, original: np.ndarray, frame: int, regions: Sequence[Region]) -> int:
        """How many redacted things are still identifiable inside the regions of one frame (upright BGR frames
        at original size; regions in original pixels).

        - A face counts when it is still detected inside a region of the output and its feature matches the face
          at the same place in the original with cosine similarity ≥ IDENTIFIABLE_SIMILARITY.
        - A text or plate counts when the original region can be read and OCR still gets at least
          READABLE_SIMILARITY of that reading from the output.
        """
        if not regions:
            return 0
        face_values = [float(v) for v in remaining_faces(output, original, regions, self.faces, self.embedder, self.short_side)]
        text_values = [float(v) for v in remaining_texts(output, original, regions, self.reader)]
        if face_values:
            self.worst_face = max([*face_values, self.worst_face if self.worst_face is not None else -1.0])
        if text_values:
            self.worst_text = max([*text_values, self.worst_text if self.worst_text is not None else 0.0])
        return sum(v >= IDENTIFIABLE_SIMILARITY for v in face_values) + sum(v >= READABLE_SIMILARITY for v in text_values)

    def scan(self, frame: int, small: np.ndarray, scale: float, full: Callable[[], tuple[np.ndarray, np.ndarray]], regions: Sequence[Region]) -> None:
        """Look at a whole output frame, outside the regions, for a redacted person's face (every frame) and
        for a redacted text (the frames in `text_frames`). `small` is the frame reduced for detection and
        `scale` turns its pixels into original pixels; `full()` gives the full-size output and original."""
        if self.references:
            rows = [np.array(row, dtype=np.float32) for row in self.faces.rows(small, LOW_FACE_SCORE)]
            output = full()[0] if rows else None
            for row in rows:
                row[:14] *= scale
                box = tuple(float(v) for v in row[:4])
                if any(in_region(centre(box), region) for region in regions):
                    continue  # a mosaic that still looks like a face: `inside` decides whether it is identifiable
                try:
                    feature = self.embedder.feature(output, row)
                except (PerceptionError, cv2.error):
                    continue
                for ref, (label, vectors) in self.references.items():
                    if frame in self.allowed.get(ref, ()):
                        continue
                    value = max(float(feature @ vector) for vector in vectors)
                    self.worst_seen = max(value, self.worst_seen if self.worst_seen is not None else -1.0)
                    if value >= IDENTIFIABLE_SIMILARITY:
                        self.leaks.append(Leak(frame, ref, box, f"{label} 얼굴 (유사도 {value:.2f})", (frame, frame)))
        if self.texts and frame in self.text_frames:
            self.texts_read += 1
            at = self.text_frames.index(frame)
            span = (self.text_frames[at - 1] + 1 if at else 0, self.text_frames[at + 1] - 1 if at + 1 < len(self.text_frames) else max(frame, self.frame_count - 1))
            for found, text, score in self.reader.read(small):
                if score < MIN_TEXT_SCORE or len(normalize(text)) < 2:
                    continue
                box = (found[0] * scale, found[1] * scale, found[2] * scale, found[3] * scale)
                if any(in_region(centre(box), region) for region in regions):
                    continue
                for ref, (label, wanted) in self.texts.items():
                    if frame in self.allowed.get(ref, ()):
                        continue
                    if similarity(text, wanted) >= EXPOSED_SIMILARITY or normalize(wanted) in normalize(text):
                        self.leaks.append(Leak(frame, ref, box, f"{label} 판독됨", span))

    def summary(self) -> tuple[str, str]:
        """Notes for the two verification rows: (inside the regions, the whole-frame scan)."""
        inside = []
        if self.worst_face is not None:
            inside.append(f"얼굴 대조 최대 유사도 {self.worst_face:.2f} (기준 {IDENTIFIABLE_SIMILARITY:.2f} 미만)")
        if self.worst_text is not None:
            inside.append(f"글자 재인식 최대 일치 {self.worst_text:.2f} (기준 {READABLE_SIMILARITY:.2f} 미만)")
        seen = []
        if self.references:
            closest = f" · 영역 밖 얼굴과의 최대 유사도 {self.worst_seen:.2f} (기준 {IDENTIFIABLE_SIMILARITY:.2f} 미만)" if self.worst_seen is not None else ""
            seen.append(f"가린 인물 {len(self.references)}명의 원본 얼굴과 모든 프레임의 얼굴을 대조{closest}")
        if self.texts:
            seen.append(f"가린 글자 {len(self.texts)}건을 {self.texts_read}개 프레임 전체에서 판독")
        if not self.references and not self.texts:
            seen.append("대조할 얼굴 · 글자가 없음")
        return " · ".join(inside), " · ".join(seen)
