"""Independent component evaluations, driven through the same worker/API paths.

Inputs, API work and output directories belong to the caller's isolated runtime.
No target invokes unrelated detectors, retrieval or downstream LLM calls.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from privframe.cli import wait_record
from privframe.domain.models import ScenarioInput
from privframe.evaluation import (ManualFaceExpectation, decision_quality, face_metrics,
                                 identity_grouping_metrics, source_span_recall)


def evaluate_components(client, manifest_path: Path, profile: str, target: str):
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.resolve().parent
    records = []
    def post(path, **kwargs):
        response = client.post(path, **kwargs)
        response.raise_for_status()
        return response.json()
    for case in manifest["cases"]:
        started = time.monotonic()
        if target == "index":
            documents = []
            for document in case["documents"]:
                path = base / document["path"]
                with path.open("rb") as stream:
                    record = post("/api/documents", files={"file": (path.name, stream)},
                                  data={"title": document.get("title", path.name)})
                ready = wait_record(client, "documents", record["id"])
                if ready["status"] != "READY":
                    raise ValueError(ready.get("error") or "Document extraction failed")
                documents.append(ready["id"])
            index = post("/api/indexes", json={"document_ids": documents, "profile": profile})
            result = wait_record(client, "indexes", index["id"])
            metrics = {"chunks": result["chunk_count"]}
        elif target == "vision":
            path = base / case["video"]
            with path.open("rb") as stream:
                video = post("/api/videos", files={"file": (path.name, stream)})
            scenario = ScenarioInput.model_validate(case.get("scenario", {"use_purpose": "unknown", "audience_scope": "unknown"}))
            run = post("/api/runs", json={"video_id": video["id"], "profile": profile,
                                           "scenario": scenario.model_dump(mode="json")})
            result = wait_record(client, "runs", run["id"])
            if result["status"] == "FAILED":
                raise ValueError(result.get("error") or "Video analysis failed")
            metrics = {"tracks": len(result["observation"]["tracks"]),
                       "persons": len((result.get("identity") or {}).get("groups", [])),
                       "identity": (result.get("identity") or {}).get("metrics", {})}
            expectation = ManualFaceExpectation.model_validate(case["manual_face_expectation"]) if case.get("manual_face_expectation") else None
            metrics["face_quality"] = face_metrics(result["observation"], expectation)
            metrics["grouping_quality"] = identity_grouping_metrics(result["observation"], result.get("identity"), expectation.annotations if expectation else None)
        elif target in {"retrieval", "decision"}:
            if target == "decision":
                snapshot = json.loads((base / case["snapshot"]).read_text())
                payload = {key: snapshot[key] for key in ("scenario", "observation", "evidence")}
            else:
                payload = {"scenario": case["scenario"], "index_id": case.get("index_id", manifest.get("index_id"))}
                if not payload["index_id"]:
                    raise ValueError("Retrieval evaluation requires a fixed index_id")
            execution = post("/api/executions", json={"graph": target, "profile": profile, **payload})
            result = wait_record(client, "executions", execution["id"])
            if result["status"] != "COMPLETED":
                raise ValueError(result.get("error") or "Component did not complete")
            node = "select_evidence" if target == "retrieval" else "validate_output"
            response = client.get(f"/api/executions/{execution['id']}/outputs/{node}")
            response.raise_for_status()
            output = response.json()["result"]
            if target == "retrieval":
                documents = {}
                for span in case.get("expected_source_spans", []):
                    response = client.get(f"/api/documents/{span['document_id']}")
                    response.raise_for_status()
                    documents[span["document_id"]] = response.json()
                metrics = source_span_recall(case.get("expected_source_spans", []), output["evidence"]["provided"], documents)
            else:
                metrics = decision_quality({**payload, **output})
        else:
            raise ValueError("Unknown evaluation target")
        records.append({"id": case["id"], "target": target, "elapsed_seconds": time.monotonic() - started,
                        "metrics": metrics, "result": result})
    return {"schema_version": "1", "target": target, "profile": profile, "cases": records}
