import hashlib
import json
from pathlib import Path

import httpx
import pytest

from privframe.adapters.rag import LangChainChunker
from privframe.domain.models import DocumentRecord, SourceMetadata, SourceUnit
from privframe.evaluation import (
    EvaluationManifest, ManualFaceExpectation, decision_quality, evaluate_manifest, face_metrics, source_span_recall,
)


def test_reference_span_survives_chunking_and_ids_but_not_missing_source_characters():
    text = " ".join(f"개인정보검증문장{number:03d}" for number in range(70))
    record = DocumentRecord(id="server-id", filename="gold.txt", format="txt", content_hash="hash",
                            metadata=SourceMetadata(title="gold"), units=[SourceUnit(text=text, page=3, article="제1조")])
    expected = [{"document_id": "manifest-id", "page": 3, "article": "제1조", "text": text[90:270]}]
    documents = {"manifest-id": record.model_dump()}
    configurations = ({"chunk_size": 100, "chunk_overlap": 12}, {"chunk_size": 180, "chunk_overlap": 30})
    versions = []
    for config in configurations:
        chunks = [chunk.model_dump() for chunk in LangChainChunker(config).split(record)]
        versions.append(chunks)
        assert source_span_recall(expected, chunks, documents)["recall"] == 1
    assert {part["id"] for part in versions[0]}.isdisjoint(part["id"] for part in versions[1])
    removed = [part for part in versions[0] if not part["offset"] <= 150 < part["offset"] + len(part["text"])]
    assert source_span_recall(expected, removed, documents)["recall"] == 0
    different_document = [{**part, "document_id": "other"} for part in versions[0]]
    assert source_span_recall(expected, different_document, documents)["recall"] == 0
    different_page = [{**part, "page": 4} for part in versions[0]]
    assert source_span_recall(expected, different_page, documents)["recall"] == 0
    invalid = [{**expected[0], "text": "문서에 없는 잘못된 정답 표기"}]
    score = source_span_recall(invalid, versions[0], documents)
    assert score["invalid_reference_count"] == 1 and score["recall"] is None


def test_decision_quality_checks_schema_complete_tracks_and_supplied_citations():
    run = {
        "observation": {"tracks": [{"id": "a"}, {"id": "b"}]},
        "evidence": {"provided": [{"id": "real-source"}]},
        "decision": {"plan": {
            "decisions": [{"track_id": "a", "treatment": {"action": "MASK", "strength": 3},
                           "reason": "test", "evidence_ids": ["made-up-source"]}] * 2,
            "summary": "test", "needs_review": True, "missing_facts": ["source"],
        }},
    }
    result = decision_quality(run)
    assert result["plan_schema_valid"]
    assert not result["track_map_complete"]
    assert result["missing_track_ids"] == ["b"] and result["duplicate_track_ids"] == ["a"]
    assert result["invalid_evidence_id_count"] == 2
    run["decision"]["plan"]["decisions"][0]["treatment"] = {"action": "KEEP", "strength": 3}
    assert not decision_quality(run)["plan_schema_valid"]
    assert not decision_quality({})["plan_schema_valid"]


def test_face_metrics_require_explicit_annotations_in_normalized_basis():
    observation = {"width": 100, "height": 80, "fps": 30, "frame_count": 3, "tracks": [{"id": "one", "points": [
        {"frame": 0, "box": {"x": 10, "y": 10, "width": 20, "height": 20}},
        {"frame": 1, "box": {"x": 60, "y": 40, "width": 20, "height": 20}},
    ]}]}
    assert face_metrics(observation, None) is None
    expectation = ManualFaceExpectation(expected_track_count=2)
    count_only = face_metrics(observation, expectation)
    assert count_only["track_count_error"] == -1
    assert count_only["annotated_box_metrics"] is None
    expectation = ManualFaceExpectation.model_validate({"annotations": {
        "width": 100, "height": 80, "fps": 30, "annotated_frames": [0, 1], "faces": [
            {"frame": 0, "box": {"x": 10, "y": 10, "width": 20, "height": 20}},
            {"frame": 1, "box": {"x": 0, "y": 0, "width": 20, "height": 20}},
        ],
    }})
    metrics = face_metrics(observation, expectation)["annotated_box_metrics"]
    assert metrics["true_positives"] == metrics["false_positives"] == metrics["false_negatives"] == 1
    assert metrics["recall"] == metrics["precision"] == 0.5
    expectation.annotations.fps = 15
    assert face_metrics(observation, expectation)["annotated_box_metrics"] is None
    assert "annotation_error" in face_metrics(observation, expectation)


def manifest_fixture(tmp_path, *, mode="fixture", render=False, confirmed=False):
    document, video = tmp_path / "source.txt", tmp_path / "video.mp4"
    document.write_text("FIXTURE_SYNTHETIC\n가상 검색 정답 문장입니다.", encoding="utf-8")
    video.write_bytes(b"api-client-test-placeholder-not-real-video")
    data = {
        "label": "FIXTURE_SYNTHETIC" if mode == "fixture" else "REFERENCE_EVALUATION", "mode": mode,
        "documents": [{"id": "source", "path": document.name, "kind": "fixture_synthetic"}],
        "cases": [{"id": "case", "video": video.name, "context": "가상 검색 정답 문장", "render": render,
                   "confirm_no_faces": confirmed,
                   "expected_source_spans": [{"document_id": "source", "text": "가상 검색 정답 문장입니다."}],
                   "manual_face_expectation": {"has_actual_faces": False, "expected_track_count": 0}}],
        "poll_interval_seconds": 0.001,
    }
    if render:
        data["cases"][0]["manual_render_treatment"] = {"action": "MASK", "strength": 3}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path, document, video


class EvaluationAPI:
    def __init__(self, document, video, mode="fixture", tracks=None, index_error=False):
        self.mode, self.requests = mode, []
        self.tracks = tracks or []
        self.index_error = index_error
        self.doc = {"id": "doc-id", "status": "READY", "metadata": {"title": "FIXTURE_SYNTHETIC"},
                    "content_hash": hashlib.sha256(document.read_bytes()).hexdigest(),
                    "units": [{"text": document.read_text().strip(), "article": None, "page": None}]}
        self.video_hash = hashlib.sha256(video.read_bytes()).hexdigest()
        self.run = {"id": "run-id", "config": {"mode": mode, "runtime": {"lock_hash": "runtime-hash"}},
                    "status": "AWAITING_LABELS", "observation": {
                        "tracks": self.tracks, "width": 100, "height": 80, "fps": 30, "frame_count": 3,
                        "working_hash": "normalized-hash", "analysis_fingerprint": "analysis-hash",
                        "detector": "haar", "tracker": "iou-greedy-v1",
                    }, "stages": [{"stage": "analyze", "elapsed_seconds": 0.01}]}

    def __call__(self, request):
        path = request.url.path
        self.requests.append((request.method, path, request.content))
        if path == "/api/profiles":
            data = [{"name": "selected", "config": {"mode": self.mode}}]
        elif path == "/api/documents":
            data = [{**self.doc}] if request.method == "GET" else {**self.doc, "status": "UPLOADED"}
        elif path.startswith("/api/indexes"):
            data = {"id": "index-id", "status": "FAILED" if self.index_error else "READY",
                    "error": "embedding provider failed" if self.index_error else None,
                    "config": {"mode": self.mode}, "fingerprint": "index-hash"}
        elif path == "/api/videos":
            data = {"id": "video-id", "content_hash": self.video_hash}
        elif path == "/api/runs/run-id/decide":
            self.run.update(status="REVIEW_REQUIRED", review_reasons=["NO_FACES_DETECTED"], evidence={
                "candidates": [{"id": "chunk-changing-id", "document_id": "doc-id", "text": self.doc["units"][0]["text"],
                                "offset": 0, "page": None, "article": None}],
                "provided": [{"id": "chunk-changing-id", "document_id": "doc-id", "text": self.doc["units"][0]["text"],
                              "offset": 0, "page": None, "article": None}],
            }, decision={"model": "fixture-only", "usage": {}, "plan": {"decisions": [], "summary": "FIXTURE ONLY",
                                                                         "needs_review": False, "missing_facts": []}})
            data = self.run
        elif path == "/api/runs/run-id/overrides":
            data = self.run
        elif path == "/api/runs/run-id/render":
            self.run.update(status="COMPLETED", render={"artifact": "output.mp4", "content_hash": "output-hash"},
                            reports={}, manual_output=True)
            data = self.run
        elif path in {"/api/runs", "/api/runs/run-id"}:
            data = self.run
        else:
            return httpx.Response(404, json={"detail": "unexpected route"})
        return httpx.Response(200, json=data)


def test_evaluator_uses_api_and_preserves_synthetic_label_hashes_ids_and_null_quality(tmp_path):
    path, document, video = manifest_fixture(tmp_path)
    api = EvaluationAPI(document, video)
    with httpx.Client(base_url="http://evaluation.test", transport=httpx.MockTransport(api)) as client:
        result = evaluate_manifest(client, path, "selected")
    assert result["status"] == "COMPLETED" and result["label"] == "FIXTURE_SYNTHETIC"
    assert result["summary"]["retrieved_source_span_recall"] == 1
    assert result["summary"]["schema_valid_case_count"] == 1
    assert result["summary"]["legal_correctness"] is None
    case = result["cases"][0]
    assert case["run_id"] == "run-id" and case["working_hash"] == "normalized-hash"
    assert case["config"]["runtime"]["lock_hash"] == "runtime-hash"
    assert case["track_count"] == 0 and case["face_metrics"]["annotated_box_metrics"] is None
    assert case["review_reasons"] == ["NO_FACES_DETECTED"]
    assert not any(route.endswith("/render") for _, route, _ in api.requests)
    assert ("GET", "/api/documents") in [(method, route) for method, route, _ in api.requests]
    json.dumps(result, ensure_ascii=False)  # Entire CLI result remains JSON-compatible.


def test_mode_mismatch_is_rejected_before_mutation_and_real_label_is_distinct(tmp_path):
    path, document, video = manifest_fixture(tmp_path)
    api = EvaluationAPI(document, video, mode="real")
    with httpx.Client(base_url="http://evaluation.test", transport=httpx.MockTransport(api)) as client:
        with pytest.raises(ValueError, match="differs"):
            evaluate_manifest(client, path, "selected")
    assert all(method == "GET" for method, _, _ in api.requests)
    data = json.loads(path.read_text())
    data.update(mode="real", label="REFERENCE_EVALUATION")
    path.write_text(json.dumps(data))
    with httpx.Client(base_url="http://evaluation.test", transport=httpx.MockTransport(api)) as client:
        result = evaluate_manifest(client, path, "selected")
    assert result["mode"] == "real" and result["label"] == "REFERENCE_EVALUATION"


def test_failed_index_is_recorded_without_running_or_faking_cases(tmp_path):
    path, document, video = manifest_fixture(tmp_path)
    api = EvaluationAPI(document, video, index_error=True)
    with httpx.Client(base_url="http://evaluation.test", transport=httpx.MockTransport(api)) as client:
        result = evaluate_manifest(client, path, "selected")
    assert result["status"] == "FAILED" and "embedding provider failed" in result["error"]
    assert result["index"]["id"] == "index-id" and result["cases"] == []
    assert result["summary"]["retrieved_source_span_recall"] is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_fixture_render_requires_explicit_zero_face_confirmation(tmp_path, confirmed):
    path, document, video = manifest_fixture(tmp_path, render=True, confirmed=confirmed)
    api = EvaluationAPI(document, video)
    with httpx.Client(base_url="http://evaluation.test", transport=httpx.MockTransport(api)) as client:
        result = evaluate_manifest(client, path, "selected")
    renders = [request for request in api.requests if request[1].endswith("/render")]
    assert bool(renders) == confirmed
    if confirmed:
        body = json.loads(next(content for method, route, content in api.requests if route.endswith("/overrides")))
        assert body == {"treatments": {}, "no_faces_confirmed": True}
        assert result["cases"][0]["manual_output"]
    else:
        assert result["status"] == "FAILED" and "confirm_no_faces" in result["cases"][0]["error"]


def test_real_render_and_implicit_fixture_reference_label_are_rejected(tmp_path):
    path, _, _ = manifest_fixture(tmp_path, mode="real", render=True)
    with pytest.raises(ValueError, match="restricted"):
        EvaluationManifest.model_validate_json(path.read_text())
    data = json.loads(path.read_text())
    data.update(mode="fixture", label="REFERENCE_EVALUATION")
    data["cases"][0]["render"] = False
    with pytest.raises(ValueError, match="labeled FIXTURE_SYNTHETIC"):
        EvaluationManifest.model_validate(data)


def test_fixed_manifest_is_readable_and_all_corpus_documents_are_synthetic():
    root = Path(__file__).resolve().parents[2]
    manifest = EvaluationManifest.model_validate_json((root / "evaluation/fixture-manifest.json").read_text())
    assert manifest.mode == "fixture" and manifest.label == "FIXTURE_SYNTHETIC"
    for document in manifest.documents:
        assert document.kind == "fixture_synthetic"
        assert (root / "evaluation" / document.path).read_text().startswith("FIXTURE_SYNTHETIC")
