"""Product inputs and durable component boundaries, using isolated real storage."""

import pytest
from fastapi.testclient import TestClient

import test_application as shared
from test_identity_review import enable_grouping, review_body
from privframe.api import create_app
from privframe.application.graphs import GRAPH_VERSION, GraphRunner
from privframe.application.pipeline import Pipeline
from privframe.domain.inputs import QueryBuilder, input_options
from privframe.domain.models import (
    ExecutionRecord, IdentityReviewInput, IndexRecord, PersonContext, PersonFacts, RunSnapshot, ScenarioInput,
)

blank_video_bytes = shared.blank_video_bytes
system = shared.system


def scenario(**changes):
    return ScenarioInput(use_purpose="social_publish", audience_scope="public", **changes)


def test_structured_inputs_and_legacy_codes_are_not_legal_shortcuts():
    assert len(input_options()["use_purpose"]) == 11
    assert len(input_options()["audience_scope"]) == 6
    assert ScenarioInput(use_purpose="unknown", audience_scope="unknown").context == ""
    with pytest.raises(ValueError):
        ScenarioInput(use_purpose="social_publish")
    assert PersonFacts(relationship="self", consent="unknown").consent == "not_applicable"
    legacy = PersonFacts.model_validate({"relationship": "third", "consent": "denied"})
    assert legacy.relationship == "other" and legacy.consent == "unknown"
    assert legacy.legacy_values == {"relationship": "third", "consent": "denied"}
    with pytest.raises(ValueError):
        PersonFacts(relationship="other", consent="not_applicable")
    facts = {f"person-{n}": PersonContext(track_ids=[f"track-{n}"], relationship="other", consent="refused") for n in range(3)}
    plan = QueryBuilder().build(scenario(persons=facts))
    assert "person-" not in plan["query"] and "track-" not in plan["query"]
    assert plan["query"].count("명시적 동의 거부") == 1
    assert "불특정 다수" in plan["query"] and plan["version"]


def test_audience_change_invalidates_person_confirmations_but_reuses_video(system):
    enable_grouping(system)
    parent = system.service.create_run(system.video.id, None, scenario(), "fixture")
    parent = system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(parent)))
    changed = parent.scenario.model_copy(update={"audience_scope": "limited_persons"})
    child = system.service.fork(parent.id, changed)
    assert child.status == "AWAITING_LABELS" and not child.identity.reviewed
    assert all(p.consent == "unknown" for p in child.scenario.persons.values())
    assert child.identity.groups == parent.identity.groups and system.analyzer.calls == 1


def test_product_requires_real_ready_index_and_pins_settings(system, monkeypatch):
    index = system.add_index()
    with pytest.raises(ValueError, match="real"):
        system.service.activate_index(index.id, "fixture")
    with TestClient(create_app(system.service)) as client:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert not client.get("/api/app-config").json()["ready"]
        result = client.post("/api/runs", json={"video_id": system.video.id, "scenario": scenario().model_dump(mode="json")})
        assert result.status_code == 503
        # No model calls: simulate an operator-verified READY record to test selection.
        config = system.service.profile("baseline")
        for identity in ("real-one", "real-two"):
            system.service.repository.insert("index", IndexRecord(id=identity, document_ids=index.document_ids,
                                                                 config=config, fingerprint=identity, status="READY"))
        monkeypatch.setenv("OPENAI_API_KEY", "test-no-network")
        system.service.activate_index("real-one")
        system.dispatcher.paused = True
        result = client.post("/api/runs", json={"video_id": system.video.id, "scenario": scenario().model_dump(mode="json")})
        assert result.status_code == 202, result.text
        assert result.json()["index_id"] == "real-one" and result.json()["config"]["mode"] == "real"
        system.service.activate_index("real-two")
        assert client.get(f"/api/runs/{result.json()['id']}").json()["index_id"] == "real-one"


def test_pause_after_detection_survives_worker_restart_without_reanalysis(system):
    enable_grouping(system)
    run = system.service.create_run(system.video.id, None, scenario(), "fixture", stop_after="analyze_video")
    assert run.status == "PAUSED", run.error
    execution_id = run.executions["analyze"]
    execution = system.service.repository.get("execution", execution_id, ExecutionRecord)
    assert execution.status == "PAUSED" and execution.nodes[0].status == "COMPLETED"
    assert system.analyzer.calls == 1 and run.observation is not None
    restarted = Pipeline(system.service.repository, system.service.artifacts, system.pipeline.adapters)
    system.dispatcher.pipeline = restarted
    def never(*a):
        pytest.fail("Detection was repeated")
    system.analyzer.analyze = never
    result = system.service.resume_execution(execution_id)
    assert result.id == execution_id and result.status == "COMPLETED", result.error
    ready = system.service.repository.get("run", run.id, RunSnapshot)
    assert ready.status == "AWAITING_LABELS" and len(ready.identity.groups) == 2
    assert ready.executions["analyze"] == execution_id
    assert [node.name for node in result.nodes] == ["analyze_video", "group_identities"]
    assert all(node.output_artifact and node.output_fingerprint and node.elapsed_seconds is not None for node in result.nodes)


def test_retrieval_is_not_repeated_after_pause_or_provider_failure(system):
    index = system.add_index()
    run = system.run(index)
    run = system.service.request_stage(run.id, "decide", stop_after="retrieve")
    assert run.status == "PAUSED", run.error
    identity = run.executions["decide"]
    original = system.pipeline.adapters.decider
    def unavailable(config):
        raise RuntimeError("Provider unavailable")
    def never(*args):
        pytest.fail("Retrieved evidence was recomputed")
    system.pipeline.adapters.retriever = never
    system.pipeline.adapters.decider = unavailable
    failure = system.service.resume_execution(identity)
    assert failure.status == "FAILED" and failure.nodes[-1].error_type == "RuntimeError"
    assert failure.nodes[-1].retryable
    saved = system.service.repository.get("run", run.id, RunSnapshot)
    assert saved.evidence.provided and saved.status == "FAILED"
    system.pipeline.adapters.decider = original
    result = system.service.resume_execution(identity)
    assert result.status == "COMPLETED", result.error
    assert sum(node.name == "retrieve" for node in result.nodes) == 1
    assert sum(node.name == "llm_decision" for node in result.nodes) == 2


def test_independent_components_never_call_other_axes_and_export_real_graph(system):
    index = system.add_index()
    system.pipeline.adapters.analyzer.analyze = lambda *a: pytest.fail("Unexpected vision")
    with TestClient(create_app(system.service)) as client:
        response = client.post("/api/executions", json={"graph": "retrieval", "profile": "fixture",
                                                        "index_id": index.id, "scenario": scenario().model_dump(mode="json")})
        assert response.status_code == 202, response.text
        execution = response.json()
        assert execution["status"] == "COMPLETED", execution["error"]
        output = client.get(f"/api/executions/{execution['id']}/outputs/select_evidence").json()["result"]
        assert output["evidence"]["provided"] and output["decision"] is None
        graph = client.get("/api/graphs/decide").json()
        assert graph["version"] == GRAPH_VERSION and "llm_decision" in graph["mermaid"]
        assert client.get("/api/artifacts/" + execution["output_artifacts"]["retrieve"]).status_code == 404
        def incompatible(value):
            value.graph_version, value.status = "old", "PAUSED"
        system.service.repository.mutate("execution", execution["id"], ExecutionRecord, incompatible)
        assert client.post(f"/api/executions/{execution['id']}/resume").status_code == 400


def test_offline_index_pause_reuses_chunks(system):
    document = system.add_document()
    system.dispatcher.paused = True
    index = system.service.create_index([document.id], "fixture")
    execution = GraphRunner(system.service.repository, system.service.artifacts).create(
        index.id, "index", "index", stop_after="chunk_documents")
    system.pipeline.build_index(index.id)
    assert system.service.repository.get("index", index.id, IndexRecord).status == "PAUSED"
    system.pipeline.adapters.chunker = lambda *a: pytest.fail("Chunking was repeated")
    system.dispatcher.paused = False
    result = system.service.resume_execution(execution.id)
    assert result.status == "COMPLETED", result.error
    assert system.service.repository.get("index", index.id, IndexRecord).status == "READY"


def test_running_pause_request_is_observed_after_the_active_node(system):
    def pause_during_detection():
        executions = system.service.repository.list("execution", ExecutionRecord)
        execution = next(item for item in executions if item.graph == "analyze" and item.status == "RUNNING")
        system.service.pause_execution(execution.id)
    system.analyzer.on_analyze = pause_during_detection
    run = system.run()
    assert run.status == "PAUSED", run.error
    execution = system.service.repository.get("execution", run.executions["analyze"], ExecutionRecord)
    assert execution.current_node == "analyze_video" and execution.pause_requested
    assert run.observation is not None
    system.analyzer.on_analyze = None
    resumed = system.service.resume_execution(execution.id)
    assert resumed.status == "COMPLETED" and system.analyzer.calls == 1


def test_independent_llm_uses_frozen_inputs_without_retrieval_or_video(system):
    run = system.run(system.add_index())
    run = system.service.request_stage(run.id, "decide")
    system.pipeline.adapters.retriever = lambda *a: pytest.fail("Unexpected retrieval")
    system.analyzer.analyze = lambda *a: pytest.fail("Unexpected video analysis")
    with TestClient(create_app(system.service)) as client:
        response = client.post("/api/executions", json={
            "graph": "decision", "profile": "fixture", "scenario": run.scenario.model_dump(mode="json"),
            "observation": run.observation.model_dump(mode="json"), "evidence": run.evidence.model_dump(mode="json"),
        })
        assert response.status_code == 202, response.text
        result = response.json()
        assert result["status"] == "COMPLETED", result["error"]
        assert [node["name"] for node in result["nodes"]] == ["llm_decision", "validate_output"]
        output = client.get(f"/api/executions/{result['id']}/outputs/validate_output").json()
        assert output["result"]["status"] == "REVIEW_READY"


def test_render_snapshot_failure_retry_keeps_pixels_but_refreshes_attempt_report(system, monkeypatch):
    run = system.run(system.add_index())
    run = system.service.request_stage(run.id, "decide")
    original = system.service.artifacts.put_bytes
    def fail_snapshot(path, data):
        if path.endswith("/snapshot.json"):
            raise OSError("Snapshot write unavailable")
        return original(path, data)
    monkeypatch.setattr(system.service.artifacts, "put_bytes", fail_snapshot)
    failed = system.service.request_stage(run.id, "render")
    assert failed.status == "FAILED" and failed.render is not None
    monkeypatch.setattr(system.service.artifacts, "put_bytes", original)
    system.pipeline.adapters.renderer.render = lambda *a: pytest.fail("Completed pixels were rendered again")
    completed = system.service.request_stage(run.id, "render", retry=True)
    assert completed.status == "COMPLETED", completed.error
    saved = GraphRunner(system.service.repository, system.service.artifacts).read_json(completed.reports["json"])
    assert saved["stages"] == [stage.model_dump(mode="json") for stage in completed.stages]
    assert saved["status"] == "COMPLETED" and completed.render == failed.render


def test_component_evaluation_runs_retrieval_and_frozen_decision_without_video(system, tmp_path):
    import json
    from privframe.component_evaluation import evaluate_components
    index = system.add_index()
    run = system.run(index)
    run = system.service.request_stage(run.id, "decide")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(run.model_dump_json())
    manifest = tmp_path / "components.json"
    manifest.write_text(json.dumps({"index_id": index.id, "cases": [{"id": "fixed", "scenario": scenario().model_dump(mode="json"), "snapshot": snapshot.name}]}))
    system.analyzer.analyze = lambda *a: pytest.fail("Unrelated detector invoked")
    with TestClient(create_app(system.service)) as client:
        retrieved = evaluate_components(client, manifest, "fixture", "retrieval")
        assert retrieved["cases"][0]["result"]["status"] == "COMPLETED"
        system.pipeline.adapters.retriever = lambda *a: pytest.fail("Unrelated retrieval invoked")
        decided = evaluate_components(client, manifest, "fixture", "decision")
        assert decided["cases"][0]["metrics"]["track_map_complete"]


def test_independent_index_and_annotated_vision_evaluation(system, tmp_path, blank_video_bytes):
    import json
    from privframe.component_evaluation import evaluate_components
    source = tmp_path / "source.txt"
    source.write_text("독립적인 근거 색인 평가입니다. " * 100)
    manifest = tmp_path / "index-eval.json"
    manifest.write_text(json.dumps({"cases": [{"id": "index-only", "documents": [{"path": source.name}]}]}))
    with TestClient(create_app(system.service)) as client:
        indexed = evaluate_components(client, manifest, "fixture", "index")
        assert indexed["cases"][0]["result"]["status"] == "READY"
        assert indexed["cases"][0]["metrics"]["chunks"] > 0
        enable_grouping(system)
        system.pipeline.adapters.retriever = lambda *a: pytest.fail("Vision evaluation retrieved sources")
        system.pipeline.adapters.decider = lambda *a: pytest.fail("Vision evaluation called a decider")
        video = tmp_path / "vision.mp4"
        video.write_bytes(blank_video_bytes)
        annotations = {"width": 64, "height": 64, "fps": 30, "exhaustive": True, "annotated_frames": [0],
                       "faces": [{"frame": 0, "identity_label": label, "box": {"x": x, "y": 16, "width": 16, "height": 16}}
                                 for label, x in [("A", 8), ("B", 40)]]}
        manifest.write_text(json.dumps({"cases": [{"id": "vision-only", "video": video.name,
                                                   "manual_face_expectation": {"annotations": annotations}}]}))
        analyzed = evaluate_components(client, manifest, "fixture", "vision")
        assert analyzed["cases"][0]["metrics"]["face_quality"]["annotated_box_metrics"]["true_positives"] == 2
        assert analyzed["cases"][0]["metrics"]["grouping_quality"]["current"]["false_merged_track_pairs"] == 0


def test_graph_checkpoint_resumes_in_a_new_python_process(tmp_path):
    import subprocess
    import sys
    code = r'''
import sys
from pathlib import Path
from privframe.application.graphs import ExecutionPaused, GraphRunner
from privframe.domain.models import ExecutionRecord
from privframe.infrastructure.storage import ArtifactStore, Repository
root = Path(sys.argv[2])
runner = GraphRunner(Repository(root / "state.sqlite3"), ArtifactStore(root))
def first(outputs):
    marker = root / "expensive-call.txt"
    if marker.exists():
        raise AssertionError("Completed node was repeated across process restart")
    marker.write_text("once")
    return {"summary": {"calls": 1}}
def second(outputs):
    assert "analyze_video" in outputs
    return {"summary": {"resumed_in_another_process": True}}
if sys.argv[1] == "start":
    execution = runner.create("video", "component", "analyze", stop_after="analyze_video")
    (root / "execution-id.txt").write_text(execution.id)
else:
    execution = runner.repository.get("execution", (root / "execution-id.txt").read_text(), ExecutionRecord)
    def clear(value):
        value.stop_after = None
        value.pause_requested = False
    runner.repository.mutate("execution", execution.id, ExecutionRecord, clear)
try:
    runner.run(execution.id, sys.argv[1], {"analyze_video": first, "group_identities": second}, {"video": "fixed"})
except ExecutionPaused:
    assert sys.argv[1] == "start"
result = runner.repository.get("execution", execution.id, ExecutionRecord)
assert result.status == ("PAUSED" if sys.argv[1] == "start" else "COMPLETED")
runner.repository.engine.dispose()
'''
    for mode in ("start", "resume"):
        subprocess.run([sys.executable, "-c", code, mode, str(tmp_path)], check=True, capture_output=True, timeout=30)
