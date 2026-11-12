"""The web API on the fixture stand-ins: the contract the web app uses, from upload to the report."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from conftest import PIPELINE, manager_for

from privframe.service.app import create_app
from privframe.service.pipeline_store import PipelineStore


@pytest.fixture
def api(workspace, tmp_path):  # noqa: F811
    settings, clip = workspace
    shutil.copytree(PIPELINE, tmp_path / "pipeline")
    manager = manager_for(settings)
    with TestClient(create_app(settings, manager, PipelineStore(tmp_path / "pipeline"))) as client:
        yield client, clip
    manager.close()


def wait_for(client, case_id, status, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        detail = client.get(f"/api/cases/{case_id}").json()
        if detail["status"] == status:
            return detail
        assert detail["status"] != "FAILED", detail.get("error")
        time.sleep(0.05)
    raise AssertionError(f"still {detail['status']} after {seconds}s")


def upload(client, clip, **data):
    return client.post("/api/cases", files={"file": ("인터뷰.mp4", clip.read_bytes(), "video/mp4")}, data=data)


def test_upload_to_report_through_the_contract(api):
    client, clip = api
    config = client.get("/api/config").json()
    assert config["backend"] == "real" and set(config["keys"]) == {"openrouter", "anthropic", "langsmith"}
    assert config["keys"]["openrouter"] is False and isinstance(config["keys"]["anthropic"], bool)
    created = upload(client, clip, memo="recording.device=mobile")
    assert created.status_code == 201
    case = created.json()
    assert (case["status"], case["title"], case["video"]["frameCount"], case["video"]["sample"]) == ("QUEUED", "인터뷰.mp4", 24, False)
    case_id = case["id"]

    detail = wait_for(client, case_id, "AWAITING_ANSWERS")
    question = next(q for q in detail["questions"] if q["status"] == "pending")
    assert client.post(f"/api/cases/{case_id}/review", json={"decisions": []}).status_code == 409
    assert client.get(f"/api/cases/{case_id}/frame", params={"t": 1}).headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/cases/{case_id}/media/proxy", headers={"Range": "bytes=0-99"}).status_code == 206
    geometry = client.get(f"/api/cases/{case_id}/geometry").json()
    assert list(geometry) == ["width", "height", "durationSec", "frames", "decisions", "mosaicBlocks"]
    assert (geometry["width"], geometry["height"], geometry["durationSec"], geometry["mosaicBlocks"]) == (320, 240, 2.0, 6)
    assert len(geometry["frames"]) == 24 and geometry["frames"][:3] == [0.0, 0.0833, 0.1667]  # when each original frame starts
    assert geometry["decisions"] == {}  # nothing is planned yet
    # the review screen asks for decisions it has not saved: the renderer's own rules answer, also for a kept target
    unsaved = [
        {"id": "draft-1", "targetId": "person-1", "scope": "face", "method": "keep", "shape": "ellipse", "ranges": [[0.0, 1.9]]},
        {"id": "draft-2", "targetId": "text-1", "scope": "text", "method": "mosaic", "shape": "box", "ranges": [[0.6, 1.4]]},
        {"id": "draft-3", "targetId": "meta-location", "scope": "metadata", "method": "remove", "ranges": []},
    ]
    preview = client.post(f"/api/cases/{case_id}/geometry", json={"decisions": unsaved}).json()["decisions"]
    assert list(preview) == ["draft-1", "draft-2"]  # file metadata has no place on screen
    face, text = preview["draft-1"], preview["draft-2"]
    assert (face["shape"], face["strength"], face["ranges"]) == ("ellipse", 1.0, [[0.0, 2.0]])  # "1.9" means the end of the clip
    assert (text["shape"], text["strength"], text["ranges"]) == ("box", 2.5, [[0.583, 1.417]])  # whole frames at 12 fps
    (start, boxes), (text_start, text_boxes) = face["runs"][0], text["runs"][0]
    assert len(face["runs"]) == 1 and (start, len(boxes)) == (0, 24) and (text_start, len(text_boxes)) == (7, 10)  # a box on every frame of the range
    assert boxes[0] == boxes[23] == [0.37, 0.138, 0.26, 0.364] and all(0 <= v <= 1 for box in [*boxes, *text_boxes] for v in box)
    assert client.post(f"/api/cases/{case_id}/geometry", json={"decisions": [{"id": "draft-4", "scope": "face"}]}).status_code == 422

    assert client.post(f"/api/cases/{case_id}/answers", json={"answers": [{"questionId": question["id"], "value": "public"}]}).status_code == 202
    detail = wait_for(client, case_id, "AWAITING_REVIEW")
    planned = client.get(f"/api/cases/{case_id}/geometry").json()["decisions"]
    assert list(planned) == [d["id"] for d in detail["plan"]["decisions"]] and {d["method"] for d in detail["plan"]["decisions"]} == {"mosaic"}
    assert all(item["ranges"] == [[0.0, 2.0]] and [len(run[1]) for run in item["runs"]] == [24] for item in planned.values())
    # a malformed edit is refused at the door and the case stays in review (inside the run it would fail the case)
    first = detail["plan"]["decisions"][0]["id"]
    for bad in ({"id": first, "shape": "star"}, {"id": first, "ranges": [[0.5]]}, {"id": first, "method": "blur"}, {"method": "keep"}):
        assert client.post(f"/api/cases/{case_id}/review", json={"decisions": [bad]}).status_code == 422
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "AWAITING_REVIEW"
    assert client.post(f"/api/cases/{case_id}/review", json={"decisions": detail["plan"]["decisions"]}).status_code == 202
    detail = wait_for(client, case_id, "COMPLETED")
    assert len(detail["report"]["verification"]) == 10 and detail["report"]["verification"][7]["check"] == "모자이크 적용 (전체 프레임)"
    assert client.get(detail["media"]["output"], params={"download": True}).headers["content-disposition"].startswith("attachment")
    assert "초 단위 스크립트" in client.get(detail["media"]["reportHtml"]).text
    exported = client.get(f"/api/cases/{case_id}/report.json")
    assert exported.json()["profile"]["id"] == "baseline-v2" and "attachment" in exported.headers["content-disposition"]
    assert [c["id"] for c in client.get("/api/cases").json()] == [case_id]
    run = client.get("/api/lab/runs").json()[0]
    assert (run["id"], run["status"], run["questions"], run["live"]) == (case_id, "COMPLETED", 1, False) and run["toolCalls"] >= 3


def test_a_file_that_is_not_a_video_is_refused(api):
    client, _ = api
    response = client.post("/api/cases", files={"file": ("notes.mp4", b"not a video", "video/mp4")})
    assert response.status_code == 400 and "영상을 읽을 수 없습니다" in response.json()["detail"]
    assert client.get("/api/cases").json() == []
    assert client.get("/api/cases/nope").status_code == 404
    assert [p for p in client.app.state.manager.cases_dir.iterdir() if p.is_dir()] == []  # nothing of the refused upload stays on disk


def test_going_back_to_the_review_is_refused_once_the_audit_rounds_are_used_up(api):
    client, clip = api
    case_id = upload(client, clip).json()["id"]
    detail = wait_for(client, case_id, "AWAITING_ANSWERS")
    client.post(f"/api/cases/{case_id}/answers", json={"answers": [{"questionId": q["id"], "value": "public"} for q in detail["questions"]]})

    def keep_the_face():
        plan = wait_for(client, case_id, "AWAITING_REVIEW")["plan"]
        face = next(d for d in plan["decisions"] if d["targetId"] == "person-1")
        assert client.post(f"/api/cases/{case_id}/review", json={"decisions": [{**face, "method": "keep"}]}).status_code == 202
        return wait_for(client, case_id, "AWAITING_AUDIT_CONFIRM")

    keep_the_face()
    assert client.post(f"/api/cases/{case_id}/audit", json={"resolutions": [], "backToReview": True}).status_code == 202
    finding = keep_the_face()["findings"][0]
    refused = client.post(f"/api/cases/{case_id}/audit", json={"resolutions": [], "backToReview": True})
    assert refused.status_code == 409 and "횟수" in refused.json()["detail"]
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "AWAITING_AUDIT_CONFIRM"  # nothing was applied for the user
    assert client.post(f"/api/cases/{case_id}/audit", json={"resolutions": [{"findingId": finding["id"], "action": "apply"}]}).status_code == 202
    wait_for(client, case_id, "COMPLETED", 300)


def test_a_draft_with_an_empty_value_is_checked_not_crashed(api):
    client, _ = api
    response = client.post("/api/lab/profiles/check", json={"params": {"rag.top_k": None}})
    assert response.status_code == 200 and isinstance(response.json()["issues"], list)


def test_lab_serves_the_topology_catalog_and_profiles(api):
    client, _ = api
    graph = client.get("/api/lab/graph").json()
    assert any(e["id"] == "await_review->legal_analyst/model" for e in graph["edges"]) and "M4" in graph["version"]
    catalog = client.get("/api/lab/catalog").json()
    assert sum(len(stage["slots"]) for stage in catalog["stages"]) == 27
    profiles = {p["id"]: p for p in client.get("/api/lab/profiles").json()}
    baseline = profiles["baseline-v2"]
    assert (baseline["configHash"], baseline["service"], baseline["gate"]) == ("e8a35ef0", ["default"], "not_run")
    assert baseline["estimate"]["framesLeaveDevice"] is True and baseline["estimate"]["costUsd"] > 0
    cheaper = client.post("/api/lab/profiles/check", json={"selections": {"llm.analyst": "claude-haiku-5.5"}}).json()
    assert cheaper["estimate"]["costUsd"] < baseline["estimate"]["costUsd"]
    assert client.post("/api/lab/experiments").status_code == 501
    assert client.post("/api/lab/profiles/baseline-v2/promote").status_code == 409
    assert client.post("/api/lab/runs", json={}).status_code == 409  # no example video in this data directory
    assert client.get("/api/lab/datasets").json()["datasets"]


def wait_experiment(client, experiment_id, want, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        experiment = client.get(f"/api/lab/stage-experiments/{experiment_id}").json()
        if experiment["status"] in want:
            return experiment
        time.sleep(0.1)
    raise AssertionError(f"experiment stayed {experiment['status']}")


def test_a_section_experiment_grows_through_the_api_and_waits_for_the_users_answers(api):
    client, clip = api
    assert [e["id"] for e in client.get("/api/lab/evaluators").json()] == ["expected"]
    videos = client.post("/api/lab/video-sets", json={"name": "시험 영상", "sendable": True}).json()
    video = client.post(f"/api/lab/video-sets/{videos['id']}/items", files={"file": ("clip.mp4", clip.read_bytes(), "video/mp4")}, data={"title": "클립", "expected": '{"people": 1}'}).json()
    assert "answers" not in video and video["expected"] == {"people": 1}
    launch = {"datasets": [videos["id"]]}

    preview = client.post("/api/lab/stage-experiments/preview", json={"from": "analysis", "baseProfile": "baseline-v2", **launch}).json()
    assert preview["interactive"] and preview["problems"] == [] and [(r["start"], r["asks"], r["interactive"]) for r in preview["runs"]] == [("scratch", ["identity", "questions"], True)]
    created = client.post("/api/lab/stage-experiments", json={"from": "analysis", "baseProfile": "baseline-v2", "title": "분석 비교", **launch})
    assert created.status_code == 201, created.text
    experiment = wait_experiment(client, created.json()["id"], ("waiting",))
    run = experiment["runs"][0]
    assert (experiment["title"], run["waitingFor"], experiment["progress"]) == ("분석 비교", "questions", {"done": 0, "total": 1, "waiting": 1})
    listed = next(r for r in client.get("/api/lab/runs").json() if r["id"] == run["caseId"])
    assert (listed["waitingFor"], listed["interactive"], listed["experimentId"], listed["autopilot"]) == ("questions", True, experiment["id"], False)
    assert client.get("/api/cases").json() == []  # opened from the Lab, not listed among the uploader's own work
    # the person answers in the service screen's own way
    detail = client.get(f"/api/cases/{run['caseId']}").json()
    assert detail["experimentId"] == experiment["id"]
    question = next(q for q in detail["questions"] if q["status"] == "pending")
    assert client.post(f"/api/cases/{run['caseId']}/answers", json={"answers": [{"questionId": question["id"], "value": "public"}]}).status_code == 202
    experiment = wait_experiment(client, experiment["id"], ("idle",))
    assert experiment["runs"][0]["status"] == "completed" and client.get(f"/api/cases/{run['caseId']}").json()["stoppedAfter"] == "legal_analyst"

    # the plan continues the answered run; two combinations at once, since it asks nothing
    planners = [c["id"] for stage in client.get("/api/lab/catalog").json()["stages"] for slot in stage["slots"] if slot["id"] == "llm.planner" for c in slot["candidates"] if not c.get("planned")][:2]
    plan = client.post("/api/lab/stage-experiments", json={"from": "plan", "baseProfile": "baseline-v2", "matrix": {"llm.planner": planners}, **launch})
    assert plan.status_code == 201, plan.text
    plan = wait_experiment(client, plan.json()["id"], ("idle",))
    assert [(r["status"], r["sourceCaseId"], r["startSection"]) for r in plan["runs"]] == [("completed", run["caseId"], "plan")] * 2
    more = client.post(f"/api/lab/stage-experiments/{plan['id']}/runs", json={"matrix": {"llm.planner": planners[:1]}, **launch})
    assert more.status_code == 201 and len(more.json()["runs"]) == 3
    plan = wait_experiment(client, plan["id"], ("idle",))
    scored = client.put(f"/api/lab/stage-experiments/{plan['id']}/runs/r1/scores", json={"evaluator": "notebook", "scores": [{"metric": "plan.coverage", "value": 0.5, "direction": "higher"}]}).json()
    assert next(s for s in scored["summary"] if s["variant"] == scored["runs"][0]["variant"])["scores"]["plan.coverage"]["mean"] == 0.5
    assert client.put(f"/api/lab/stage-experiments/{plan['id']}/runs/r1/scores", json={"evaluator": "notebook", "scores": [{"value": 1}]}).status_code == 422
    assert client.patch(f"/api/lab/stage-experiments/{plan['id']}", json={"title": "계획 비교", "costCapUsd": 9}).json()["costCapUsd"] == 9.0
    assert len(client.delete(f"/api/lab/stage-experiments/{plan['id']}/runs/r3").json()["runs"]) == 2
    assert client.post(f"/api/lab/stage-experiments/{plan['id']}/stop").status_code == 409
    assert client.delete(f"/api/lab/stage-experiments/{plan['id']}").status_code == 200
    assert [e["id"] for e in client.get("/api/lab/stage-experiments").json()] == [experiment["id"]]
    assert any(r["id"] == plan["runs"][0]["caseId"] for r in client.get("/api/lab/runs").json())  # the runs stay in the run list
