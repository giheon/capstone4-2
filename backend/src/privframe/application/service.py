from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from privframe.config import Settings, fingerprint, index_config, load_profile
from privframe.domain.models import (
    DocumentRecord, ExecutionRecord, IdentityGroup, IdentityReviewInput, IdentityTrackSummary, IndexRecord, ProductSettings,
    PersonContext, RunSnapshot, ScenarioInput, SplitTrackInput, StageRecord, Treatment, VideoRecord, now,
)
from privframe.domain.identity import apply_review, require_identity_edit, split_observed_track
from privframe.domain.review import effective_treatments
from privframe.domain.ports import ArtifactStorage, RecordRepository
from privframe.infrastructure.storage import ConflictError


class Dispatcher(Protocol):
    def send(self, task: str, *args: str) -> str: ...


UNSET = object()


class Service:
    def __init__(self, settings: Settings, repository: RecordRepository, artifacts: ArtifactStorage,
                 dispatcher: Dispatcher, runtime: Callable[[], dict] = dict, thumbnailer=None):
        self.settings = settings
        self.repository = repository
        self.artifacts = artifacts
        self.dispatcher = dispatcher
        self.runtime = runtime
        self.thumbnailer = thumbnailer

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

    def create_index(self, document_ids: list[str], profile: str, stop_after: str | None = None) -> IndexRecord:
        from privframe.application.graphs import GRAPH_NODES, GraphRunner
        if stop_after and stop_after not in GRAPH_NODES["index"]:
            raise ValueError("Unknown stop node")
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
        if stop_after:
            GraphRunner(self.repository, self.artifacts).create(value.id, "index", "index", stop_after=stop_after)
        try:
            self.dispatcher.send("index", value.id)
        except Exception as error:
            message = f"Queue unavailable: {error}"
            def failed(item):
                item.status, item.error = "FAILED", message
            self.repository.mutate("index", value.id, IndexRecord, failed)
        return self.repository.get("index", value.id, IndexRecord)

    def create_run(self, video_id: str, index_id: str | None, scenario: ScenarioInput,
                   profile: str, parent_id: str | None = None, stop_after: str | None = None) -> RunSnapshot:
        from privframe.application.graphs import GRAPH_NODES
        if stop_after and stop_after not in GRAPH_NODES["analyze"]:
            raise ValueError("Unknown stop node")
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
        scenario = scenario.model_copy(deep=True)
        scenario.persons = {}  # Membership is bound only after analysis or verified reuse.
        run = RunSnapshot(schema_version="3", id=uuid4().hex, video_id=video_id, index_id=index_id,
                          parent_id=parent_id, scenario=scenario, config=config)
        self.repository.insert("run", run)
        return self.request_stage(run.id, "analyze", stop_after=stop_after)

    def labels(self, run_id: str, labels: dict[str, str]) -> RunSnapshot:
        def change(run):
            if run.status != "AWAITING_LABELS" or run.observation is None:
                raise ValueError("Track descriptions can only be changed before decision; fork to change inputs")
            if run.identity is not None:
                raise ValueError("Use identity-review to edit person facts for grouped runs")
            ids = {track.id for track in run.observation.tracks}
            if not set(labels).issubset(ids):
                raise ValueError("Unknown track label")
            if any(len(value) > 500 for value in labels.values()):
                raise ValueError("Track descriptions must be at most 500 characters")
            run.scenario.track_labels = labels
            run.updated_at = now()
        return self.repository.mutate("run", run_id, RunSnapshot, change)

    def identity_review(self, run_id: str, review: IdentityReviewInput) -> RunSnapshot:
        def change(run):
            require_identity_edit(run)
            if run.identity.revision != review.revision:
                raise ConflictError("인물 정보가 변경되었습니다. 새로고침 후 다시 저장하세요.")
            apply_review(run, review)
        return self.repository.mutate("run", run_id, RunSnapshot, change)

    def split_track(self, run_id: str, request: SplitTrackInput) -> RunSnapshot:
        original = self.repository.get("run", run_id, RunSnapshot)
        require_identity_edit(original)
        if original.identity.revision != request.revision:
            raise ConflictError("인물 정보가 변경되었습니다. 새로고침 후 다시 저장하세요.")
        track = next((item for item in original.observation.tracks if item.id == request.track_id), None)
        if track is None or track.object_type != "face":
            raise ValueError("Unknown face track")
        suffix = uuid4().hex[:8]
        children = split_observed_track(track, request.frame, original.observation.fps, suffix)
        if self.thumbnailer:
            for child in children:
                child.thumbnail = self.thumbnailer(original.observation, child, run_id)

        def change(run):
            require_identity_edit(run)
            identity = run.identity
            if identity.revision != request.revision:
                raise ConflictError("인물 정보가 변경되었습니다. 새로고침 후 다시 저장하세요.")
            affected = next(group for group in identity.groups if track.id in group.track_ids)
            previous = affected.model_dump(mode="json")
            affected.track_ids = [key for key in affected.track_ids if key != track.id]
            run.scenario.persons.pop(affected.id, None)
            if affected.track_ids:
                affected.source = "user"
                affected.review_reasons = []
                affected.thumbnail = next((item.thumbnail for item in run.observation.tracks
                                           if item.id in affected.track_ids and item.thumbnail), None)
                run.scenario.persons[affected.id] = PersonContext(track_ids=affected.track_ids)
            else:
                identity.groups.remove(affected)
            run.observation.tracks = [item for item in run.observation.tracks if item.id != track.id]
            run.observation.tracks.extend(children)
            run.observation.tracks.sort(key=lambda item: (item.start, item.id))
            identity.tracks = [item for item in identity.tracks if item.track_id != track.id]
            for number, child in enumerate(children, 1):
                group = IdentityGroup(id=f"person-s{suffix}-{number}", track_ids=[child.id],
                                      thumbnail=child.thumbnail, source="user")
                identity.groups.append(group)
                identity.tracks.append(IdentityTrackSummary(
                    track_id=child.id, thumbnail=child.thumbnail, issues=["manual_split_requires_review"],
                ))
                run.scenario.persons[group.id] = PersonContext(track_ids=group.track_ids)
            identity.candidates = [item for item in identity.candidates
                                   if track.id not in (item.first_track_id, item.second_track_id)]
            identity.user_changes.append({
                "at": now(), "operation": "split_track", "revision": identity.revision,
                "track_id": track.id, "frame": request.frame, "before_group": previous,
                "children": [item.id for item in children],
            })
            # The saved feature file still describes the original tracks. Never
            # reuse it as if it represented the new membership/track geometry.
            identity.feature_fingerprint = ""
            identity.grouping_fingerprint = ""
            identity.reviewed = False
            identity.revision += 1
            run.scenario.track_labels = {}
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
        copied.stages, copied.reports, copied.effective_treatments, copied.executions = [], {}, {}, {}
        copied.render, copied.error, copied.manual_output = None, None, False
        return self.repository.insert("run", copied)

    def request_stage(self, run_id: str, stage: str, retry: bool = False, stop_after: str | None = None) -> RunSnapshot:
        if stage not in {"analyze", "decide", "render"}:
            raise ValueError("Unknown stage")
        from privframe.application.graphs import GRAPH_NODES
        if stop_after and stop_after not in GRAPH_NODES[stage]:
            raise ValueError("Unknown stop node")
        attempt = uuid4().hex

        def change(run):
            if run.status == "COMPLETED":
                raise ValueError("Completed runs are immutable; fork to experiment")
            if retry:
                if run.status not in {"FAILED", "ANALYZING", "DECIDING", "RENDERING", "QUEUED", "PAUSED"}:
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
            if stage == "decide" and run.identity and run.identity.groups and not run.identity.reviewed:
                raise ValueError("인물 그룹과 동의 정보를 저장·확인한 후 판단을 요청하세요.")
            if stage == "render":
                run.effective_treatments = effective_treatments(run)
                run.manual_output = bool(run.review_reasons) or run.no_faces_confirmed
            run.status = {"analyze": "ANALYZING", "decide": "DECIDING", "render": "RENDERING"}[stage]
            run.error = None
            run.updated_at = now()
            run.stages.append(StageRecord(id=attempt, stage=stage, status="QUEUED"))

        value = self.repository.mutate("run", run_id, RunSnapshot, change)
        if stop_after:
            from privframe.application.graphs import GraphRunner
            runner = GraphRunner(self.repository, self.artifacts)
            if stage in value.executions:
                def stop(execution):
                    from privframe.application.graphs import GRAPH_NODES
                    if stop_after not in GRAPH_NODES[stage]:
                        raise ValueError("Unknown stop node")
                    execution.stop_after = stop_after
                self.repository.mutate("execution", value.executions[stage], ExecutionRecord, stop)
            else:
                execution = runner.create(run_id, "run", stage, stop_after=stop_after)
                def attach(value):
                    value.executions[stage] = execution.id
                self.repository.mutate("run", run_id, RunSnapshot, attach)
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

    def product_settings(self):
        try:
            return self.repository.get("settings", "product", ProductSettings)
        except KeyError:
            return ProductSettings()

    def activate_index(self, index_id: str, profile: str = "baseline"):
        config = self.profile(profile)
        index = self.repository.get("index", index_id, IndexRecord)
        if config["mode"] != "real" or index.config["mode"] != "real":
            raise ValueError("Only a real profile and real index can be activated")
        if index.status != "READY" or index_config(index.config) != index_config(config):
            raise ValueError("Activate a READY index compatible with the product profile")
        settings = ProductSettings(profile=profile, active_index_id=index_id)
        try:
            def update(value):
                value.profile, value.active_index_id = profile, index_id
            return self.repository.mutate("settings", "product", ProductSettings, update)
        except KeyError:
            return self.repository.insert("settings", settings)

    def app_config(self):
        import os
        import shutil
        from privframe.domain.inputs import input_options
        settings = self.product_settings()
        config = self.profile(settings.profile)
        reasons = []
        if not os.environ.get("OPENAI_API_KEY"):
            reasons.append("판단 서비스 준비가 필요합니다.")
        if config["mode"] != "real":
            reasons.append("서비스 실행 설정을 확인하고 있습니다.")
        if not self.settings.model_path.is_file() or not self.settings.identity_model_path.is_file():
            reasons.append("영상 분석 모델을 준비하고 있습니다.")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            reasons.append("영상 처리 서비스를 준비하고 있습니다.")
        try:
            index = self.repository.get("index", settings.active_index_id or "", IndexRecord)
            if (index.status != "READY" or index.config["mode"] != "real"
                    or index_config(index.config) != index_config(config)):
                raise ValueError("Incompatible active index")
        except (KeyError, ValueError):
            reasons.append("판단에 사용할 근거 자료를 준비하고 있습니다.")
        return {"ready": not reasons, "messages": reasons, "inputs": input_options()}

    def create_product_run(self, video_id, scenario):
        if scenario.use_purpose is None or scenario.audience_scope is None:
            raise ValueError("이용 목적과 공개·제공 범위를 선택하세요.")
        if not self.app_config()["ready"]:
            raise ServiceUnavailable("서비스 준비 중입니다. 잠시 후 다시 확인하세요.")
        settings = self.product_settings()
        return self.create_run(video_id, settings.active_index_id, scenario, settings.profile)

    def pause_execution(self, execution_id):
        def pause(value):
            if value.status not in {"QUEUED", "RUNNING"}:
                raise ValueError("Only queued or running executions can pause")
            value.pause_requested = True
        return self.repository.mutate("execution", execution_id, ExecutionRecord, pause)

    def resume_execution(self, execution_id):
        execution = self.repository.get("execution", execution_id, ExecutionRecord)
        if execution.status not in {"PAUSED", "FAILED", "RUNNING"}:
            raise ValueError("Only paused, failed or interrupted executions can resume")
        from privframe.application.graphs import GRAPH_VERSION
        if execution.graph_version != GRAPH_VERSION:
            raise ValueError("Graph version changed; create a new execution")
        def clear(value):
            value.pause_requested, value.stop_after, value.status = False, None, "QUEUED"
        self.repository.mutate("execution", execution_id, ExecutionRecord, clear)
        if execution.owner_kind == "run":
            self.request_stage(execution.owner_id, execution.graph, retry=True)
        else:
            task = {"index": "index", "document": "extract", "component": "component"}[execution.owner_kind]
            if execution.owner_kind in {"index", "document"}:
                cls = IndexRecord if execution.owner_kind == "index" else DocumentRecord
                def queued(value):
                    value.status = "QUEUED"
                self.repository.mutate(execution.owner_kind, execution.owner_id, cls, queued)
            try:
                self.dispatcher.send(task, execution.id if task == "component" else execution.owner_id)
            except Exception as error:
                message = f"Queue unavailable: {error}"
                def failed(value):
                    value.status, value.error = "FAILED", message
                self.repository.mutate("execution", execution.id, ExecutionRecord, failed)
                if execution.owner_kind in {"index", "document"}:
                    self.repository.mutate(execution.owner_kind, execution.owner_id, cls, failed)
        return self.repository.get("execution", execution_id, ExecutionRecord)

    def create_component(self, graph, payload, stop_after=None):
        from privframe.application.graphs import GraphRunner
        if graph not in {"retrieval", "decision"}:
            raise ValueError("Component graph must be retrieval or decision")
        if graph == "decision" and (payload.get("observation") is None or payload.get("evidence") is None):
            raise ValueError("Decision components require frozen observation and evidence")
        runner = GraphRunner(self.repository, self.artifacts)
        payload = {**payload, "config": self.profile(payload["profile"])}
        if graph == "retrieval":
            index = self.repository.get("index", payload.get("index_id") or "", IndexRecord)
            if index.status != "READY" or index_config(index.config) != index_config(payload["config"]):
                raise ValueError("Retrieval requires a compatible READY index")
        execution = runner.create(uuid4().hex, "component", graph, stop_after=stop_after)
        path = runner.write_json(f"executions/{execution.id}/input.json", payload)
        def attach(value):
            value.input_artifact = path
        self.repository.mutate("execution", execution.id, ExecutionRecord, attach)
        try:
            self.dispatcher.send("component", execution.id)
        except Exception as error:
            message = f"Queue unavailable: {error}"
            def failed(value):
                value.status, value.error = "FAILED", message
            self.repository.mutate("execution", execution.id, ExecutionRecord, failed)
        return self.repository.get("execution", execution.id, ExecutionRecord)


class ServiceUnavailable(ValueError):
    pass
