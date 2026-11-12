"""Section experiments on the fixture stand-ins: sets of runs that grow, runs that continue an earlier run's
checkpoint, and ranges that ask the user (never answered for them)."""
import threading
import time

import pytest

from privframe.core import stages
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.service import evaluation
from privframe.service.datasets import DatasetStore
from privframe.service.experiments import ExperimentError, ExperimentManager

from conftest import PIPELINE, manager_for

BASE = "baseline-v2"
NOTES = {"title": "클립", "memo": "recording.device=mobile", "expected": {"people": 1, "plates": 0, "scenes": 2}}


@pytest.fixture
def lab(workspace, tmp_path):  # noqa: F811
    settings, clip = workspace
    runs = manager_for(settings)
    datasets = DatasetStore(tmp_path / "data" / "datasets", lambda: (clip, NOTES))
    experiments = ExperimentManager(runs, datasets, tmp_path / "data" / "experiments")
    yield runs, experiments, datasets, clip
    runs.close()


def other(slot: str, count: int = 1) -> list[str]:
    """Candidates of a part that are not the base profile's own."""
    catalog = load_catalog(PIPELINE)
    own = resolve(catalog, load_profile(PIPELINE, BASE)).part(slot).candidate.id
    return [c.id for c in catalog.slot(slot).candidates if c.id != own and not c.planned][:count]


def settle(experiments, experiment_id, want=("idle", "waiting"), seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        experiment = experiments.get(experiment_id)
        if experiment["status"] in want:
            return experiment
        time.sleep(0.05)
    raise AssertionError(f"experiment stayed {experiment['status']}: {[(r['id'], r['status'], r['error']) for r in experiment['runs']]}")


def answer(runs, case_id):
    """Answer the analyst's question the way a person would in the service screen."""
    case = runs.get(case_id)
    question = next(q for q in case.detail["questions"] if q["status"] == "pending")
    runs.resume(case, "questions", {question["id"]: "public"}, "Q1=public")


def nodes(runs, case_id) -> set[str]:
    return {e["node"] for e in runs.get(case_id).events if e["kind"] == "node.start"}


def answered_analysis(runs, experiments):
    """An analysis experiment with one run the user answered."""
    experiment = experiments.start({"from": "analysis", "baseProfile": BASE, "datasets": ["sample"]})
    experiment = settle(experiments, experiment["id"], want=("waiting",))
    answer(runs, experiment["runs"][0]["caseId"])
    return settle(experiments, experiment["id"], want=("idle",))


def test_sections_say_where_the_user_is_asked(lab):
    _, experiments, _, _ = lab
    asked = {s["id"]: [i["kind"] for i in s["interactions"]] for s in experiments.sections()}
    assert asked == {"perception": [], "analysis": ["identity", "questions"], "plan": [], "review": ["review", "audit"], "render": []}
    assert [s["entry"] for s in experiments.sections()] == ["perceive", "await_identity", "plan", "await_review", "render_original"]


def test_a_range_that_asks_nothing_runs_a_matrix_and_later_launches_join_the_same_experiment(lab):
    runs, experiments, _, _ = lab
    first, second = other("vlm.scene", 2)
    own = resolve(load_catalog(PIPELINE), load_profile(PIPELINE, BASE)).part("vlm.scene").candidate.id
    experiment = experiments.start({"from": "perception", "baseProfile": BASE, "matrix": {"vlm.scene": [own, first]}, "datasets": ["sample"]})
    assert (experiment["interactive"], experiment["status"]) == (False, "running")
    experiment = settle(experiments, experiment["id"])
    assert [(v["id"], v["base"]) for v in experiment["variants"]] == [("base", True), ("v1", False)]
    assert [(r["id"], r["variant"], r["status"], r["interactive"]) for r in experiment["runs"]] == [("r1", "base", "completed", False), ("r2", "v1", "completed", False)]
    swapped = experiment["runs"][1]
    assert "perceive/detect_track" in swapped["metrics"]["reuse"]["steps"] and "perceive/describe_segments" not in swapped["metrics"]["reuse"]["steps"]
    assert swapped["metrics"]["vsBase"]["agreement"] == 1.0 and experiment["runs"][0]["metrics"]["vsBase"] is None
    assert {s["metric"] for s in swapped["scores"]} == {"expected.people", "expected.plates", "expected.scenes"}  # the dataset says nothing about texts
    assert runs.get(swapped["caseId"]).detail["stoppedAfter"] == "perceive" and not runs.get(swapped["caseId"]).waiting
    assert not runs.get(swapped["caseId"]).meta["autopilot"]  # a range that asks nothing still does not answer for the user

    # another combination later: the same experiment grows, the earlier runs stay
    experiment = experiments.launch(experiment["id"], {"matrix": {"vlm.scene": [second]}, "datasets": ["sample"]})
    experiment = settle(experiments, experiment["id"])
    assert [r["id"] for r in experiment["runs"]] == ["r1", "r2", "r3"] and [v["id"] for v in experiment["variants"]] == ["base", "v1", "v2"]
    # the same combination again is a second run of that combination, not a new one
    experiment = settle(experiments, experiments.launch(experiment["id"], {"matrix": {"vlm.scene": [first]}, "datasets": ["sample"]})["id"])
    summary = {s["variant"]: s for s in experiment["summary"]}
    assert (summary["v1"]["runs"], summary["v1"]["completed"], summary["v1"]["items"]) == (2, 2, 1)
    assert summary["v1"]["scores"]["expected.people"] == {"label": "인물 수 일치", "unit": "ratio", "direction": "higher", "mean": 1.0, "n": 1}
    assert summary["v1"]["byDataset"][0]["dataset"] == "sample" and summary["v1"]["byDataset"][0]["items"] == 1
    assert len(experiments.list()) == 1 and experiments.list()[0]["progress"] == {"done": 4, "total": 4, "waiting": 0}


def test_a_range_that_asks_takes_one_run_at_a_time_and_waits_for_the_person(lab):
    runs, experiments, _, _ = lab
    analysts = other("llm.analyst", 2)
    plan = experiments.preview({"from": "analysis", "baseProfile": BASE, "matrix": {"llm.analyst": analysts}, "datasets": ["sample"]})
    assert plan["interactive"] and [i["kind"] for i in plan["interactions"]] == ["identity", "questions"]
    assert any("한 번에 1건" in p for p in plan["problems"])
    with pytest.raises(ExperimentError, match="한 번에 1건"):
        experiments.start({"from": "analysis", "baseProfile": BASE, "matrix": {"llm.analyst": analysts}, "datasets": ["sample"]})
    assert experiments.list() == []  # a refused launch leaves nothing behind

    experiment = experiments.start({"from": "analysis", "baseProfile": BASE, "matrix": {"llm.analyst": analysts[:1]}, "datasets": ["sample"]})
    experiment = settle(experiments, experiment["id"], want=("waiting",))
    run = experiment["runs"][0]
    assert (run["status"], run["waitingFor"], run["interactive"], run["startSection"]) == ("waiting", "questions", True, "perception")
    case = runs.get(run["caseId"])
    assert case.status == "AWAITING_ANSWERS" and not case.meta["autopilot"] and case.detail["experimentId"] == experiment["id"]
    assert case.run_summary()["waitingFor"] == "questions" and case.run_summary()["interactive"] is True
    time.sleep(0.4)  # nothing answers in the person's place
    assert runs.get(run["caseId"]).status == "AWAITING_ANSWERS"

    answer(runs, run["caseId"])
    experiment = settle(experiments, experiment["id"], want=("idle",))
    run = experiment["runs"][0]
    assert run["status"] == "completed" and run["metrics"]["analysis"]["questions"] == 1 and run["metrics"]["waitSec"] > 0.3
    assert run["metrics"]["elapsedSec"] < run["metrics"]["waitSec"] + 60 and case.detail["stoppedAfter"] == "legal_analyst"
    assert not any(e.get("auto") for e in case.events if e["kind"] == "resume")
    assert "plan" not in nodes(runs, run["caseId"])

    # the next run of the same range is asked again, and does not run perception again
    experiment = experiments.launch(experiment["id"], {"matrix": {"llm.analyst": analysts[:1]}, "datasets": ["sample"]})
    experiment = settle(experiments, experiment["id"], want=("waiting",))
    again = experiment["runs"][1]
    assert (again["waitingFor"], again["sourceCaseId"], again["startSection"], again["forkedAt"]) == ("questions", run["caseId"], "analysis", "legal_analyst")
    assert "perceive" not in nodes(runs, again["caseId"]) and runs.get(again["caseId"]).detail["questions"][0]["status"] == "pending"
    answer(runs, again["caseId"])
    experiment = settle(experiments, experiment["id"], want=("idle",))
    assert [r["status"] for r in experiment["runs"]] == ["completed", "completed"] and experiment["summary"][0]["runs"] == 2


def test_a_range_behind_the_questions_continues_an_answered_run_without_asking_again(lab):
    runs, experiments, _, clip = lab
    planners = other("llm.planner", 2)
    request = {"from": "plan", "baseProfile": BASE, "matrix": {"llm.planner": planners}, "datasets": ["sample"]}

    # nobody has answered for this video yet: an unattended run does not count
    demo = runs.create(clip, "clip.mp4", autopilot=True, autopilot_answers={"purpose.publication_scope": "public"}, kind="demo")
    assert runs.wait(demo, 300) == "COMPLETED"
    plan = experiments.preview(request)
    assert not plan["interactive"] and all(r["source"] is None and "법률 분석" in r["problem"] for r in plan["runs"])
    with pytest.raises(ExperimentError, match="법률 분석"):
        experiments.start(request)
    picked = experiments.preview({**request, "matrix": {"llm.planner": planners[:1]}, "source": demo.id})
    assert "자동으로 답한" in picked["runs"][0]["problem"] and picked["variants"][0]["recomputes"] == ["plan"]

    analysis = answered_analysis(runs, experiments)
    answered = analysis["runs"][0]["caseId"]
    plan = experiments.preview(request)
    assert plan["problems"] == [] and [(r["start"], r["source"]["caseId"], r["startSection"], r["asks"]) for r in plan["runs"]] == [("fork", answered, "plan", [])] * 2
    experiment = settle(experiments, experiments.start(request)["id"], want=("idle",))
    assert [(r["status"], r["start"], r["sourceCaseId"], r["forkedAt"], r["interactive"]) for r in experiment["runs"]] == [("completed", "fork", answered, "plan", False)] * 2
    # naming a run to continue keeps the other choices in view
    named = experiments.preview({"experiment": experiment["id"], "matrix": {"llm.planner": planners[:1]}, "datasets": ["sample"], "source": experiment["runs"][0]["caseId"]})["runs"][0]
    assert named["source"]["caseId"] == experiment["runs"][0]["caseId"] and answered in [c["caseId"] for c in named["candidates"]] and len(named["candidates"]) >= 2
    summary = experiment["summary"][0]
    assert summary["fullSecMean"] >= summary["sectionSecMean"] and summary["fullCostUsdMean"] >= summary["costUsdMean"]
    for run in experiment["runs"]:
        case = runs.get(run["caseId"])
        assert not [e for e in case.events if e["kind"] in ("interrupt", "resume")] and not {"perceive", "legal_analyst"} & nodes(runs, case.id)
        assert run["metrics"]["plan"]["decisions"] == 2 and case.detail["stoppedAfter"] == "review_guide"
        assert [q["answer"] for q in case.detail["questions"]] == ["public"]  # the answer came with the checkpoint
    assert runs.get(answered).status == "COMPLETED" and "plan" not in nodes(runs, answered)  # the continued run is untouched

    # a run made with another planner is not what a review of the base profile's plan continues
    refused = experiments.preview({"from": "review", "baseProfile": BASE, "datasets": ["sample"], "source": experiment["runs"][0]["caseId"]})
    assert "'계획'의 부품이 이 실험의 기준 프로필과 다릅니다" in refused["runs"][0]["problem"]
    experiment = settle(experiments, experiments.launch(experiment["id"], {"datasets": ["sample"]})["id"], want=("idle",))
    planned = experiment["runs"][2]
    assert (planned["variant"], planned["status"], planned["metrics"]["vsBase"]) == ("base", "completed", None)
    assert experiment["runs"][0]["metrics"]["vsBase"]["agreement"] == 1.0  # compared with the base combination once it has run

    # review asks: one run, the person approves, the run ends in front of the render
    review = experiments.start({"from": "review", "baseProfile": BASE, "datasets": ["sample"]})
    review = settle(experiments, review["id"], want=("waiting",))
    waiting = review["runs"][0]
    assert (waiting["waitingFor"], waiting["sourceCaseId"], waiting["startSection"]) == ("review", planned["caseId"], "review")
    runs.resume(runs.get(waiting["caseId"]), "review", {"action": "approve", "decisions": []}, "수정 없음")
    review = settle(experiments, review["id"], want=("idle",))
    done = review["runs"][0]
    assert done["status"] == "completed" and done["metrics"]["review"] == {"edits": 0, "findings": 0, "auditRounds": 0, "redacted": ["person-1:face", "text-1:text"]}
    assert runs.get(done["caseId"]).detail["stoppedAfter"] == "auditor" and "render_original" not in nodes(runs, done["caseId"])

    # and the render continues from the reviewed run, unattended, to the end
    render = settle(experiments, experiments.start({"from": "render", "baseProfile": BASE, "datasets": ["sample"]})["id"], want=("idle",), seconds=300)
    rendered = render["runs"][0]
    assert (rendered["status"], rendered["sourceCaseId"], rendered["interactive"]) == ("completed", done["caseId"], False)
    assert rendered["metrics"]["render"]["failed"] == [] and "await_review" not in nodes(runs, rendered["caseId"])
    assert "stoppedAfter" not in runs.get(rendered["caseId"]).detail


def test_the_experiment_answers_while_a_runs_video_is_copied_and_a_stop_then_takes_the_run_back(lab, monkeypatch):
    runs, experiments, _, _ = lab
    copying, go_on = threading.Event(), threading.Event()
    create = runs.create

    def slow_create(*args, **kwargs):
        case = create(*args, **kwargs)
        copying.set()
        assert go_on.wait(30)
        return case

    monkeypatch.setattr(runs, "create", slow_create)
    experiment = experiments.start({"from": "perception", "baseProfile": BASE, "datasets": ["sample"]})
    assert copying.wait(30)
    # the copy is not done under the manager's lock: reading and stopping do not wait for it
    reader = threading.Thread(target=lambda: experiments.stop(experiment["id"]))
    reader.start()
    reader.join(5)
    assert not reader.is_alive()
    go_on.set()
    experiment = settle(experiments, experiment["id"])
    assert [(r["status"], r["error"], r.get("caseId")) for r in experiment["runs"]] == [("skipped", "실험을 멈춤", None)]
    assert runs.list() == []  # the case that was being made is not left behind


def test_scores_from_outside_are_stored_per_run_and_averaged_per_combination(lab):
    _, experiments, _, _ = lab
    experiment = settle(experiments, experiments.start({"from": "perception", "baseProfile": BASE, "datasets": ["sample"]})["id"])
    experiment = settle(experiments, experiments.launch(experiment["id"], {"datasets": ["sample"]})["id"])
    first, second = experiment["runs"]
    experiments.set_scores(experiment["id"], first["id"], "team-eval", [{"metric": "track.idf1", "label": "추적 IDF1", "value": 0.8, "unit": "ratio", "direction": "higher"}])
    experiment = experiments.set_scores(experiment["id"], second["id"], "team-eval", [{"metric": "track.idf1", "label": "추적 IDF1", "value": 0.6, "unit": "ratio", "direction": "higher"}])
    assert experiment["summary"][0]["scores"]["track.idf1"] == {"label": "추적 IDF1", "unit": "ratio", "direction": "higher", "mean": 0.7, "n": 1}  # one video, two runs
    # the same evaluator again replaces its own scores and leaves the others
    experiment = experiments.set_scores(experiment["id"], second["id"], "team-eval", [{"metric": "track.idf1", "value": 1.0}])
    assert experiment["summary"][0]["scores"]["track.idf1"]["mean"] == 0.9 and {s["evaluator"] for s in experiment["runs"][1]["scores"]} == {"expected", "team-eval"}
    with pytest.raises(ExperimentError, match="수가 아닙니다"):
        experiments.set_scores(experiment["id"], second["id"], "team-eval", [{"metric": "x", "value": "높음"}])
    assert [e["id"] for e in evaluation.describe()] == ["expected"]

    experiment = experiments.delete_run(experiment["id"], first["id"])
    assert [r["id"] for r in experiment["runs"]] == ["r2"] and experiment["summary"][0]["scores"]["track.idf1"]["mean"] == 1.0
    assert experiments.edit(experiment["id"], title="추출 비교", cost_cap_usd=2)["costCapUsd"] == 2.0


def test_a_waiting_run_outlives_the_experiment_manager_and_its_result_is_taken_when_answered(lab):
    runs, experiments, datasets, _ = lab
    experiment = settle(experiments, experiments.start({"from": "analysis", "baseProfile": BASE, "datasets": ["sample"]})["id"], want=("waiting",))
    with pytest.raises(ExperimentError, match="실행 중인 실행이 없습니다"):
        experiments.stop(experiment["id"])
    reopened = ExperimentManager(runs, datasets, experiments.root)  # as after a server restart
    assert reopened.get(experiment["id"])["runs"][0]["status"] == "waiting"
    answer(runs, experiment["runs"][0]["caseId"])
    assert settle(reopened, experiment["id"], want=("idle",))["runs"][0]["status"] == "completed"


def test_the_range_ends_where_its_last_section_ends():
    assert stages.stop_at("perception") == {"after": ["perceive"], "before": [], "shown": "perceive"}
    assert stages.stop_at("review") == {"after": [], "before": ["render_original"], "shown": "auditor"}
    assert stages.stop_at("render") == {}
    assert [s.id for s in stages.span("analysis", "review")] == ["analysis", "plan", "review"]
    with pytest.raises(ValueError):
        stages.span("plan", "analysis")


def test_sections_and_steps_name_the_same_parts():
    # the step store keys a step by its parts and an experiment decides by the section's: the two lists must agree
    for section in stages.SECTIONS:
        steps = [stages.STEPS[node] for node in section.steps if node in stages.STEPS]
        assert {slot for step in steps for slot in step.slots} == set(section.slots), section.id
        assert {param for step in steps for param in step.params} == set(section.params), section.id
