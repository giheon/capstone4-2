"""Repeatable API evaluation; source-span coverage is independent of chunk IDs.

The fixture corpus checks software integration, never legal correctness. Face
quality metrics require explicit annotations in normalized video coordinates.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from privframe.domain.models import Box, Contract, DecisionPlan, ScenarioInput, Treatment, now


class ExpectedSourceSpan(Contract):
    document_id: str
    text: str | None = None
    article: str | None = None
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def has_locator(self):
        if not (self.text and self.text.strip()) and not (self.article and self.article.strip()):
            raise ValueError("An expected source span needs text or an article locator")
        return self


class EvaluationDocument(Contract):
    id: str
    path: str
    title: str | None = None
    source_url: str | None = None
    kind: str = "uploaded"


class FaceAnnotation(Contract):
    frame: int = Field(ge=0)
    box: Box


class FaceAnnotations(Contract):
    frame_basis: Literal["normalized_cfr_zero_based"] = "normalized_cfr_zero_based"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    exhaustive: bool = False
    annotated_frames: list[int] = Field(default_factory=list)
    faces: list[FaceAnnotation] = Field(default_factory=list)
    iou_threshold: float = Field(default=0.5, gt=0, le=1)


class ManualFaceExpectation(Contract):
    has_actual_faces: bool | None = None
    expected_track_count: int | None = Field(default=None, ge=0)
    annotations: FaceAnnotations | None = None


class EvaluationCase(Contract):
    id: str
    video: str
    context: str
    facts: dict[str, str] = Field(default_factory=dict)
    track_labels: dict[str, str] = Field(default_factory=dict)
    expected_source_spans: list[ExpectedSourceSpan] = Field(default_factory=list)
    manual_face_expectation: ManualFaceExpectation | None = None
    render: bool = False
    manual_render_treatment: Treatment | None = None
    confirm_no_faces: bool = False

    @model_validator(mode="after")
    def explicit_render(self):
        if self.render and (self.manual_render_treatment is None or self.manual_render_treatment.action == "KEEP"):
            raise ValueError("Fixture rendering requires an explicit protective treatment for every detected track")
        return self


class EvaluationManifest(Contract):
    schema_version: Literal["1"] = "1"
    label: Literal["FIXTURE_SYNTHETIC", "REFERENCE_EVALUATION"]
    mode: Literal["fixture", "real"]
    documents: list[EvaluationDocument] = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)
    timeout_seconds: float = Field(default=1200, gt=0, le=7200)
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=5)

    @model_validator(mode="after")
    def consistent(self):
        for entries in (self.documents, self.cases):
            if len({entry.id for entry in entries}) != len(entries):
                raise ValueError("Document and case IDs must be unique")
        known = {document.id for document in self.documents}
        for case in self.cases:
            if any(span.document_id not in known for span in case.expected_source_spans):
                raise ValueError("Expected source spans must name a manifest document")
            if self.mode != "fixture" and case.render:
                raise ValueError("Automatic evaluation rendering is restricted to explicit fixture mode")
        if self.mode == "fixture" and self.label != "FIXTURE_SYNTHETIC":
            raise ValueError("Fixture evaluation must be labeled FIXTURE_SYNTHETIC")
        return self


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def _compact(text: str) -> tuple[str, list[int]]:
    positions = [index for index, char in enumerate(text) if not char.isspace()]
    return "".join(text[index] for index in positions), positions


def _occurrences(text: str, needle: str):
    position = 0
    while (position := text.find(needle, position)) >= 0:
        yield position
        position += 1


def source_span_recall(expected: list[ExpectedSourceSpan | dict], candidates: list[dict],
                       documents: dict[str, dict]) -> dict[str, Any]:
    """Check source-character coverage, including spans split across adjacent chunks.

    Whitespace removed by a splitter does not count as missing substantive text.
    Every supplied character interval is checked against the extracted source
    unit; joining unrelated chunks or finding the same quote in another document
    cannot turn a missed reference into a hit.
    """
    results = []
    for raw in expected:
        span = ExpectedSourceSpan.model_validate(raw)
        document = documents.get(span.document_id)
        valid, found = False, False
        locator = span.text if span.text and span.text.strip() else span.article or ""
        needle, _ = _compact(locator)
        if document:
            for unit in document.get("units", []):
                if span.page is not None and unit.get("page") != span.page:
                    continue
                unit_text = unit.get("text", "")
                unit_compact, positions = _compact(unit_text)
                if span.article and _compact(span.article)[0] not in _compact(unit.get("article") or unit_text)[0]:
                    continue
                occurrences = list(_occurrences(unit_compact, needle))
                if not occurrences:
                    continue
                valid = True
                intervals = []
                for chunk in candidates:
                    if chunk.get("document_id") != document.get("id"):
                        continue
                    if chunk.get("page") != unit.get("page") or chunk.get("article") != unit.get("article"):
                        continue
                    offset, content = chunk.get("offset", 0), chunk.get("text", "")
                    if (isinstance(offset, int) and offset >= 0 and isinstance(content, str)
                            and unit_text[offset:offset + len(content)] == content):
                        intervals.append((offset, offset + len(content)))
                for occurrence in occurrences:
                    needed = positions[occurrence:occurrence + len(needle)]
                    if all(any(start <= char < end for start, end in intervals) for char in needed):
                        found = True
                        break
                if found:
                    break
        results.append({**span.model_dump(), "valid_reference": valid, "retrieved": found})
    invalid = sum(not item["valid_reference"] for item in results)
    hits = sum(item["retrieved"] for item in results)
    return {"expected": len(results), "matched": hits, "invalid_reference_count": invalid,
            "recall": hits / len(results) if results and not invalid else None, "spans": results,
            "matching": "document_and_source_character_coverage_whitespace_insensitive"}


def decision_quality(run: dict) -> dict[str, Any]:
    observation = run.get("observation") or {}
    expected = {track["id"] for track in observation.get("tracks", [])}
    result = run.get("decision") or {}
    raw = result.get("plan")
    if raw is None:
        return {"plan_schema_valid": False, "track_map_complete": False,
                "decision_count": 0, "error": result.get("error") or "No decision plan"}
    try:
        plan = DecisionPlan.model_validate(raw)
    except ValueError as error:
        return {"plan_schema_valid": False, "track_map_complete": False,
                "decision_count": len(raw.get("decisions", [])) if isinstance(raw, dict) else 0,
                "error": str(error)}
    ids = [decision.track_id for decision in plan.decisions]
    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    provided = {chunk["id"] for chunk in (run.get("evidence") or {}).get("provided", [])}
    bad_citations = [citation for decision in plan.decisions for citation in decision.evidence_ids
                     if citation not in provided]
    return {
        "plan_schema_valid": True, "decision_count": len(ids),
        "track_map_complete": not duplicates and set(ids) == expected,
        "missing_track_ids": sorted(expected - set(ids)), "unknown_track_ids": sorted(set(ids) - expected),
        "duplicate_track_ids": sorted(duplicates), "invalid_evidence_id_count": len(bad_citations),
        "uncited_decision_count": sum(not decision.evidence_ids for decision in plan.decisions),
        "needs_review": plan.needs_review, "missing_facts": list(plan.missing_facts), "error": result.get("error"),
    }


def _iou(first: dict, second: dict) -> float:
    left, top = max(first["x"], second["x"]), max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / (first["width"] * first["height"] + second["width"] * second["height"] - intersection)


def face_metrics(observation: dict, expectation: ManualFaceExpectation | None) -> dict | None:
    if expectation is None:
        return None
    count = len(observation.get("tracks", []))
    result = {"has_actual_faces": expectation.has_actual_faces, "expected_track_count": expectation.expected_track_count,
              "observed_track_count": count,
              "track_count_error": count - expectation.expected_track_count
              if expectation.expected_track_count is not None else None, "annotated_box_metrics": None}
    annotations = expectation.annotations
    if annotations is None:
        return result
    if (annotations.width != observation["width"] or annotations.height != observation["height"]
            or not math.isclose(annotations.fps, observation["fps"], abs_tol=0.01)):
        result["annotation_error"] = "Annotations do not use this observation's normalized frame basis"
        return result
    known = set(annotations.annotated_frames) | {face.frame for face in annotations.faces}
    if annotations.exhaustive:
        known = set(range(observation["frame_count"]))
    if any(frame < 0 or frame >= observation["frame_count"] for frame in known):
        result["annotation_error"] = "Annotated frame is outside the normalized video"
        return result
    if any(face.box.x + face.box.width > annotations.width or face.box.y + face.box.height > annotations.height
           for face in annotations.faces):
        result["annotation_error"] = "Annotated box is outside the normalized frame"
        return result
    ground = [face.model_dump() for face in annotations.faces]
    predicted = [point for track in observation.get("tracks", []) for point in track["points"]
                 if point["frame"] in known]
    pairs = sorted((-_iou(first["box"], second["box"]), first_index, second_index)
                   for first_index, first in enumerate(ground)
                   for second_index, second in enumerate(predicted)
                   if first["frame"] == second["frame"]
                   and _iou(first["box"], second["box"]) >= annotations.iou_threshold)
    matched_ground, matched_predicted = set(), set()
    for _, first, second in pairs:
        if first not in matched_ground and second not in matched_predicted:
            matched_ground.add(first)
            matched_predicted.add(second)
    matches = len(matched_ground)
    result["annotated_box_metrics"] = {
        "iou_threshold": annotations.iou_threshold, "exhaustive": annotations.exhaustive,
        "annotated_frame_count": len(known), "ground_truth_boxes": len(ground), "detected_boxes": len(predicted),
        "true_positives": matches, "false_negatives": len(ground) - matches,
        "false_positives": len(predicted) - matches, "recall": matches / len(ground) if ground else None,
        "precision": matches / len(predicted) if predicted else None,
    }
    return result


def _json(response: httpx.Response) -> Any:
    response.raise_for_status()
    return response.json()


def _wait(client: httpx.Client, kind: str, record_id: str, manifest: EvaluationManifest) -> dict:
    deadline = time.monotonic() + manifest.timeout_seconds
    active = {"UPLOADED", "QUEUED", "INDEXING", "ANALYZING", "DECIDING", "RENDERING"}
    while time.monotonic() < deadline:
        if kind == "documents":
            records = _json(client.get("/api/documents"))
            record = next((item for item in records if item["id"] == record_id), None)
        else:
            record = _json(client.get(f"/api/{kind}/{record_id}"))
        if record is not None and record["status"] not in active:
            return record
        time.sleep(manifest.poll_interval_seconds)
    raise TimeoutError(f"Evaluation timed out waiting for {kind} {record_id}")


def _summary(cases: list[dict]) -> dict:
    scores = [case["retrieval"] for case in cases if case.get("retrieval") is not None]
    expected = sum(score["expected"] for score in scores)
    matched = sum(score["matched"] for score in scores)
    invalid = sum(score["invalid_reference_count"] for score in scores)
    quality = [case["decision_quality"] for case in cases if case.get("decision_quality") is not None]
    return {
        "case_count": len(cases), "completed_decisions": len(quality),
        "failed_case_count": sum(case.get("error") is not None for case in cases),
        "source_spans_expected": expected, "source_spans_matched": matched,
        "invalid_reference_count": invalid,
        "retrieved_source_span_recall": matched / expected if expected and not invalid else None,
        "schema_valid_case_count": sum(item["plan_schema_valid"] for item in quality),
        "complete_track_map_case_count": sum(item["track_map_complete"] for item in quality),
        "decision_error_case_count": sum(bool(item.get("error")) for item in quality),
        "invalid_evidence_id_count": sum(item.get("invalid_evidence_id_count", 0) for item in quality),
        "uncited_decision_count": sum(item.get("uncited_decision_count", 0) for item in quality),
        "legal_correctness": None, "reidentification_risk": None,
    }


def evaluate_manifest(client: httpx.Client, manifest: Path, profile: str) -> dict[str, Any]:
    manifest = manifest.resolve()
    settings = EvaluationManifest.model_validate_json(manifest.read_text(encoding="utf-8"))
    profiles = _json(client.get("/api/profiles"))
    selected = next((entry["config"] for entry in profiles if entry["name"] == profile), None)
    if selected is None:
        raise ValueError(f"Unknown evaluation profile: {profile}")
    if selected.get("mode") != settings.mode:
        raise ValueError(f"Manifest mode={settings.mode} differs from selected profile mode={selected.get('mode')}")
    paths = {entry.id: (manifest.parent / entry.path).resolve() for entry in settings.documents}
    videos = {case.id: (manifest.parent / case.video).resolve() for case in settings.cases}
    for path in [*paths.values(), *videos.values()]:
        if not path.is_file():
            raise ValueError(f"Evaluation asset missing: {path}; create the demo or provide your annotated assets")
    result: dict[str, Any] = {
        "schema_version": "1", "started_at": now(), "manifest_hash": _digest(manifest),
        "manifest": str(manifest), "label": settings.label, "mode": settings.mode,
        "profile": profile, "selected_config": selected, "measurement_scope": "integration_and_reference_retrieval",
        "status": "RUNNING", "documents": [], "index": None, "cases": [],
    }
    documents = {}
    started = time.monotonic()
    try:
        for entry in settings.documents:
            path = paths[entry.id]
            digest = _digest(path)
            uploaded_at = time.monotonic()
            with path.open("rb") as handle:
                record = _json(client.post("/api/documents", files={"file": (path.name, handle)}, data={
                    "title": entry.title or path.name, "kind": entry.kind, "source_url": entry.source_url or "",
                }))
            document_log = {"manifest_id": entry.id, "record_id": record["id"], "path": str(path),
                            "content_hash": digest, "status": record["status"]}
            result["documents"].append(document_log)
            if record.get("content_hash") != digest:
                raise ValueError("Uploaded document hash differs from the evaluation asset")
            record = _wait(client, "documents", record["id"], settings)
            document_log.update(status=record["status"], metadata=record["metadata"],
                                extraction_seconds=time.monotonic() - uploaded_at, error=record.get("error"))
            if record["status"] != "READY":
                raise RuntimeError(record.get("error") or f"Document extraction failed: {record['status']}")
            documents[entry.id] = record
        index_start = time.monotonic()
        index = _json(client.post("/api/indexes", json={"document_ids": [doc["id"] for doc in documents.values()],
                                                       "profile": profile}))
        result["index"] = index
        index = _wait(client, "indexes", index["id"], settings)
        result["index"] = {**index, "client_elapsed_seconds": time.monotonic() - index_start}
        if index["status"] != "READY":
            raise RuntimeError(index.get("error") or "Evaluation index failed")
        for case in settings.cases:
            case_start = time.monotonic()
            item: dict[str, Any] = {"id": case.id, "scenario": {"context": case.context, "facts": case.facts,
                                                                "track_labels": case.track_labels},
                                    "video_path": str(videos[case.id]), "run_id": None, "status": "RUNNING"}
            result["cases"].append(item)
            try:
                scenario = ScenarioInput(context=case.context, facts=case.facts)
                with videos[case.id].open("rb") as handle:
                    video = _json(client.post("/api/videos", files={"file": (videos[case.id].name, handle)}))
                item["video_id"], item["original_hash"] = video["id"], video["content_hash"]
                if video["content_hash"] != _digest(videos[case.id]):
                    raise ValueError("Uploaded video hash differs from the evaluation asset")
                run = _json(client.post("/api/runs", json={"video_id": video["id"], "index_id": index["id"],
                                                           "profile": profile, "scenario": scenario.model_dump()}))
                item["run_id"] = run["id"]
                item["config"] = run["config"]
                run = _wait(client, "runs", run["id"], settings)
                if run["status"] != "AWAITING_LABELS":
                    raise RuntimeError(run.get("error") or f"Analysis failed: {run['status']}")
                if case.track_labels:
                    run = _json(client.post(f"/api/runs/{run['id']}/labels", json={"track_labels": case.track_labels}))
                _json(client.post(f"/api/runs/{run['id']}/decide"))
                run = _wait(client, "runs", run["id"], settings)
                if run["status"] not in {"REVIEW_READY", "REVIEW_REQUIRED"}:
                    raise RuntimeError(run.get("error") or f"Decision failed: {run['status']}")
                observation, evidence = run["observation"], run.get("evidence") or {}
                item.update(
                    status=run["status"], config=run["config"],
                    analysis_fingerprint=observation["analysis_fingerprint"], working_hash=observation["working_hash"],
                    detector=observation["detector"], tracker=observation["tracker"],
                    track_count=len(observation["tracks"]), frame_count=observation["frame_count"],
                    retrieval=source_span_recall(case.expected_source_spans, evidence.get("candidates", []), documents),
                    provided_source_span_recall=source_span_recall(case.expected_source_spans,
                                                                  evidence.get("provided", []), documents),
                    decision_quality=decision_quality(run), review_reasons=run.get("review_reasons", []),
                    usage=(run.get("decision") or {}).get("usage", {}),
                    decision_model=(run.get("decision") or {}).get("model"),
                    face_metrics=face_metrics(observation, case.manual_face_expectation),
                    stages=run.get("stages", []),
                )
                if case.render:
                    if not observation["tracks"] and not case.confirm_no_faces:
                        raise ValueError("Rendering zero detected faces requires explicit confirm_no_faces=true")
                    treatments = {track["id"]: case.manual_render_treatment.model_dump(mode="json")
                                  for track in observation["tracks"]}
                    _json(client.put(f"/api/runs/{run['id']}/overrides", json={
                        "treatments": treatments, "no_faces_confirmed": not observation["tracks"] and case.confirm_no_faces,
                    }))
                    _json(client.post(f"/api/runs/{run['id']}/render"))
                    run = _wait(client, "runs", run["id"], settings)
                    if run["status"] != "COMPLETED":
                        raise RuntimeError(run.get("error") or "Fixture render failed")
                    item.update(status=run["status"], render=run["render"], reports=run["reports"],
                                stages=run["stages"], manual_output=run["manual_output"])
            except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError) as error:
                item["status"], item["error"] = "FAILED", str(error)
            finally:
                item["client_elapsed_seconds"] = time.monotonic() - case_start
        result["status"] = "FAILED" if any(case.get("error") for case in result["cases"]) else "COMPLETED"
    except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError) as error:
        result["status"], result["error"] = "FAILED", str(error)
    result["ended_at"] = now()
    result["client_elapsed_seconds"] = time.monotonic() - started
    result["summary"] = _summary(result["cases"])
    return result
