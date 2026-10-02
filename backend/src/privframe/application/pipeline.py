import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from privframe.domain.models import (
    DocumentRecord, EvidenceBundle, IndexRecord, RunSnapshot, now,
)
from privframe.domain.ports import (
    ArtifactStorage, Chunker, Decider, Extractor, RecordRepository, Renderer, Reranker, Retriever, VideoAnalyzer,
)
from privframe.domain.review import review_reasons


@dataclass
class Adapters:
    extractor: Extractor
    chunker: Callable[[dict], Chunker]
    retriever: Callable[[dict, Path], Retriever]
    decider: Callable[[dict], Decider]
    analyzer: VideoAnalyzer
    renderer: Renderer
    reporter: Callable[[RunSnapshot], dict[str, str]]
    reranker: Callable[[dict], Reranker] | None = None


class Pipeline:
    def __init__(self, repository: RecordRepository, artifacts: ArtifactStorage, adapters: Adapters):
        self.repository, self.artifacts, self.adapters = repository, artifacts, adapters

    def extract(self, document_id: str):
        document = self.repository.get("document", document_id, DocumentRecord)
        if document.status == "READY":
            return
        try:
            units = self.adapters.extractor.extract(
                self.artifacts.resolve(f"documents/{document.id}/original.{document.format}"), document.format)
            if not units or not any(unit.text.strip() for unit in units):
                raise ValueError("텍스트를 추출할 수 없습니다. OCR은 초기 구현에서 지원하지 않습니다.")
            def ready(value):
                value.units, value.status, value.error = units, "READY", None
            self.repository.mutate("document", document.id, DocumentRecord, ready)
        except Exception as error:
            message = str(error)
            def failed(value):
                value.status, value.error = "FAILED", message
            self.repository.mutate("document", document.id, DocumentRecord, failed)

    def build_index(self, index_id: str):
        index = self.repository.get("index", index_id, IndexRecord)
        if index.status == "READY":
            return
        def indexing(value):
            value.status, value.error = "INDEXING", None
        self.repository.mutate("index", index.id, IndexRecord, indexing)
        try:
            chunks = []
            chunker = self.adapters.chunker(index.config)
            for document_id in index.document_ids:
                document = self.repository.get("document", document_id, DocumentRecord)
                if document.status != "READY":
                    raise ValueError("Document extraction is not ready")
                chunks.extend(chunker.split(document))
            count = self.adapters.retriever(index.config, self.artifacts.root / "chroma").build(index, chunks)
            def ready(value):
                value.chunk_count, value.status, value.error = count, "READY", None
            self.repository.mutate("index", index.id, IndexRecord, ready)
        except Exception as error:
            message = str(error)
            def failed(value):
                value.status, value.error = "FAILED", message
            self.repository.mutate("index", index.id, IndexRecord, failed)

    def run_stage(self, run_id: str, stage: str, attempt_id: str):
        run = self.repository.get("run", run_id, RunSnapshot)
        if not run.stages or run.stages[-1].id != attempt_id or run.stages[-1].status != "QUEUED":
            return  # Completed, superseded, or redelivered attempt: never overwrite its artifacts.
        def begin(value):
            if value.stages[-1].id != attempt_id or value.stages[-1].status != "QUEUED":
                raise ValueError("Attempt is no longer active")
            value.stages[-1].status = "RUNNING"
            value.stages[-1].started_at = now()
            value.stages[-1].snapshot_artifact = f"runs/{value.id}/attempts/{attempt_id}/snapshot.json"
        run = self.repository.mutate("run", run.id, RunSnapshot, begin)
        started = time.monotonic()
        try:
            if stage == "analyze":
                self._analyze(run)
            elif stage == "decide":
                self._decide(run)
            elif stage == "render":
                run.render = self.adapters.renderer.render(
                    run.observation, run.effective_treatments, run.id, attempt_id)
                run.status = "COMPLETED"
            else:
                raise ValueError("Unknown stage")
            run.stages[-1].status = "COMPLETED"
            run.stages[-1].ended_at = now()
            run.stages[-1].elapsed_seconds = time.monotonic() - started
            run.updated_at = now()
            if stage == "render":
                run.reports = self.adapters.reporter(run)
            self._commit_attempt(run, attempt_id)
        except Exception as error:
            run.status, run.error = "FAILED", str(error)
            run.stages[-1].status, run.stages[-1].error = "FAILED", str(error)
            run.stages[-1].ended_at = now()
            run.stages[-1].elapsed_seconds = time.monotonic() - started
            run.updated_at = now()
            try:
                run.reports = self.adapters.reporter(run)
            except Exception:
                pass  # Keep the original stage failure even if report generation also fails.
            self._commit_attempt(run, attempt_id)

    def _commit_attempt(self, run: RunSnapshot, attempt_id: str):
        run.stages[-1].snapshot_artifact = f"runs/{run.id}/attempts/{attempt_id}/snapshot.json"
        try:
            self.artifacts.put_bytes(run.stages[-1].snapshot_artifact, run.model_dump_json(indent=2).encode())
        except Exception as error:
            # SQLite can still retain the stage results when file storage fails.
            # Never leave the attempt running or claim a missing snapshot exists.
            message = f"Snapshot persistence failed: {error}"
            run.error = f"{run.error}; {message}" if run.error else message
            run.status = run.stages[-1].status = "FAILED"
            run.stages[-1].error = run.error
            run.stages[-1].snapshot_artifact = None
            run.updated_at = now()
            run.reports = {}
            try:
                run.reports = self.adapters.reporter(run)
            except Exception:
                pass
        def change(value):
            if value.stages[-1].id != attempt_id:
                return
            for key in type(run).model_fields:
                setattr(value, key, getattr(run, key))
        self.repository.mutate("run", run.id, RunSnapshot, change)

    def _analyze(self, run: RunSnapshot):
        from privframe.domain.models import VideoRecord
        video = self.repository.get("video", run.video_id, VideoRecord)
        if run.parent_id:
            parent = self.repository.get("run", run.parent_id, RunSnapshot)
            fingerprint_method = getattr(self.adapters.analyzer, "fingerprint", None)
            if (parent.observation and fingerprint_method
                    and parent.observation.analysis_fingerprint == fingerprint_method(video, run.config)
                    and self.artifacts.resolve(parent.observation.working_artifact).is_file()):
                run.observation = parent.observation.model_copy(deep=True)
        if run.observation is None:
            run.observation = self.adapters.analyzer.analyze(video, run.config, run.id)
        ids = {track.id for track in run.observation.tracks}
        run.scenario.track_labels = {key: value for key, value in run.scenario.track_labels.items() if key in ids}
        run.status = "AWAITING_LABELS"

    def _decide(self, run: RunSnapshot):
        query_parts = [run.scenario.context]
        query_parts += [f"{key}: {value}" for key, value in sorted(run.scenario.facts.items())]
        query_parts += [f"{key}: {value}" for key, value in sorted(run.scenario.track_labels.items())]
        query = "\n".join(query_parts)
        if run.index_id:
            index = self.repository.get("index", run.index_id, IndexRecord)
            retrieval_config = {**index.config, "top_k": run.config["top_k"],
                                "evidence_char_budget": run.config["evidence_char_budget"]}
            run.evidence = self.adapters.retriever(retrieval_config, self.artifacts.root / "chroma").retrieve(index, query)
        else:
            run.evidence = EvidenceBundle(query=query, index_id="none")
        if self.adapters.reranker:
            run.evidence = self.adapters.reranker(run.config).rerank(query, run.evidence)
        # Persist what was actually retrieved before an external model call can fail.
        def save_evidence(value):
            if value.stages[-1].id == run.stages[-1].id:
                value.evidence = run.evidence
        self.repository.mutate("run", run.id, RunSnapshot, save_evidence)
        run.decision = self.adapters.decider(run.config).decide(run.scenario, run.observation, run.evidence)
        run.review_reasons = review_reasons(run.observation, run.evidence, run.decision)
        run.status = "REVIEW_REQUIRED" if run.review_reasons else "REVIEW_READY"
