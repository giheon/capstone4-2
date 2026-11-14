import time
from collections.abc import Callable
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from privframe.domain.models import (
    Chunk, DocumentRecord, EvidenceBundle, ExecutionRecord, IndexRecord, PersonContext, RunSnapshot, now,
)
from privframe.domain.ports import (
    ArtifactStorage, Chunker, Decider, Extractor, IdentityGrouper, RecordRepository, Renderer,
    Reranker, Retriever, VideoAnalyzer,
)
from privframe.domain.inputs import QueryBuilder, usage_scope
from privframe.application.graphs import GraphRunner, ExecutionPaused, SupersededExecution
from privframe.domain.identity import validate_membership
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
    identity_grouper: IdentityGrouper | None = None
    query_builder: QueryBuilder = dataclass_field(default_factory=QueryBuilder)


class Pipeline:
    def __init__(self, repository: RecordRepository, artifacts: ArtifactStorage, adapters: Adapters):
        self.repository, self.artifacts, self.adapters = repository, artifacts, adapters
        self.graphs = GraphRunner(repository, artifacts)

    def _offline_execution(self, owner, kind, graph, operations):
        execution = next((value for value in self.repository.list("execution", ExecutionRecord)
                          if value.owner_id == owner.id and value.owner_kind == kind and value.graph == graph), None)
        execution = execution or self.graphs.create(owner.id, kind, graph)
        return self.graphs.run(execution.id, execution.id, operations, owner.model_dump(mode="json"))

    def extract(self, document_id: str):
        document = self.repository.get("document", document_id, DocumentRecord)
        if document.status == "READY":
            return
        def extract(outputs):
            units = self.adapters.extractor.extract(
                self.artifacts.resolve(f"documents/{document.id}/original.{document.format}"), document.format)
            if not units or not any(unit.text.strip() for unit in units):
                raise ValueError("텍스트를 추출할 수 없습니다. OCR은 초기 구현에서 지원하지 않습니다.")
            return {"units": [unit.model_dump(mode="json") for unit in units], "summary": {"units": len(units)}}
        try:
            outputs = self._offline_execution(document, "document", "extract", {"extract_document": extract})
            from privframe.domain.models import SourceUnit
            units = [SourceUnit.model_validate(item) for item in self.graphs.read_json(outputs["extract_document"])["units"]]
            def ready(value):
                value.units, value.status, value.error = units, "READY", None
            self.repository.mutate("document", document.id, DocumentRecord, ready)
        except ExecutionPaused:
            def paused(value):
                value.status = "PAUSED"
            self.repository.mutate("document", document.id, DocumentRecord, paused)
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
        def chunk(outputs):
            chunks = []
            chunker = self.adapters.chunker(index.config)
            for document_id in index.document_ids:
                document = self.repository.get("document", document_id, DocumentRecord)
                if document.status != "READY":
                    raise ValueError("Document extraction is not ready")
                chunks.extend(chunker.split(document))
            return {"chunks": [item.model_dump(mode="json") for item in chunks], "summary": {"chunks": len(chunks)}}
        def embed(outputs):
            chunks = [Chunk.model_validate(item) for item in self.graphs.read_json(outputs["chunk_documents"])["chunks"]]
            count = self.adapters.retriever(index.config, self.artifacts.root / "chroma").build(index, chunks)
            return {"count": count, "summary": {"chunks": count}}
        try:
            outputs = self._offline_execution(index, "index", "index", {"chunk_documents": chunk, "embed_index": embed})
            count = self.graphs.read_json(outputs["embed_index"])["count"]
            def ready(value):
                value.chunk_count, value.status, value.error = count, "READY", None
            self.repository.mutate("index", index.id, IndexRecord, ready)
        except ExecutionPaused:
            def paused(value):
                value.status = "PAUSED"
            self.repository.mutate("index", index.id, IndexRecord, paused)
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
            self._execute_stage(run, stage, attempt_id)
            run.stages[-1].status = "COMPLETED"
            if run.stages[-1].ended_at is None:
                run.stages[-1].ended_at = now()
                run.stages[-1].elapsed_seconds = time.monotonic() - started
            run.updated_at = now()
            self._commit_attempt(run, attempt_id)
        except SupersededExecution:
            return
        except ExecutionPaused:
            run.status = run.stages[-1].status = "PAUSED"
            run.stages[-1].ended_at = now()
            run.stages[-1].elapsed_seconds = time.monotonic() - started
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

    def _prepare_analysis(self, run: RunSnapshot):
        from privframe.domain.models import VideoRecord
        video = self.repository.get("video", run.video_id, VideoRecord)
        parent = None
        analysis_reused = False
        if run.parent_id:
            parent = self.repository.get("run", run.parent_id, RunSnapshot)
            fingerprint_method = getattr(self.adapters.analyzer, "fingerprint", None)
            if (run.observation is None and parent.observation and fingerprint_method
                    and parent.observation.analysis_fingerprint == fingerprint_method(video, run.config)
                    and self.artifacts.resolve(parent.observation.working_artifact).is_file()):
                run.observation = parent.observation.model_copy(deep=True)
                analysis_reused = True
                run.reused_stages["analyze"] = parent.id
        if run.observation is None:
            run.observation = self.adapters.analyzer.analyze(video, run.config, run.id)
        if (analysis_reused and run.identity is None and parent.identity
                and run.config.get("identity_grouper") == "arcface"
                and all(run.config.get(key) == parent.config.get(key)
                        for key in run.config.keys() | parent.config.keys() if key.startswith("identity_"))):
            # Freeze the parent's edited membership before recognition can fail.
            # Retries must use this child snapshot even if the parent is edited.
            run.identity = parent.identity.model_copy(deep=True)
            same_purpose = usage_scope(run.scenario) == usage_scope(parent.scenario)
            run.scenario.persons = {
                group.id: parent.scenario.persons[group.id].model_copy(deep=True)
                if same_purpose and group.id in parent.scenario.persons
                else PersonContext(track_ids=group.track_ids)
                for group in run.identity.groups
            }
            if not same_purpose:
                run.identity.reviewed = False
        # Checkpoint the expensive detection result before feature/model failures.
        # A retry continues from these frames without repeating video detection.
        def save_analysis(value):
            if value.stages[-1].id == run.stages[-1].id:
                value.observation = run.observation.model_copy(deep=True)
                value.reused_stages = dict(run.reused_stages)
                if run.identity:
                    value.identity = run.identity.model_copy(deep=True)
                    value.scenario.persons = {key: person.model_copy(deep=True)
                                              for key, person in run.scenario.persons.items()}
        self.repository.mutate("run", run.id, RunSnapshot, save_analysis)
        ids = {track.id for track in run.observation.tracks}
        if parent and not analysis_reused:
            run.scenario.track_labels = {}  # Sequential IDs can name different people after reanalysis.
        else:
            run.scenario.track_labels = {key: value for key, value in run.scenario.track_labels.items() if key in ids}

    def _group_identities(self, run):
        parent = self.repository.get("run", run.parent_id, RunSnapshot) if run.parent_id else None
        analysis_reused = bool(parent and run.reused_stages.get("analyze") == parent.id)
        grouper = self.adapters.identity_grouper
        if run.config.get("identity_grouper", "none") == "arcface" and grouper is None:
            raise RuntimeError("ArcFace identity grouping is configured but its adapter is unavailable")
        if run.config.get("identity_grouper", "none") == "arcface" and grouper:
            local_identity = run.identity is not None
            previous = run.identity or (parent.identity if parent and analysis_reused else None)
            previous_persons = run.scenario.persons if local_identity else (
                parent.scenario.persons if parent and analysis_reused else {})
            carried_persons = {}
            current = grouper.group(run.observation, run.config, run.id, cached=previous)
            same_grouping = previous and previous.grouping_fingerprint == current.grouping_fingerprint
            # Manual track splitting invalidates feature files. After extracting
            # the new track geometry, preserve its human-edited membership when
            # all identity settings and the model are unchanged.
            manual_geometry = previous and any(track.source_track_id for track in run.observation.tracks)
            same_settings = local_identity or (parent and all(
                run.config.get(key) == parent.config.get(key)
                for key in run.config if key.startswith("identity_")
            ))
            if previous and (same_grouping or (manual_geometry and same_settings and previous.model == current.model)):
                validate_membership(run, previous.groups)
                current.groups = [group.model_copy(deep=True) for group in previous.groups]
                current.user_changes = list(previous.user_changes)
                current.revision = previous.revision
                same_purpose = local_identity or (parent and usage_scope(run.scenario) == usage_scope(parent.scenario))
                if same_purpose:
                    carried_persons = {
                        group.id: previous_persons[group.id].model_copy(deep=True)
                        for group in current.groups if group.id in previous_persons
                        and set(previous_persons[group.id].track_ids) == set(group.track_ids)
                    }
                    current.reviewed = previous.reviewed and len(carried_persons) == len(current.groups)
                if parent and analysis_reused:
                    run.reused_stages["identity"] = parent.id
            run.identity = current
            # New/changed purpose keeps the visual grouping but requires new
            # person facts; 'self' never implies consent.
            run.scenario.persons = {
                group.id: carried_persons.get(group.id, PersonContext(track_ids=group.track_ids))
                for group in current.groups
            }
            run.scenario.track_labels = {}
        else:
            run.identity = None
            run.scenario.persons = {}
        run.status = "AWAITING_LABELS"

    def _build_query(self, run):
        run.query_plan = self.adapters.query_builder.build(run.scenario)

    def _retrieve(self, run):
        query = run.query_plan["query"]
        if run.index_id:
            index = self.repository.get("index", run.index_id, IndexRecord)
            retrieval_config = {**index.config, "top_k": run.config["top_k"],
                                "evidence_char_budget": run.config["evidence_char_budget"]}
            run.evidence = self.adapters.retriever(retrieval_config, self.artifacts.root / "chroma").retrieve(index, query)
        else:
            run.evidence = EvidenceBundle(query=query, index_id="none")

    def _select_evidence(self, run):
        if self.adapters.reranker:
            run.evidence = self.adapters.reranker(run.config).rerank(run.query_plan["query"], run.evidence)
        def save(value):
            if value.stages[-1].id == run.stages[-1].id:
                value.evidence, value.query_plan = run.evidence, run.query_plan
        if run.stages:
            self.repository.mutate("run", run.id, RunSnapshot, save)

    def _llm_decision(self, run):
        run.decision = self.adapters.decider(run.config).decide(run.scenario, run.observation, run.evidence)
        if run.decision.error:
            raise RuntimeError(run.decision.error)

    def _validate_output(self, run):
        run.review_reasons = review_reasons(run.observation, run.evidence, run.decision)
        run.status = "REVIEW_REQUIRED" if run.review_reasons else "REVIEW_READY"

    def _execute_stage(self, run, stage, attempt_id):
        import json
        methods = {
            "analyze": {"analyze_video": self._prepare_analysis, "group_identities": self._group_identities},
            "decide": {"build_query": self._build_query, "retrieve": self._retrieve,
                       "select_evidence": self._select_evidence, "llm_decision": self._llm_decision,
                       "validate_output": self._validate_output},
            "render": {"render_video": lambda value: self._render(value, attempt_id), "report": self._report},
        }
        if stage not in methods:
            raise ValueError("Unknown stage")
        execution_id = run.executions.get(stage)
        if not execution_id:
            execution = self.graphs.create(run.id, "run", stage)
            execution_id = run.executions[stage] = execution.id
            def attach(value):
                if value.stages[-1].id == attempt_id:
                    value.executions[stage] = execution_id
            self.repository.mutate("run", run.id, RunSnapshot, attach)
        fields = ("observation", "identity", "scenario", "reused_stages", "query_plan", "evidence", "decision",
                  "review_reasons", "render", "reports", "status")
        def hydrate(payload):
            merged = run.model_dump(mode="json")
            merged.update(payload["run"])
            loaded = RunSnapshot.model_validate(merged)
            for field in fields:
                setattr(run, field, getattr(loaded, field))
        def wrap(method):
            def operation(outputs):
                if outputs:
                    hydrate(self.graphs.read_json(next(reversed(outputs.values()))))
                method(run)
                data = run.model_dump(mode="json")
                summary = {"status": run.status}
                if run.observation:
                    summary.update(tracks=len(run.observation.tracks), frames=run.observation.frame_count)
                if run.identity:
                    summary.update(persons=len(run.identity.groups), **run.identity.metrics)
                if run.evidence:
                    summary.update(candidates=len(run.evidence.candidates), provided=len(run.evidence.provided))
                if run.review_reasons:
                    summary["review_reasons"] = run.review_reasons
                return {"run": {key: data[key] for key in fields}, "summary": summary,
                        "usage": run.decision.usage if run.decision else {}}
            return operation
        operations = {name: wrap(method) for name, method in methods[stage].items()}
        def guard():
            current = self.repository.get("run", run.id, RunSnapshot)
            return current.stages[-1].id == attempt_id
        inputs = {"config": run.config, "scope": usage_scope(run.scenario),
                  "persons": run.scenario.persons if stage == "decide" else {},
                  "treatments": run.effective_treatments if stage == "render" else {}}
        # Convert contracts before hashing; checkpoints contain only artifact references.
        inputs = json.loads(json.dumps(inputs, default=lambda value: value.model_dump(mode="json")))
        outputs = self.graphs.run(execution_id, attempt_id, operations, inputs, guard)
        if outputs:
            hydrate(self.graphs.read_json(next(reversed(outputs.values()))))
        if stage == "render" and run.stages[-1].ended_at is None:
            # Transport/snapshot failure can follow a fully checkpointed graph.
            # Reissue only this attempt's report, reusing its rendered pixels.
            self._report(run)

    def _render(self, run, attempt_id):
        run.render = self.adapters.renderer.render(run.observation, run.effective_treatments, run.id, attempt_id)
        run.status = "COMPLETED"

    def _report(self, run):
        from datetime import datetime
        run.stages[-1].status = "COMPLETED"
        run.stages[-1].ended_at = now()
        run.stages[-1].elapsed_seconds = (datetime.fromisoformat(run.stages[-1].ended_at)
                                        - datetime.fromisoformat(run.stages[-1].started_at)).total_seconds()
        run.reports = self.adapters.reporter(run)

    def run_component(self, execution_id):
        execution = self.repository.get("execution", execution_id, ExecutionRecord)
        payload = self.graphs.read_json(execution.input_artifact)
        from privframe.domain.models import ScenarioInput, VideoObservation
        config = payload["config"]
        run = RunSnapshot(id=execution.owner_id, video_id="component", config=config,
                          scenario=ScenarioInput.model_validate(payload["scenario"]), index_id=payload.get("index_id"),
                          observation=VideoObservation.model_validate(payload["observation"]) if payload.get("observation") else None,
                          evidence=EvidenceBundle.model_validate(payload["evidence"]) if payload.get("evidence") else None)
        fields = ("query_plan", "evidence", "decision", "review_reasons", "status")
        def wrap(method):
            def operation(outputs):
                if outputs:
                    previous = self.graphs.read_json(next(reversed(outputs.values())))
                    data = run.model_dump(mode="json")
                    data.update(previous["result"])
                    loaded = RunSnapshot.model_validate(data)
                    for key in fields:
                        setattr(run, key, getattr(loaded, key))
                method(run)
                data = run.model_dump(mode="json")
                return {"result": {key: data[key] for key in fields},
                        "summary": {"status": run.status, "review_reasons": run.review_reasons},
                        "usage": run.decision.usage if run.decision else {}}
            return operation
        methods = {"retrieval": {"build_query": self._build_query, "retrieve": self._retrieve,
                                 "select_evidence": self._select_evidence},
                   "decision": {"llm_decision": self._llm_decision, "validate_output": self._validate_output}}
        try:
            self.graphs.run(execution_id, execution_id, {name: wrap(method) for name, method in methods[execution.graph].items()},
                            payload)
        except ExecutionPaused:
            pass
