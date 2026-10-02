"""HTTP contracts exercise service transitions and uploaded/artifact boundaries."""

import json

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from privframe.adapters.rag import ChromaRetriever
from privframe.api import create_app
from privframe.bootstrap import create_service
from privframe.domain.models import RunSnapshot
from privframe.infrastructure.storage import Record

import test_application as shared_fixtures

# Reuse the same local-video and pipeline harness through normal pytest fixtures.
blank_video_bytes = shared_fixtures.blank_video_bytes
textured_video_bytes = shared_fixtures.textured_video_bytes
system = shared_fixtures.system


def test_multipart_document_preserves_public_metadata_and_index_contract(system):
    with TestClient(create_app(system.service)) as client:
        uploaded = client.post("/api/documents", files={"file": ("../../자료.txt", "영상 개인정보 보호".encode(), "text/plain")},
                               data={"title": "교육용 자료", "source_url": "https://www.privacy.go.kr/", "kind": "guide"})
        assert uploaded.status_code == 201, uploaded.text
        document = uploaded.json()
        assert document["filename"] == "자료.txt" and document["status"] == "READY"
        assert document["metadata"]["source_url"] == "https://www.privacy.go.kr/"
        assert "page" in document["units"][0] and document["units"][0]["text"] == "영상 개인정보 보호"
        fetched = client.get(f"/api/documents/{document['id']}")
        assert fetched.status_code == 200 and fetched.json() == document
        built = client.post("/api/indexes", json={"document_ids": [document["id"]], "profile": "fixture"})
        assert built.status_code == 202 and built.json()["status"] == "READY", built.text
        retry_same = client.post("/api/indexes", json={"document_ids": [document["id"]], "profile": "fixture"})
        assert retry_same.json()["id"] == built.json()["id"]
        missing = client.post("/api/indexes", json={"document_ids": ["unknown"], "profile": "fixture"})
        assert missing.status_code == 404
        unexpected = client.post("/api/indexes", json={"document_ids": [document["id"]], "policy": "injected"})
        assert unexpected.status_code == 422


def test_invalid_metadata_does_not_leave_orphan_uploaded_artifact(system):
    before = set(system.service.settings.data_dir.rglob("original.txt"))
    with TestClient(create_app(system.service)) as client:
        response = client.post("/api/documents", files={"file": ("bad.txt", b"source text", "text/plain")},
                               data={"source_url": "file:///private/source.txt"})
        assert response.status_code in {400, 422}
        assert client.get("/api/documents").json() == []
    assert set(system.service.settings.data_dir.rglob("original.txt")) == before


def test_empty_invalid_pdf_oversized_and_unsupported_uploads_leave_no_artifacts(system):
    system.service.settings.upload_limit_mb = 1
    with TestClient(create_app(system.service)) as client:
        cases = [("empty.txt", b"", 400), ("fake.pdf", b"not a PDF", 400),
                 ("large.txt", b"x" * (1024 * 1024 + 1), 413), ("script.html", b"<script>run()</script>", 400)]
        for filename, content, status in cases:
            response = client.post("/api/documents", files={"file": (filename, content, "application/octet-stream")})
            assert response.status_code == status, response.text
        assert client.get("/api/documents").json() == []
    assert list(system.service.settings.data_dir.rglob("original.txt")) == []
    assert list(system.service.settings.data_dir.rglob("original.pdf")) == []


def test_artifact_route_cannot_traverse_into_private_state_inside_data_directory(system):
    with TestClient(create_app(system.service)) as client:
        valid = client.get(f"/api/artifacts/{system.video.artifact}")
        assert valid.status_code == 200 and valid.content.startswith(b"\x00\x00")
        assert client.get("/api/artifacts/state.sqlite3").status_code == 404
        bypass = client.get("/api/artifacts/runs/%2e%2e/state.sqlite3")
        assert bypass.status_code in {400, 404}, "An allowed namespace must not expose the SQLite database"
        outside = client.get("/api/artifacts/runs/%2e%2e/%2e%2e/outside.txt")
        assert outside.status_code in {400, 404}


def test_http_review_override_gate_and_completed_run_immutability(system):
    with TestClient(create_app(system.service)) as client:
        run = client.post("/api/runs", json={"video_id": system.video.id, "scenario": {"context": "교육용 영상"},
                                            "profile": "fixture"}).json()
        identity = run["id"]
        assert run["status"] == "AWAITING_LABELS"
        assert client.post(f"/api/runs/{identity}/labels", json={"track_labels": {"unknown": "person"}}).status_code == 400
        reviewed = client.post(f"/api/runs/{identity}/decide")
        assert reviewed.status_code == 202 and reviewed.json()["status"] == "REVIEW_REQUIRED"
        assert client.post(f"/api/runs/{identity}/render").status_code == 400
        assert client.put(f"/api/runs/{identity}/overrides", json={"treatments": {
            "track1": {"action": "KEEP", "strength": 2},
        }}).status_code == 422
        overridden = client.put(f"/api/runs/{identity}/overrides", json={"treatments": {
            "track1": {"action": "MASK", "strength": 3}, "track2": {"action": "KEEP", "strength": None},
        }})
        assert overridden.status_code == 200
        completed = client.post(f"/api/runs/{identity}/render")
        assert completed.status_code == 202 and completed.json()["status"] == "COMPLETED", completed.text
        before = client.get(f"/api/runs/{identity}").json()
        assert client.post(f"/api/runs/{identity}/decide").status_code == 400
        assert client.put(f"/api/runs/{identity}/overrides", json={"treatments": {}}).status_code == 400
        assert client.post(f"/api/runs/{identity}/retry", json={"stage": "render"}).status_code == 400
        assert client.get(f"/api/runs/{identity}").json() == before


def test_fork_omitted_index_inherits_but_explicit_null_clears(system):
    parent = system.run(system.add_index())
    with TestClient(create_app(system.service)) as client:
        inherited = client.post(f"/api/runs/{parent.id}/fork", json={})
        cleared = client.post(f"/api/runs/{parent.id}/fork", json={"index_id": None})
        assert inherited.status_code == cleared.status_code == 202
        assert inherited.json()["index_id"] == parent.index_id
        assert cleared.json()["index_id"] is None
        assert system.analyzer.calls == 1


def test_regenerate_report_uses_saved_evidence_after_delete_reindex_and_repository_reopen(system, monkeypatch):
    completed = system.completed()
    old_document_ids = {item.document_id for item in completed.evidence.provided}
    old_collection = system.pipeline.adapters.retriever(completed.config, system.service.artifacts.root / "chroma")
    from privframe.domain.models import IndexRecord
    index = system.service.repository.get("index", completed.index_id, IndexRecord)
    old_collection.client.delete_collection(ChromaRetriever._name(index))
    with Session(system.service.repository.engine) as session, session.begin():
        session.execute(delete(Record).where(Record.kind == "index", Record.id == completed.index_id))
        session.execute(delete(Record).where(Record.kind == "document", Record.id.in_(old_document_ids)))
    replacement = system.add_index(system.add_document("변경된 문서의 새 개인정보 보호 근거"))
    assert replacement.id != completed.index_id
    def never(*args, **kwargs):
        raise AssertionError("Report regeneration must not retrieve or call a model")
    monkeypatch.setattr("privframe.adapters.rag.create_retriever", never)
    monkeypatch.setattr("privframe.adapters.decision.create_decider", never)
    reopened = create_service(system.service.settings, system.dispatcher)
    try:
        with TestClient(create_app(reopened)) as client:
            response = client.post(f"/api/runs/{completed.id}/report")
            assert response.status_code == 200, response.text
            regenerated = response.json()
            assert regenerated["evidence"] == completed.evidence.model_dump(mode="json")
            assert regenerated["decision"] == completed.decision.model_dump(mode="json")
            assert regenerated["render"] == completed.render.model_dump(mode="json")
            report = client.get(f"/api/artifacts/{regenerated['reports']['json']}")
            assert report.status_code == 200
            saved = json.loads(report.content)
            assert saved["evidence"] == regenerated["evidence"]
            assert saved["index_id"] == completed.index_id
            assert saved["config"] == completed.config
            persisted = reopened.repository.get("run", completed.id, RunSnapshot)
            assert persisted.status == "COMPLETED" and persisted.evidence == completed.evidence
    finally:
        reopened.repository.engine.dispose()


def test_failed_document_and_index_retry_return_current_recovered_records(system):
    extractor = system.pipeline.adapters.extractor
    class TemporarilyUnavailable:
        def extract(self, *args):
            raise OSError("Temporary extraction failure")
    system.pipeline.adapters.extractor = TemporarilyUnavailable()
    with TestClient(create_app(system.service)) as client:
        failed = client.post("/api/documents", files={"file": ("source.txt", "영상 개인정보 보호".encode(), "text/plain")})
        assert failed.status_code == 201 and failed.json()["status"] == "FAILED"
        identity = failed.json()["id"]
        system.pipeline.adapters.extractor = extractor
        recovered = client.post(f"/api/documents/{identity}/retry")
        assert recovered.status_code == 200 and recovered.json()["status"] == "READY", recovered.text
        assert recovered.json()["error"] is None
        assert recovered.json() == client.get(f"/api/documents/{identity}").json()
        chunker = system.pipeline.adapters.chunker
        def unavailable(config):
            raise RuntimeError("Temporary indexing failure")
        system.pipeline.adapters.chunker = unavailable
        failed_index = client.post("/api/indexes", json={"document_ids": [identity], "profile": "fixture"})
        assert failed_index.status_code == 202 and failed_index.json()["status"] == "FAILED"
        index_id = failed_index.json()["id"]
        system.pipeline.adapters.chunker = chunker
        indexed = client.post(f"/api/indexes/{index_id}/retry")
        assert indexed.status_code == 202 and indexed.json()["status"] == "READY", indexed.text
        assert indexed.json()["error"] is None and indexed.json()["chunk_count"] > 0
        assert client.post(f"/api/indexes/{index_id}/retry").status_code == 400


def test_corrupt_video_upload_is_saved_but_analysis_fails_without_model_call(system):
    from privframe.bootstrap import create_pipeline
    system.pipeline.adapters.analyzer = create_pipeline(system.service).adapters.analyzer
    def never(config):
        raise AssertionError("A corrupt video must fail before model initialization")
    system.pipeline.adapters.decider = never
    with TestClient(create_app(system.service)) as client:
        uploaded = client.post("/api/videos", files={"file": ("../../broken.mp4", b"not a video", "video/mp4")})
        assert uploaded.status_code == 201 and uploaded.json()["filename"] == "broken.mp4"
        result = client.post("/api/runs", json={"video_id": uploaded.json()["id"], "profile": "fixture",
                                               "scenario": {"context": "형식 확인용 영상"}})
        assert result.status_code == 202 and result.json()["status"] == "FAILED", result.text
        run = result.json()
        assert run["observation"] is None and run["decision"] is None and run["error"]
        assert run["stages"][-1]["stage"] == "analyze" and run["stages"][-1]["status"] == "FAILED"
        assert run["reports"]["json"] and client.get(f"/api/artifacts/{run['reports']['json']}").status_code == 200


def test_http_completed_output_revision_changes_strength_without_repeating_models(system, textured_video_bytes):
    index = system.add_index()
    with TestClient(create_app(system.service)) as client:
        uploaded = client.post("/api/videos", files={"file": ("pattern.mp4", textured_video_bytes, "video/mp4")})
        assert uploaded.status_code == 201
        created = client.post("/api/runs", json={"video_id": uploaded.json()["id"], "index_id": index.id,
                                                "scenario": {"context": "영상 처리 강도 비교"}, "profile": "fixture"})
        identity = created.json()["id"]
        assert client.post(f"/api/runs/{identity}/revise").status_code == 400
        assert client.post(f"/api/runs/{identity}/decide").json()["status"] == "REVIEW_READY"
        parent = client.post(f"/api/runs/{identity}/render").json()
        assert parent["status"] == "COMPLETED", parent["error"]
        def never(*args, **kwargs):
            raise AssertionError("Revision must use the stored analysis and decision")
        system.pipeline.adapters.analyzer.analyze = never
        system.pipeline.adapters.analyzer.fingerprint = never
        system.pipeline.adapters.retriever = never
        system.pipeline.adapters.decider = never
        revision = client.post(f"/api/runs/{identity}/revise")
        assert revision.status_code == 201, revision.text
        revised = revision.json()
        new_id = revised["id"]
        assert new_id != identity and revised["parent_id"] == identity
        assert revised["reused_stages"] == {"analyze": identity, "decide": identity}
        assert revised["stages"] == [] and revised["render"] is None and revised["reports"] == {}
        assert revised["evidence"] == parent["evidence"] and revised["decision"] == parent["decision"]
        assert client.put(f"/api/runs/{new_id}/overrides", json={"treatments": {
            "track1": {"action": "BLUR", "strength": 3},
        }}).status_code == 200
        result = client.post(f"/api/runs/{new_id}/render")
        assert result.status_code == 202 and result.json()["status"] == "COMPLETED", result.text
        rendered = result.json()
        assert [stage["stage"] for stage in rendered["stages"]] == ["render"]
        assert rendered["effective_treatments"]["track1"] == {"action": "BLUR", "strength": 3}
        assert rendered["render"]["content_hash"] != parent["render"]["content_hash"]
        assert client.get(f"/api/runs/{identity}").json() == parent
        assert client.post("/api/runs/not-found/revise").status_code == 404
