from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

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


UsePurpose = Literal["personal_record", "social_publish", "commercial", "internal_use", "limited_share",
                     "subject_access", "evidence_submission", "news_public_interest", "research_ai", "other", "unknown"]
AudienceScope = Literal["self_only", "limited_persons", "internal_members", "designated_external", "public", "unknown"]


class PersonFacts(Contract):
    relationship: Literal["self", "other", "unknown"] = "unknown"
    consent: Literal["granted", "not_obtained", "refused", "unknown", "not_applicable"] = "unknown"
    access_requester: Literal["yes", "no", "unknown"] = "unknown"
    legacy_values: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def legacy_codes(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        legacy = dict(value.get("legacy_values", {}))
        if value.get("relationship") == "third":
            legacy["relationship"] = "third"
            value["relationship"] = "other"
        if value.get("consent") == "denied":
            legacy["consent"] = "denied"
            value["consent"] = "unknown"
        if value.get("relationship") == "self":
            if value.get("consent") not in (None, "unknown", "not_applicable"):
                legacy.setdefault("consent", value["consent"])
            value["consent"] = "not_applicable"
        value["legacy_values"] = legacy
        return value

    @model_validator(mode="after")
    def applicable_consent(self):
        if self.relationship != "self" and self.consent == "not_applicable":
            raise ValueError("Consent is only not_applicable for the uploader themself")
        return self


class PersonContext(PersonFacts):
    track_ids: list[str]


class ScenarioInput(Contract):
    context: str = Field(default="", max_length=10000)
    use_purpose: UsePurpose | None = None
    audience_scope: AudienceScope | None = None
    facts: dict[str, str] = Field(default_factory=dict)
    track_labels: dict[str, str] = Field(default_factory=dict)
    persons: dict[str, PersonContext] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete_input(self):
        if (self.use_purpose is None) != (self.audience_scope is None):
            raise ValueError("Both use_purpose and audience_scope must be selected")
        if self.use_purpose is None and not self.context.strip():
            raise ValueError("Select purpose and audience, or supply a legacy scenario")
        return self


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
    object_type: str = "face"
    landmarks: list[tuple[float, float]] | None = None


class TrackPoint(Contract):
    frame: int = Field(ge=0)
    time: float = Field(ge=0)
    box: Box
    confidence: float
    landmarks: list[tuple[float, float]] | None = None


class Track(Contract):
    id: str
    points: list[TrackPoint]
    thumbnail: str | None = None
    start: float
    end: float
    object_type: str = "face"
    source_track_id: str | None = None


class IdentityGroup(Contract):
    id: str
    track_ids: list[str]
    thumbnail: str | None = None
    source: Literal["automatic", "user"] = "automatic"
    review_reasons: list[str] = Field(default_factory=list)


class IdentityAssignment(Contract):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    track_ids: list[str] = Field(min_length=1)


class IdentityReviewInput(Contract):
    revision: int = Field(ge=0)
    groups: list[IdentityAssignment]
    persons: dict[str, PersonFacts] = Field(default_factory=dict)
    confirmed_group_ids: list[str] = Field(default_factory=list)


class SplitTrackInput(Contract):
    revision: int = Field(ge=0)
    track_id: str
    frame: int = Field(ge=0)


class IdentityTrackSummary(Contract):
    track_id: str
    sample_frames: list[int] = Field(default_factory=list)
    thumbnail: str | None = None
    quality: float = 0
    consistency: float | None = None
    issues: list[str] = Field(default_factory=list)


class IdentityCandidate(Contract):
    first_track_id: str
    second_track_id: str
    similarity: float


class IdentityGrouping(Contract):
    feature_fingerprint: str
    grouping_fingerprint: str
    features_artifact: str | None = None
    automatic_groups: list[IdentityGroup]
    groups: list[IdentityGroup]
    tracks: list[IdentityTrackSummary] = Field(default_factory=list)
    candidates: list[IdentityCandidate] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    reviewed: bool = False
    user_changes: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any] = Field(default_factory=dict)
    elapsed_seconds: float = 0


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
    query_plan: dict[str, Any] | None = None
    executions: dict[str, str] = Field(default_factory=dict)
    parent_id: str | None = None
    reused_stages: dict[str, str] = Field(default_factory=dict)
    scenario: ScenarioInput
    config: dict[str, Any]
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    status: str = "QUEUED"
    observation: VideoObservation | None = None
    identity: IdentityGrouping | None = None
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


class ProductSettings(Contract):
    id: str = "product"
    profile: str = "baseline"
    active_index_id: str | None = None
    created_at: str = Field(default_factory=now)


class NodeRecord(Contract):
    name: str
    attempt: int
    status: str
    input_fingerprint: str
    output_fingerprint: str | None = None
    output_artifact: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    started_at: str = Field(default_factory=now)
    ended_at: str | None = None
    elapsed_seconds: float | None = None
    error: str | None = None
    error_type: str | None = None
    retryable: bool = False


class ExecutionRecord(Contract):
    id: str
    owner_id: str
    owner_kind: Literal["run", "index", "document", "component"]
    graph: str
    graph_version: str
    status: str = "QUEUED"
    attempt_id: str | None = None
    current_node: str | None = None
    stop_after: str | None = None
    pause_requested: bool = False
    nodes: list[NodeRecord] = Field(default_factory=list)
    input_artifact: str | None = None
    output_artifacts: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    error: str | None = None


class InputChoice(Contract):
    value: str
    label: str


class InputOptions(Contract):
    version: str
    use_purpose: list[InputChoice]
    audience_scope: list[InputChoice]
    relationship: list[InputChoice]
    consent: list[InputChoice]


class AppConfig(Contract):
    ready: bool
    messages: list[str]
    inputs: InputOptions
