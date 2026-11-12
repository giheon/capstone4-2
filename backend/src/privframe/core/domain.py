"""Domain model of a case (design §8, GLOSSARY.md).

Three conventions hold everywhere:
- **Time** is seconds on the original video's timeline, counted from its first frame (`(pts - first_pts) * time_base`).
  The frame index is the join key between the original and the analysis proxy (frame i ↔ frame i).
- **A time range** is half-open, `[start, end)`: a frame belongs to it when its timestamp does. A range the system
  writes is aligned to frames, so it ends where the frame after its last frame begins (the video's end for the
  last frame). Membership is decided in one place, `geometry.Timeline`.
- **Geometry** is in upright display pixels of the original video (rotation applied), as `(x, y, w, h)`.

Objects the web app reads are serialized with camelCase aliases, so `model_dump(by_alias=True)` is exactly the
API contract in frontend/src/api/contract.ts.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Box = tuple[float, float, float, float]
Range = tuple[float, float]
# What a decision does to its target. Things on screen are either kept or covered with a mosaic (the only
# redaction: its level is fixed per target kind, ADR-0007). File metadata is either kept or removed.
Method = Literal["keep", "mosaic", "remove"]
Shape = Literal["ellipse", "box", "outline"]
Scope = Literal["face", "body", "text", "plate", "region", "metadata"]
TrackKind = Literal["face", "person", "plate", "text"]
TargetKind = Literal["person", "plate", "text", "region", "metadata"]
REDACT_METHOD: dict[str, str] = {"person": "mosaic", "text": "mosaic", "plate": "mosaic", "region": "mosaic", "metadata": "remove"}  # by target kind
Provenance = Literal["declared", "answered", "inferred"]
Applicability = Literal["applies", "not_applies", "uncertain"]


class Model(BaseModel):
    """Internal objects (snake_case)."""

    model_config = ConfigDict(extra="ignore")


class Contract(BaseModel):
    """Objects the web app reads: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


# ---------- video ----------


class VideoMeta(Model):
    """What ffprobe says about the preserved original (design §6.2)."""

    path: str
    filename: str
    sha256: str
    size_bytes: int
    container: str
    duration_s: float
    frame_count: int
    width: int  # coded size
    height: int
    display_width: int  # upright size after rotation
    display_height: int
    rotation: int = 0  # degrees clockwise needed to display upright (0 · 90 · 180 · 270)
    codec: str
    profile: str = ""
    level: int = 0
    pix_fmt: str = ""
    bit_depth: int = 8
    color: dict[str, str] = Field(default_factory=dict)
    time_base: tuple[int, int]
    avg_frame_rate: str = ""
    vfr: bool = False
    audio: str = "없음"
    stream_count: int = 1
    first_pts: int = 0
    # names of the container and stream tags that record where the video was shot (GPS coordinates)
    location_tags: list[str] = Field(default_factory=list)


class Proxy(Model):
    """Analysis-only copy: same frame count and timestamps, upright, scaled down (design §6.2)."""

    path: str
    width: int
    height: int
    scale: float  # proxy pixels per original display pixel
    frame_count: int
    pts_identical: bool


class VideoAsset(Model):
    meta: VideoMeta
    proxy: Proxy
    pts_path: str  # JSON list of the original frame pts, index = frame number

    def to_original(self, box: Box) -> Box:
        s = 1.0 / self.proxy.scale
        return (box[0] * s, box[1] * s, box[2] * s, box[3] * s)


# ---------- perception ----------


class TrackPoint(Model):
    f: int  # original frame index
    t: float
    box: Box
    score: float = 1.0


class Track(Model):
    id: str
    kind: TrackKind
    segment: str = ""
    points: list[TrackPoint]
    text: str | None = None  # recognized text of a text track
    quality: float = 1.0

    @property
    def start(self) -> float:
        return self.points[0].t

    @property
    def end(self) -> float:
        return self.points[-1].t


class PersonGroup(Model):
    id: str
    face_tracks: list[str] = Field(default_factory=list)
    body_tracks: list[str] = Field(default_factory=list)
    # filled by describe_persons (VLM) and by cue propagation from attached text
    attributes: dict[str, Any] = Field(default_factory=dict)
    cues: list[str] = Field(default_factory=list)
    prominence: float = 0.0


class TextItem(Model):
    id: str
    text: str
    kind: Literal["nametag", "sign", "caption", "document", "screen", "other"] = "other"
    tracks: list[str] = Field(default_factory=list)
    attached_person: str | None = None
    confidence: float = 0.0


class Plate(Model):
    id: str
    tracks: list[str] = Field(default_factory=list)
    text: str | None = None


class SegmentInfo(Model):
    id: str
    start: float
    end: float
    start_frame: int
    end_frame: int  # inclusive
    keyframes: list[int] = Field(default_factory=list)
    label: str = ""
    # scene description from the VLM: place_type, setting, activity, people_count, minors_present,
    # sensitivity_flags, camera_awareness, visible_text_summary, summary_ko, confidence
    description: dict[str, Any] = Field(default_factory=dict)


class IdentityAmbiguity(Model):
    """Two person groups the grouping step could not confidently merge or separate (design §5.7 await_identity)."""

    id: str
    a: str
    b: str
    similarity: float
    at_a: float
    at_b: float


class VideoUnderstanding(Model):
    """Structured output of the perceive subgraph (design §7.2)."""

    segments: list[SegmentInfo] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)
    persons: list[PersonGroup] = Field(default_factory=list)
    texts: list[TextItem] = Field(default_factory=list)
    plates: list[Plate] = Field(default_factory=list)
    ambiguities: list[IdentityAmbiguity] = Field(default_factory=list)
    summary_ko: str = ""
    provenance: dict[str, str] = Field(default_factory=dict)
    sample_interval: float = 1 / 12  # seconds between the analysis samples the tracks were made from
    # "<target id>:<scope>" → the time ranges redacting that part of the target covers (geometry.visible_ranges).
    # Derived from the tracks once, so that the plan, the review screen and the renderer agree on every frame.
    visible: dict[str, list[Range]] = Field(default_factory=dict)

    def track(self, track_id: str) -> Track | None:
        return next((t for t in self.tracks if t.id == track_id), None)

    def tracks_for(self, target_id: str, scope: str) -> list[Track]:
        """The tracks a decision on `target_id` with `scope` redacts."""
        ids: list[str] = []
        for person in self.persons:
            if person.id == target_id:
                # redacting the body includes the face: where the body was not detected, the face still is covered
                ids = [*person.body_tracks, *person.face_tracks] if scope == "body" else person.face_tracks
        for text in self.texts:
            if text.id == target_id:
                ids = text.tracks
        for plate in self.plates:
            if plate.id == target_id:
                ids = plate.tracks
        wanted = set(ids)
        return [t for t in self.tracks if t.id in wanted]


# ---------- web contract objects ----------


class Citation(Contract):
    id: str
    label: str
    authority: Literal[1, 2, 3]
    kind: str
    note: str | None = None
    placeholder: bool | None = None


class FactOption(Contract):
    value: str
    label: str


class Fact(Contract):
    key: str
    label: str
    value: str
    value_label: str
    provenance: Provenance
    confidence: float | None = None
    source: str
    editable: bool = True
    options: list[FactOption] | None = None


class Target(Contract):
    id: str
    kind: TargetKind
    label: str
    detail: str
    ranges: list[Range]
    cues: list[str] = Field(default_factory=list)
    attached_to: str | None = None
    tracks: list[str] = Field(default_factory=list)
    personal: bool = True


class QuestionOption(Contract):
    value: str
    label: str
    detail: str | None = None


class Impact(Contract):
    option: str
    effect: str


class Question(Contract):
    id: str
    fact_key: str
    round: int
    prompt: str
    note: str | None = None
    options: list[QuestionOption]
    basis: str
    citations: list[Citation] = Field(default_factory=list)
    impact: list[Impact] = Field(default_factory=list)
    status: Literal["pending", "answered", "deferred", "removed"] = "pending"
    answer: str | None = None
    removed_reason: str | None = None
    targets: list[str] = Field(default_factory=list)
    frame_at: float | None = None


class IdentityCandidate(Contract):
    label: str
    at: float
    target: str
    note: str


class IdentityQuestion(Contract):
    id: str
    prompt: str
    detail: str
    candidates: list[IdentityCandidate]
    options: list[QuestionOption]
    status: Literal["pending", "answered"] = "pending"
    answer: str | None = None


class Analysis(Contract):
    id: str
    question: str
    applicability: Applicability
    reasoning: str
    citations: list[Citation] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)


class Requirement(Contract):
    id: str
    statement: str
    targets: list[str]
    ranges: list[Range] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    # which part of each target must be unidentifiable; the planner turns this into decisions
    scope: Scope = "face"


class Decision(Contract):
    id: str
    target_id: str
    target_label: str
    scope: Scope
    method: Method
    shape: Shape | None = None  # none for metadata, which has no place on screen
    ranges: list[Range]
    satisfies: list[str] = Field(default_factory=list)
    analysis_id: str | None = None
    rationale: str
    citations: list[Citation] = Field(default_factory=list)
    similar_cases: list[Citation] = Field(default_factory=list)
    risk_if_changed: str = ""
    risk: Literal["high", "medium", "low", "uncertain"] = "medium"
    source: Literal["planner", "user", "auditor"] = "planner"
    edited: bool = False


class Unsatisfied(Contract):
    requirement_id: str
    reason: str


class Plan(Contract):
    version: int = 1
    decisions: list[Decision] = Field(default_factory=list)
    unsatisfied: list[Unsatisfied] = Field(default_factory=list)


class Finding(Contract):
    id: str
    type: Literal["residual_identifier", "requirement_unmet", "over_redaction", "inconsistency"]
    severity: Literal["high", "medium", "low"]
    title: str
    detail: str
    target_id: str
    target_label: str
    ranges: list[Range] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    recommendation: str
    status: Literal["open", "applied", "rejected"] = "open"
    reject_reason: str | None = None
    # what applying the recommendation does to the plan (not part of the web contract)
    fix: dict[str, Any] | None = Field(default=None, exclude=True)


class VerificationRow(Contract):
    check: str
    original: str
    output: str
    verdict: Literal["pass", "fail", "info"]
    note: str | None = None


class ScriptRow(Contract):
    start: float
    end: float
    scene: str
    target: str
    treatment: str
    basis: str


METHOD_LABEL: dict[str, str] = {"keep": "유지", "mosaic": "모자이크", "remove": "삭제"}
SHAPE_LABEL: dict[str, str] = {"ellipse": "타원", "box": "사각형", "outline": "윤곽"}
SCOPE_LABEL: dict[str, str] = {"face": "얼굴", "body": "전신", "text": "글자", "plate": "번호판", "region": "영역", "metadata": "메타데이터"}
AUTHORITY_LABEL: dict[int, str] = {1: "규범", 2: "해석", 3: "참고"}


def merge_ranges(ranges: list[Range], gap: float = 0.0) -> list[Range]:
    """Sorted union of time ranges; ranges closer than `gap` seconds are joined. Times keep millisecond
    precision (a frame lasts at least a few milliseconds, so rounding never moves a boundary across a frame)."""
    out: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in ranges if b >= a):
        if out and start <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def clip_ranges(ranges: list[Range], within: list[Range]) -> list[Range]:
    """The parts of `ranges` that fall inside `within`."""
    out: list[Range] = []
    for a, b in ranges:
        for c, d in within:
            lo, hi = max(a, c), min(b, d)
            if hi > lo:
                out.append((lo, hi))
    return merge_ranges(out)
