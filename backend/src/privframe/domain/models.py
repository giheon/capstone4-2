from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(StrEnum):
    KEEP = "KEEP"
    BLUR = "BLUR"
    MOSAIC = "MOSAIC"
    MASK = "MASK"


class Treatment(Contract):
    action: Action
    strength: int | None = Field(default=None, ge=1, le=3)

    @model_validator(mode="after")
    def supported_combination(self):
        if self.action == Action.KEEP and self.strength is not None:
            raise ValueError("KEEP does not take strength")
        if self.action != Action.KEEP and self.strength is None:
            raise ValueError("Redaction requires strength 1, 2, or 3")
        return self


class ScenarioInput(Contract):
    context: str = Field(min_length=1, max_length=10000)
    facts: dict[str, str] = Field(default_factory=dict)
    track_labels: dict[str, str] = Field(default_factory=dict)


class SourceMetadata(Contract):
    title: str
    source_url: str | None = None
    kind: str = "uploaded"
    publisher: str | None = None
    effective_from: str | None = None
    edition: str | None = None
    published_at: str | None = None
    version: str | None = None

    @field_validator("source_url")
    @classmethod
    def public_url(cls, value):
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("source_url must be an HTTP(S) URL")
        return value


class SourceUnit(Contract):
    text: str
    page: int | None = None
    article: str | None = None


class DocumentRecord(Contract):
    id: str
    filename: str
    format: str
    content_hash: str
    metadata: SourceMetadata
    created_at: str = Field(default_factory=now)
    units: list[SourceUnit] = Field(default_factory=list)
    status: str = "UPLOADED"
    error: str | None = None


class Chunk(Contract):
    id: str
    document_id: str
    text: str
    page: int | None = None
    article: str | None = None
    offset: int = 0
    metadata: SourceMetadata
    content_hash: str


class Evidence(Chunk):
    score: float | None = None
    index_id: str


class EvidenceBundle(Contract):
    query: str
    index_id: str
    candidates: list[Evidence] = Field(default_factory=list)
    provided: list[Evidence] = Field(default_factory=list)
    retrieved_at: str = Field(default_factory=now)


class Box(Contract):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class Detection(Contract):
    box: Box
    confidence: float = Field(ge=0, le=1)


class TrackPoint(Contract):
    frame: int = Field(ge=0)
    time: float = Field(ge=0)
    box: Box
    confidence: float


class Track(Contract):
    id: str
    points: list[TrackPoint]
    thumbnail: str | None = None
    start: float
    end: float


class VideoRecord(Contract):
    id: str
    filename: str
    content_hash: str
    artifact: str
    created_at: str = Field(default_factory=now)


class VideoObservation(Contract):
    video_id: str
    original_hash: str
    working_artifact: str
    working_hash: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    has_audio: bool
    tracks: list[Track]
    detector: str
    tracker: str
    analysis_fingerprint: str
    normalization: dict[str, Any] = Field(default_factory=dict)


class TrackDecision(Contract):
    track_id: str
    treatment: Treatment
    reason: str
    evidence_ids: list[str]


class DecisionPlan(Contract):
    decisions: list[TrackDecision]
    summary: str
    needs_review: bool
    missing_facts: list[str]


class DecisionResult(Contract):
    plan: DecisionPlan | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    prompt: list[dict[str, str]] = Field(default_factory=list)
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class RenderResult(Contract):
    artifact: str
    content_hash: str
    treatments: dict[str, Treatment]
    parameters: dict[str, Any]
    frame_count: int
    has_audio: bool


class StageRecord(Contract):
    id: str
    stage: str
    status: str
    started_at: str = Field(default_factory=now)
    ended_at: str | None = None
    elapsed_seconds: float | None = None
    error: str | None = None
    snapshot_artifact: str | None = None


class RunSnapshot(Contract):
    schema_version: str = "1"
    id: str
    video_id: str
    index_id: str | None = None
    parent_id: str | None = None
    reused_stages: dict[str, str] = Field(default_factory=dict)
    scenario: ScenarioInput
    config: dict[str, Any]
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    status: str = "QUEUED"
    observation: VideoObservation | None = None
    evidence: EvidenceBundle | None = None
    decision: DecisionResult | None = None
    review_reasons: list[str] = Field(default_factory=list)
    overrides: dict[str, Treatment] = Field(default_factory=dict)
    effective_treatments: dict[str, Treatment] = Field(default_factory=dict)
    manual_output: bool = False
    no_faces_confirmed: bool = False
    stages: list[StageRecord] = Field(default_factory=list)
    render: RenderResult | None = None
    reports: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class IndexRecord(Contract):
    id: str
    document_ids: list[str]
    config: dict[str, Any]
    fingerprint: str
    created_at: str = Field(default_factory=now)
    status: str = "QUEUED"
    chunk_count: int = 0
    error: str | None = None
