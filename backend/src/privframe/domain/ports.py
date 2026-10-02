from pathlib import Path
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from .models import (
    Chunk, DecisionResult, Detection, DocumentRecord, EvidenceBundle, IndexRecord,
    Contract, RenderResult, ScenarioInput, SourceUnit, Treatment, VideoObservation, VideoRecord,
)

T = TypeVar("T", bound=Contract)


class RecordRepository(Protocol):
    def get(self, kind: str, record_id: str, cls: type[T]) -> T: ...
    def list(self, kind: str, cls: type[T]) -> list[T]: ...
    def insert(self, kind: str, value: T) -> T: ...
    def mutate(self, kind: str, record_id: str, cls: type[T], change: Callable[[T], None]) -> T: ...


class ArtifactStorage(Protocol):
    root: Path
    def resolve(self, relative: str) -> Path: ...
    def relative(self, path: Path) -> str: ...
    def put_bytes(self, relative: str, data: bytes) -> str: ...


class Extractor(Protocol):
    def extract(self, path: Path, format: str) -> list[SourceUnit]: ...


class Chunker(Protocol):
    def split(self, document: DocumentRecord) -> list[Chunk]: ...


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class Retriever(Protocol):
    def build(self, index: IndexRecord, chunks: list[Chunk]) -> int: ...
    def retrieve(self, index: IndexRecord, query: str) -> EvidenceBundle: ...


class Reranker(Protocol):
    def rerank(self, query: str, bundle: EvidenceBundle) -> EvidenceBundle: ...


class Decider(Protocol):
    def decide(self, scenario: ScenarioInput, observation: VideoObservation,
               evidence: EvidenceBundle) -> DecisionResult: ...


class Detector(Protocol):
    def detect(self, frame: Any) -> list[Detection]: ...


class Tracker(Protocol):
    def update(self, frame: int, time: float, detections: list[Detection],
               scene_cut: bool = False) -> dict[str, Detection]: ...


class VideoAnalyzer(Protocol):
    def analyze(self, video: VideoRecord, config: dict[str, Any], run_id: str) -> VideoObservation: ...


class Renderer(Protocol):
    def render(self, observation: VideoObservation, treatments: dict[str, Treatment],
               run_id: str, attempt_id: str) -> RenderResult: ...
