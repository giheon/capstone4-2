"""The whole case graph on the fixture stand-ins (FR-13): no keys, no network, no model downloads.

What is real here: LangGraph execution and checkpoints, the create_agent loop with its budgets, the citation
validator, the question gate and interrupt, the planner's validator, review edits, the audit, the original
stream render, output verification and the report. What is scripted: model replies and detections.
"""
import json
from pathlib import Path

import pytest

from conftest import make_clip, manager_for

from privframe.core import video
from privframe.core.fixture import Fixture
from privframe.service.runner import RunError

CHECKS = [
    "프레임 수", "PTS 목록", "오디오 framemd5", "메타·챕터·회전·색 정보", "픽셀 포맷 · 프로파일", "처리 영역 밖 PSNR-Y / SSIM", "VMAF (전체 프레임)",
    "모자이크 적용 (전체 프레임)", "처리 영역 재식별 (얼굴 대조 · 글자 재인식)", "모자이크한 대상의 노출 (전체 프레임 · 화면 전체)",
]  # fmt: skip
LOCATION = {"location": "+37.5665+126.9780/"}  # ISO 6709, as a phone writes where a clip was shot


def kinds(case) -> list[str]:
    return [e["kind"] for e in case.events]


def location_tags(path) -> list[str]:
    return video.location_tags(video.ffprobe_json(Path(path)))


def checks(report) -> list[str]:
    """Names of the verification rows, without the time base the PTS row carries."""
    return [row["check"].split(" (time_base")[0] for row in report["verification"]]


def test_case_runs_to_questions_then_review_and_cites_only_known_evidence(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4", memo="recording.device=mobile")
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    question = next(q for q in case.detail["questions"] if q["status"] == "pending")
    assert question["factKey"] == "purpose.publication_scope" and question["impact"]
    assert {f["key"] for f in case.detail["facts"]} >= {"recording.device"}
    assert case.detail["perception"] == {"scenes": 1, "people": 1, "plates": 0, "texts": 1, "tracks": 3}

    manager.resume(case, "questions", {question["id"]: "public"}, "Q1=public")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    detail = case.detail
    assert [a["applicability"] for a in detail["analyses"]] == ["applies"]
    assert all(c["id"].startswith("pipa#") for a in detail["analyses"] for c in a["citations"])
    assert {r["scope"] for r in detail["requirements"]} == {"face", "text"}
    fact = next(f for f in detail["facts"] if f["key"] == "purpose.publication_scope")
    assert (fact["value"], fact["provenance"]) == ("public", "answered")
    assert [t["id"] for t in detail["targets"]] == ["person-1", "text-1"]  # the file records no location: nothing but what is on screen
    drawn = {d["targetId"]: (d["method"], d["scope"], d["shape"], d["ranges"]) for d in detail["plan"]["decisions"]}
    # one way to redact, and to the end of the 2.0 s clip although the last analysis sample is a frame before it
    assert drawn == {"person-1": ("mosaic", "face", "ellipse", [(0.0, 2.0)]), "text-1": ("mosaic", "text", "box", [(0.0, 2.0)])}
    assert not any("alternatives" in d for d in detail["plan"]["decisions"])
    seen = set(kinds(case))
    assert {"run.start", "run.status", "node.start", "node.end", "edge", "llm", "tool.call", "tool.result", "message", "state", "interrupt", "resume", "checkpoint", "budget", "validator", "activity"} <= seen
    nodes = {e["node"] for e in case.events if e["kind"] == "node.start"}
    assert {"ingest", "perceive", "perceive/assemble", "legal_analyst", "legal_analyst/model", "legal_analyst/search_law", "legal_analyst/ask_user", "await_answers", "legal_analyst/finalize", "legal_analyst/validate", "plan", "review_guide", "await_review"} <= nodes
    assert not any("." in n for n in nodes)  # middleware hooks are not topology nodes
    edges = {e["edge"] for e in case.events if e["kind"] == "edge"}
    assert {"__start__->ingest", "perceive->legal_analyst/model", "legal_analyst/validate->plan", "perceive/ocr->perceive/assemble", "perceive/describe_persons->perceive/assemble"} <= edges
    manager.close()


def test_validator_sends_a_bad_citation_back_and_the_retry_passes(workspace):
    settings, clip = workspace
    manager = manager_for(settings, Fixture(ask=False, bad_first_submission=True))
    case = manager.create(clip, "clip.mp4")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    verdicts = [(e["ok"], e["issues"]) for e in case.events if e["kind"] == "validator" and e["node"] == "legal_analyst/validate"]
    assert verdicts[0][0] is False and "없는 근거 ID" in verdicts[0][1][0]
    assert verdicts[-1][0] is True
    assert any(e["kind"] == "edge" and e["edge"] == "legal_analyst/validate->legal_analyst/model" for e in case.events)
    assert case.detail["questions"] == []
    manager.close()


def test_wrong_stage_actions_are_refused(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4")
    manager.wait(case, 120)
    with pytest.raises(RunError) as error:
        manager.resume(case, "review", {"action": "approve"}, "x")
    assert error.value.status == 409
    manager.close()


def to_review(manager, clip, **kwargs):
    case = manager.create(clip, "clip.mp4", **kwargs)
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    question = next(q for q in case.detail["questions"] if q["status"] == "pending")
    manager.resume(case, "questions", {question["id"]: "public"}, "Q1=public")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    return case


def test_approved_plan_is_rendered_verified_and_reported(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    manager.resume(case, "review", {"action": "approve", "decisions": []}, "수정 없음")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    report = case.detail["report"]
    assert checks(report) == CHECKS and not [r for r in report["verification"] if r["verdict"] == "fail"]
    applied = report["verification"][7]
    assert (applied["verdict"], applied["original"], applied["output"]) == ("pass", "가려야 할 프레임 24개 (결정 2건)", "24 / 24 프레임에 모자이크")
    assert [r["verdict"] for r in report["verification"][8:]] == ["info", "info"]  # the fixture has no detector to look at the output with
    assert {row["treatment"] for row in report["script"]} == {"얼굴 모자이크 · 타원", "글자 모자이크 · 사각형"}
    assert {(row["start"], row["end"]) for row in report["script"]} == {(0.0, 2.0)}
    assert report["citations"] and report["notices"][-1].startswith("이 보고서는 법률 자문이 아닙니다")
    assert any("음성은 처리하지 않았습니다" in n for n in report["notices"])
    output = next((case.dir / "output").iterdir())
    assert output.name == "clip.privframe.mp4" and output.stat().st_size > 0
    assert (case.dir / "report.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert case.events[-1] == {**case.events[-1], "kind": "run.end", "status": "COMPLETED"}
    assert [(s["label"], s["state"]) for s in case.detail["renderSteps"]][-1] == ("출력 검증", "done")
    assert [s["state"] for s in case.detail["renderSteps"]] == ["done"] * 4
    assert location_tags(output) == [] and not any("위치 메타데이터" in n for n in report["notices"])
    manager.close()


def test_audit_catches_a_review_edit_that_breaks_a_requirement(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    face = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    manager.resume(case, "review", {"action": "approve", "decisions": [{**face, "method": "keep"}]}, "얼굴 유지로 변경")
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"
    finding = case.detail["findings"][0]
    assert (finding["type"], finding["targetId"], finding["status"]) == ("requirement_unmet", "person-1", "open")
    assert any(e.startswith("검토 변경 D-1") for e in manager.state(case)["edits"])
    manager.resume(case, "audit", {"resolutions": [{"findingId": finding["id"], "action": "apply"}]}, "적용 1")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    restored = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    assert (restored["method"], restored["source"]) == ("mosaic", "auditor")
    assert case.detail["findings"][0]["status"] == "applied"
    manager.close()


def test_the_audit_offers_the_return_to_the_review_only_while_rounds_are_left(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)

    def keep_the_face():
        face = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
        manager.resume(case, "review", {"action": "approve", "decisions": [{**face, "method": "keep"}]}, "얼굴 유지로 변경")
        assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"

    keep_the_face()
    assert case.detail["canReturnToReview"] is True
    manager.resume(case, "audit", {"resolutions": [], "backToReview": True}, "계획으로 돌아가 직접 수정")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    keep_the_face()
    assert case.detail["canReturnToReview"] is False
    # a reply that asks for it anyway is not read as "apply every recommendation": the case asks again
    manager.resume(case, "audit", {"resolutions": [], "backToReview": True}, "계획으로 돌아가 직접 수정")
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM" and case.detail["findings"][0]["status"] == "open"
    manager.close()


def test_changing_a_fact_in_review_reanalyses_and_replans(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    assert {d["method"] for d in case.detail["plan"]["decisions"]} == {"mosaic"}
    manager.resume(case, "review", {"action": "facts", "facts": [{"key": "purpose.publication_scope", "value": "private"}]}, "사실 수정")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    assert case.detail["requirements"] == []
    assert {d["method"] for d in case.detail["plan"]["decisions"]} == {"keep"}
    assert case.detail["plan"]["version"] == 2
    assert any(e["kind"] == "edge" and e["edge"] == "await_review->legal_analyst/model" for e in case.events)
    manager.close()


def test_a_case_survives_a_restart_while_waiting_for_the_user(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4")
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    events_before = len(case.events)
    manager.close()

    restarted = manager_for(settings)
    again = restarted.get(case.id)
    assert again.status == "AWAITING_ANSWERS" and len(again.events) == events_before
    question = next(q for q in again.detail["questions"] if q["status"] == "pending")
    restarted.resume(again, "questions", {question["id"]: "later"}, "나중에")
    assert restarted.wait(again, 120) == "AWAITING_REVIEW"
    assert [e["seq"] for e in again.events] == list(range(1, len(again.events) + 1))
    assert next(q for q in again.detail["questions"] if q["id"] == question["id"])["status"] == "deferred"
    restarted.close()


def test_a_log_line_cut_off_when_the_server_stopped_is_dropped_on_the_next_start(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4")
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    count = len(case.events)
    manager.close()
    log = case.dir / "events.jsonl"
    with log.open("a", encoding="utf-8") as file:
        file.write('{"seq": 999, "kind": "activ')  # the write the stop interrupted

    restarted = manager_for(settings)
    again = restarted.get(case.id)
    assert len(again.events) == count and log.read_text(encoding="utf-8").endswith("}\n")
    question = next(q for q in again.detail["questions"] if q["status"] == "pending")
    restarted.resume(again, "questions", {question["id"]: "public"}, "공개 장소")
    assert restarted.wait(again, 120) == "AWAITING_REVIEW"
    assert [json.loads(line)["seq"] for line in log.read_text(encoding="utf-8").splitlines()] == list(range(1, len(again.events) + 1))
    restarted.close()


def test_a_case_the_server_stopped_before_its_first_step_starts_from_its_upload_when_continued(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4", start=False)  # still in the queue when the server stops
    manager.close()

    restarted = manager_for(settings)
    again = restarted.get(case.id)
    assert (again.status, again.detail["pauseReason"]) == ("PAUSED", "restart")
    restarted.proceed(again)
    assert restarted.wait(again, 120) == "AWAITING_ANSWERS", again.detail.get("error")
    restarted.close()


def test_an_unstarted_case_can_be_taken_back(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4", start=False)
    manager.discard(case)
    assert manager.list() == [] and not case.dir.exists()
    manager.close()


def test_cost_cap_pauses_and_a_higher_cap_continues(workspace):
    settings, clip = workspace
    manager = manager_for(settings, **{"case.cost_cap_usd": 0.01})
    case = manager.create(clip, "clip.mp4", start=False)
    manager._services(case).cost.add(0.02)  # as if earlier model calls had already cost more than the cap
    manager._launch(case, {"upload": {"path": case.meta["original"], "filename": "clip.mp4", "memo": "", "mode": "anonymized_upload"}}, first=True)
    assert manager.wait(case, 120) == "PAUSED"
    assert case.detail["pauseReason"] == "budget"
    with pytest.raises(RunError):
        manager.proceed(case)
    manager.proceed(case, cost_cap_usd=1.0)
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    manager.close()


def test_an_uncertain_person_grouping_is_asked_before_the_legal_analysis(workspace):
    settings, clip = workspace
    manager = manager_for(settings, Fixture(ambiguity=True))
    case = manager.create(clip, "clip.mp4")
    assert manager.wait(case, 120) == "AWAITING_IDENTITY"
    question = case.detail["identity"]
    assert [c["target"] for c in question["candidates"]] == ["person-1", "person-2"] and case.detail["perception"]["people"] == 2
    manager.resume(case, "identity", "same", "같은 사람")
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    assert case.detail["perception"]["people"] == 1 and case.detail["identity"]["answer"] == "same"
    assert [t["id"] for t in case.detail["targets"] if t["kind"] == "person"] == ["person-1"]
    edges = [e["edge"] for e in case.events if e["kind"] == "edge"]
    assert "perceive->await_identity" in edges and "await_identity->legal_analyst/model" in edges and "perceive->legal_analyst/model" not in edges
    manager.close()


def test_a_question_answered_late_in_review_changes_the_plan(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = manager.create(clip, "clip.mp4")
    assert manager.wait(case, 120) == "AWAITING_ANSWERS"
    question = next(q for q in case.detail["questions"] if q["status"] == "pending")
    manager.resume(case, "questions", {question["id"]: "later"}, "나중에")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    assert {d["method"] for d in case.detail["plan"]["decisions"]} == {"mosaic"}  # unanswered: the conservative plan
    manager.resume(case, "review", {"action": "answers", "answers": {question["id"]: "private"}}, "늦은 답변")
    assert manager.wait(case, 120) == "AWAITING_REVIEW"
    answered = next(q for q in case.detail["questions"] if q["id"] == question["id"])
    assert (answered["status"], answered["answer"]) == ("answered", "private")
    assert {d["method"] for d in case.detail["plan"]["decisions"]} == {"keep"}
    manager.close()


def test_rejecting_an_audit_finding_is_recorded_as_accepted_risk(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    face = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    manager.resume(case, "review", {"action": "approve", "decisions": [{**face, "method": "keep"}]}, "얼굴 유지")
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"
    finding = case.detail["findings"][0]
    manager.resume(case, "audit", {"resolutions": [{"findingId": finding["id"], "action": "reject", "reason": "본인 영상"}]}, "위험 수용 1")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    assert next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")["method"] == "keep"
    assert (case.detail["findings"][0]["status"], case.detail["findings"][0]["rejectReason"]) == ("rejected", "본인 영상")
    assert any("위험 수용" in line and "본인 영상" in line for line in case.detail["report"]["history"])
    manager.close()


def test_tools_called_alongside_a_question_run_once_and_are_reported_once(workspace):
    settings, clip = workspace
    manager = manager_for(settings, Fixture(ask_with_other_tools=True))
    case = to_review(manager, clip)
    results = [e for e in case.events if e["kind"] == "tool.result" and e["tool"] == "checklist"]
    starts = [e for e in case.events if e["kind"] == "node.start" and e["node"] == "legal_analyst/checklist"]
    budget = [e["used"] for e in case.events if e["kind"] == "budget" and e["key"] == "tool_calls"]
    assert len(results) == 1 and len(starts) == 1
    assert budget == sorted(set(budget))  # the count never repeats or goes back
    ids = [e["id"] for e in case.events if e["kind"] == "checkpoint"]
    assert len(ids) == len(set(ids))
    manager.close()


def test_review_edits_keep_the_one_method_of_a_target_and_are_fitted_to_frames(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    face = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    text = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "text-1")
    # methods that do not exist for these targets, and "1.9" for a face that is there until the clip ends at 2.0
    edits = [{**face, "method": "remove", "ranges": [[0.0, 1.9]]}, {**text, "method": "blur", "ranges": [[0.04, 1.96]]}]
    manager.resume(case, "review", {"action": "approve", "decisions": edits}, "바뀌지 않는 수정")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")  # nothing changed: no audit finding, straight to the render
    after = {d["targetId"]: (d["method"], d["ranges"], d["edited"]) for d in case.detail["plan"]["decisions"]}
    assert after == {"person-1": ("mosaic", [(0.0, 2.0)], False), "text-1": ("mosaic", [(0.0, 2.0)], False)}
    assert case.detail["plan"]["version"] == 1 and not manager.state(case).get("edits")
    assert case.detail["report"]["verification"][7]["output"] == "24 / 24 프레임에 모자이크"
    manager.close()


def test_a_review_edit_that_cuts_a_range_short_is_aligned_to_frames_and_caught_by_the_audit(workspace):
    settings, clip = workspace
    manager = manager_for(settings)
    case = to_review(manager, clip)
    face = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    manager.resume(case, "review", {"action": "approve", "decisions": [{**face, "ranges": [[0.6, 1.4]]}]}, "구간 축소")  # well inside the appearance
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"
    cut = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    # at 12 fps: the frames on screen between 0.6 and 1.4 s are 7 (from 0.583) to 16 (until 1.417)
    assert (cut["ranges"], cut["edited"], cut["source"]) == ([(0.583, 1.417)], True, "user")
    assert any(e.startswith("검토 변경 D-1") and "구간 수정" in e for e in manager.state(case)["edits"])
    finding = case.detail["findings"][0]
    assert (finding["type"], finding["targetId"], finding["ranges"]) == ("requirement_unmet", "person-1", [(0.0, 2.0)])
    manager.resume(case, "audit", {"resolutions": [{"findingId": finding["id"], "action": "apply"}]}, "적용 1")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    restored = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "person-1")
    assert [tuple(r) for r in restored["ranges"]] == [(0.0, 2.0)] and restored["source"] == "auditor"
    assert case.detail["report"]["verification"][7]["output"] == "24 / 24 프레임에 모자이크"
    manager.close()


def test_location_metadata_is_a_target_and_is_removed_from_the_output(workspace):
    settings, clip = workspace
    located = clip.with_name("located.mp4")
    make_clip(located, tags=LOCATION)
    assert "location" in location_tags(located) and location_tags(clip) == []
    manager = manager_for(settings)
    case = to_review(manager, located)
    detail = case.detail
    assert [(t["id"], t["kind"], t["ranges"]) for t in detail["targets"]][-1] == ("meta-location", "metadata", []) and len(detail["targets"]) == 3
    fact = next(f for f in detail["facts"] if f["key"] == "file.location_metadata")
    assert (fact["value"], fact["provenance"], fact["editable"]) == ("present", "inferred", False)
    wanted = next(r for r in detail["requirements"] if r["targets"] == ["meta-location"])
    assert (wanted["scope"], wanted["ranges"]) == ("metadata", []) and {r["scope"] for r in detail["requirements"]} == {"face", "text", "metadata"}
    removal = next(d for d in detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    assert (removal["method"], removal["scope"], removal["ranges"], removal["satisfies"]) == ("remove", "metadata", [], [wanted["id"]])
    assert "shape" not in removal and removal["targetLabel"] == "촬영 위치 정보"
    assert {d["targetId"]: d["method"] for d in detail["plan"]["decisions"]} == {"person-1": "mosaic", "text-1": "mosaic", "meta-location": "remove"}

    manager.resume(case, "review", {"action": "approve", "decisions": []}, "수정 없음")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    output = next((case.dir / "output").iterdir())
    assert location_tags(output) == []  # gone from the copy …
    assert "location" in location_tags(case.meta["original"])  # … and the preserved original is as it was uploaded
    report = case.detail["report"]
    assert checks(report) == [*CHECKS, "위치 메타데이터 삭제"] and not [r for r in report["verification"] if r["verdict"] == "fail"]
    row = report["verification"][-1]
    assert (row["verdict"], row["output"]) == ("pass", "남은 태그 없음") and "location" in row["original"]
    assert report["verification"][3]["verdict"] == "pass" and "계획에 따라 삭제한 태그" in report["verification"][3]["note"]
    whole_file = [r for r in report["script"] if r["scene"] == "파일 전체"]
    assert [(r["target"], r["treatment"], r["start"], r["end"]) for r in whole_file] == [("촬영 위치 정보", "삭제", 0.0, 2.0)]
    assert any("촬영 위치 메타데이터" in n and "삭제했습니다" in n for n in report["notices"])
    assert "<td>삭제</td>" in (case.dir / "report.html").read_text(encoding="utf-8")
    manager.close()


def test_keeping_the_location_against_a_requirement_is_an_audit_finding_and_an_accepted_risk(workspace):
    settings, clip = workspace
    located = clip.with_name("located.mp4")
    make_clip(located, tags=LOCATION)
    manager = manager_for(settings)
    case = to_review(manager, located)
    removal = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    # a mosaic is not something a file tag can get: that edit is not taken, keeping it is
    manager.resume(case, "review", {"action": "approve", "decisions": [{**removal, "method": "mosaic", "shape": "box", "ranges": [[0.0, 2.0]]}]}, "없는 처리")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    unchanged = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    assert (unchanged["method"], unchanged["ranges"], unchanged["edited"]) == ("remove", [], False) and "shape" not in unchanged
    assert location_tags(next((case.dir / "output").iterdir())) == []

    case = to_review(manager, located)
    removal = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    manager.resume(case, "review", {"action": "approve", "decisions": [{**removal, "method": "keep"}]}, "위치 유지")
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"
    finding = case.detail["findings"][0]
    assert (finding["type"], finding["targetId"], finding["ranges"], finding["recommendation"]) == ("requirement_unmet", "meta-location", [], "파일에서 이 정보를 삭제합니다.")
    manager.resume(case, "audit", {"resolutions": [{"findingId": finding["id"], "action": "reject", "reason": "촬영지 공개"}]}, "위험 수용 1")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    assert "location" in location_tags(next((case.dir / "output").iterdir()))  # kept, as decided
    report = case.detail["report"]
    assert checks(report) == CHECKS and not [r for r in report["verification"] if r["verdict"] == "fail"]
    assert any("촬영 위치 메타데이터" in n and "그대로 있습니다" in n for n in report["notices"])
    assert [(r["target"], r["treatment"]) for r in report["script"] if r["scene"] == "파일 전체"] == [("촬영 위치 정보", "유지")]

    case = to_review(manager, located)
    removal = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    manager.resume(case, "review", {"action": "approve", "decisions": [{**removal, "method": "keep"}]}, "위치 유지")
    assert manager.wait(case, 120) == "AWAITING_AUDIT_CONFIRM"
    manager.resume(case, "audit", {"resolutions": [{"findingId": case.detail["findings"][0]["id"], "action": "apply"}]}, "적용 1")
    assert manager.wait(case, 300) == "COMPLETED", case.detail.get("error")
    restored = next(d for d in case.detail["plan"]["decisions"] if d["targetId"] == "meta-location")
    assert (restored["method"], restored["source"], restored["ranges"]) == ("remove", "auditor", [])
    assert location_tags(next((case.dir / "output").iterdir())) == [] and checks(case.detail["report"])[-1] == "위치 메타데이터 삭제"
    manager.close()
