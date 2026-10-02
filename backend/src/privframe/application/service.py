from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from privframe.config import Settings, fingerprint, index_config, load_profile
from privframe.domain.models import (
    DocumentRecord, IndexRecord, RunSnapshot, ScenarioInput, StageRecord, Treatment, VideoRecord, now,
)
from privframe.domain.review import effective_treatments
from privframe.domain.ports import ArtifactStorage, RecordRepository


class Dispatcher(Protocol):
    def send(self, task: str, *args: str) -> str: ...


UNSET = object()


class Service:
    def __init__(self, settings: Settings, repository: RecordRepository, artifacts: ArtifactStorage,
                 dispatcher: Dispatcher, runtime: Callable[[], dict] = dict):
        self.settings = settings
        self.repository = repository
        self.artifacts = artifacts
        self.dispatcher = dispatcher
        self.runtime = runtime

    def profile(self, name: str) -> dict:
        return {**load_profile(name, self.settings), "runtime": self.runtime()}

    def queue_document(self, document: DocumentRecord) -> DocumentRecord:
        self.repository.insert("document", document)
        try:
            self.dispatcher.send("extract", document.id)
        except Exception as error:
            message = f"Queue unavailable: {error}"
            def failed(value):
                value.status = "FAILED"
                value.error = message
            self.repository.mutate("document", document.id, DocumentRecord, failed)
        return self.repository.get("document", document.id, DocumentRecord)

    def create_index(self, document_ids: list[str], profile: str) -> IndexRecord:
        if not document_ids:
            raise ValueError("Select at least one document")
        documents = [self.repository.get("document", record_id, DocumentRecord) for record_id in document_ids]
        if any(doc.status != "READY" for doc in documents):
            raise ValueError("All documents must finish extraction before indexing")
        config = self.profile(profile)
        identity = fingerprint({
            "documents": sorted((doc.id, doc.content_hash) for doc in documents),
            "index": index_config(config), "implementation": config["runtime"],
        })
        existing = next((idx for idx in self.repository.list("index", IndexRecord)
                         if idx.fingerprint == identity and idx.status == "READY"), None)
        if existing:
            return existing
        value = IndexRecord(id=uuid4().hex, document_ids=sorted(set(document_ids)),
                            config=config, fingerprint=identity)
        self.repository.insert("index", value)
        try:
            self.dispatcher.send("index", value.id)
        except Exception as error:
            message = f"Queue unavailable: {error}"
            def failed(item):
                item.status, item.error = "FAILED", message
            self.repository.mutate("index", value.id, IndexRecord, failed)
        return self.repository.get("index", value.id, IndexRecord)

    def create_run(self, video_id: str, index_id: str | None, scenario: ScenarioInput,
                   profile: str, parent_id: str | None = None) -> RunSnapshot:
        self.repository.get("video", video_id, VideoRecord)
        config = self.profile(profile)
        if index_id:
            index = self.repository.get("index", index_id, IndexRecord)
            if index.status != "READY":
                raise ValueError("Index is not ready")
            if index_config(config) != index_config(index.config):
                raise ValueError("Selected index and profile use different indexing settings; build a matching index")
        if parent_id:
            parent = self.repository.get("run", parent_id, RunSnapshot)
            if parent.video_id != video_id:
                raise ValueError("Parent run must use the same video")
        run = RunSnapshot(id=uuid4().hex, video_id=video_id, index_id=index_id,
                          parent_id=parent_id, scenario=scenario, config=config)
        self.repository.insert("run", run)
        return self.request_stage(run.id, "analyze")

    def labels(self, run_id: str, labels: dict[str, str]) -> RunSnapshot:
        def change(run):
            if run.status != "AWAITING_LABELS" or run.observation is None:
                raise ValueError("Track descriptions can only be changed before decision; fork to change inputs")
            ids = {track.id for track in run.observation.tracks}
            if not set(labels).issubset(ids):
                raise ValueError("Unknown track label")
            if any(len(value) > 500 for value in labels.values()):
                raise ValueError("Track descriptions must be at most 500 characters")
            run.scenario.track_labels = labels
            run.updated_at = now()
        return self.repository.mutate("run", run_id, RunSnapshot, change)

    def overrides(self, run_id: str, treatments: dict[str, Treatment], confirmed: bool) -> RunSnapshot:
        def change(run):
            if run.status not in {"REVIEW_READY", "REVIEW_REQUIRED"} or run.observation is None:
                raise ValueError("Overrides require a completed decision awaiting review")
            ids = {track.id for track in run.observation.tracks}
            if not set(treatments).issubset(ids):
                raise ValueError("Override contains unknown track")
            if confirmed and ids:
                raise ValueError("No-faces confirmation is only valid for zero detected tracks")
            run.overrides = treatments
            run.no_faces_confirmed = confirmed
            run.updated_at = now()
        return self.repository.mutate("run", run_id, RunSnapshot, change)

    def fork(self, run_id: str, scenario: ScenarioInput | None = None, profile: str | None = None,
             index_id: str | None | object = UNSET) -> RunSnapshot:
        parent = self.repository.get("run", run_id, RunSnapshot)
        return self.create_run(parent.video_id, parent.index_id if index_id is UNSET else index_id,
                               scenario or parent.scenario, profile or parent.config["name"], parent.id)

    def revise(self, run_id: str) -> RunSnapshot:
        """Review a completed output again without rerunning its analysis or decision."""
        parent = self.repository.get("run", run_id, RunSnapshot)
        if parent.status != "COMPLETED" or parent.observation is None or parent.decision is None:
            raise ValueError("Only completed outputs can be revised")
        copied = parent.model_copy(deep=True)
        copied.id = uuid4().hex
        copied.parent_id = parent.id
        copied.reused_stages = {"analyze": parent.id, "decide": parent.id}
        copied.config["runtime"] = self.runtime()
        copied.created_at = copied.updated_at = now()
        copied.status = "REVIEW_REQUIRED" if copied.review_reasons else "REVIEW_READY"
        copied.stages, copied.reports, copied.effective_treatments = [], {}, {}
        copied.render, copied.error, copied.manual_output = None, None, False
        return self.repository.insert("run", copied)

    def request_stage(self, run_id: str, stage: str, retry: bool = False) -> RunSnapshot:
        if stage not in {"analyze", "decide", "render"}:
            raise ValueError("Unknown stage")
        attempt = uuid4().hex

        def change(run):
            if run.status == "COMPLETED":
                raise ValueError("Completed runs are immutable; fork to experiment")
            if retry:
                if run.status not in {"FAILED", "ANALYZING", "DECIDING", "RENDERING", "QUEUED"}:
                    raise ValueError("Only failed or interrupted stages can be retried")
                if not run.stages or run.stages[-1].stage != stage:
                    raise ValueError("Retry must target the last requested stage")
                if run.stages[-1].status in {"QUEUED", "RUNNING"}:
                    run.stages[-1].status = "SUPERSEDED"
                    run.stages[-1].ended_at = now()
            elif stage == "analyze" and run.status != "QUEUED":
                raise ValueError("Analysis has already been requested")
            elif stage == "decide" and run.status != "AWAITING_LABELS":
                raise ValueError("Decision requires completed analysis; fork to change a decision")
            elif stage == "render" and run.status not in {"REVIEW_READY", "REVIEW_REQUIRED"}:
                raise ValueError("Review is required before rendering")
            if stage == "decide" and run.observation is None:
                raise ValueError("Analysis is required")
            if stage == "render":
                run.effective_treatments = effective_treatments(run)
                run.manual_output = bool(run.review_reasons) or run.no_faces_confirmed
            run.status = {"analyze": "ANALYZING", "decide": "DECIDING", "render": "RENDERING"}[stage]
            run.error = None
            run.updated_at = now()
            run.stages.append(StageRecord(id=attempt, stage=stage, status="QUEUED"))

        self.repository.mutate("run", run_id, RunSnapshot, change)
        try:
            self.dispatcher.send("stage", run_id, stage, attempt)
        except Exception as error:
            message = f"Queue unavailable: {error}"
            def failed(run):
                if run.stages[-1].id != attempt:
                    return
                run.status = "FAILED"
                run.error = message
                run.stages[-1].status = "FAILED"
                run.stages[-1].error = run.error
                run.stages[-1].ended_at = now()
            self.repository.mutate("run", run_id, RunSnapshot, failed)
        return self.repository.get("run", run_id, RunSnapshot)
