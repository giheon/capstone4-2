"""From requirements to decisions (core/agents/planner.py), and the one target that is not on screen: the
file's location tags. Plain logic on a made-up understanding; the planner model is a scripted stand-in.

A 10 s clip at 30 fps analysed on every 5th frame: person-1 (face and body) is there throughout, a name tag
is read from 2.0 to 4.0 s, a plate is tracked from 5.0 to 6.7 s, person-2 was grouped but never tracked.
"""
from conftest import PIPELINE
import pytest
from pydantic import ValidationError

from privframe.core import knowledge
from privframe.core import understanding as views
from privframe.core.agents import analyst, planner
from privframe.core.agents.planner import DecisionOut, PlanOut, coverage_gap, finish_plan, make_plan, required_ranges, rule_plan, validate_plan
from privframe.core.domain import METHOD_LABEL, REDACT_METHOD, PersonGroup, Plate, Requirement, TextItem, Track, TrackPoint, VideoMeta, VideoUnderstanding
from privframe.core.fixture import as_fixture
from privframe.core.geometry import TOLERANCE_S, Timeline, visible_ranges
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.core.runtime import CostMeter, ModelCallError, Services
from privframe.core.settings import CoreSettings

TIMELINE = Timeline.build([i / 30 for i in range(300)], 10.0, 5 / 30, [(0, 299)])
WHOLE = [(0.0, 10.0)]
TAG_SEEN, PLATE_SEEN = [(1.667, 4.367)], [(4.667, 7.033)]  # readings and samples held for the pad (ten frames) on both sides


def track(track_id: str, kind: str, frames, box, text: str | None = None) -> Track:
    return Track(id=track_id, kind=kind, segment="S1", points=[TrackPoint(f=f, t=f / 30, box=box, score=0.9) for f in frames], text=text)


def meta(*location_tags: str) -> VideoMeta:
    return VideoMeta(
        path="x.mp4", filename="x.mp4", sha256="0", size_bytes=1, container="mp4", duration_s=10.0, frame_count=300, width=1920, height=1080,
        display_width=1920, display_height=1080, codec="h264", time_base=(1, 30), location_tags=list(location_tags),
    )  # fmt: skip


@pytest.fixture
def seen() -> VideoUnderstanding:
    understanding = VideoUnderstanding(
        tracks=[
            track("T1", "face", range(0, 300, 5), (400.0, 200.0, 80.0, 100.0)),
            track("T2", "person", range(0, 300, 5), (340.0, 180.0, 200.0, 600.0)),
            track("TX1", "text", (60, 90, 120), (380.0, 420.0, 120.0, 30.0), text="김민지"),
            track("TP1", "plate", range(150, 201, 5), (1500.0, 900.0, 120.0, 40.0)),
        ],
        persons=[PersonGroup(id="person-1", face_tracks=["T1"], body_tracks=["T2"]), PersonGroup(id="person-2", body_tracks=["T9"])],
        texts=[TextItem(id="text-1", text="김민지", kind="nametag", tracks=["TX1"], attached_person="person-1", confidence=0.9)],
        plates=[Plate(id="plate-1", tracks=["TP1"])],
        sample_interval=5 / 30,
    )
    understanding.visible = visible_ranges(understanding, TIMELINE)
    assert understanding.visible == {"person-1:face": WHOLE, "person-1:body": WHOLE, "person-2:face": [], "person-2:body": [], "text-1:text": TAG_SEEN, "plate-1:plate": PLATE_SEEN}
    return understanding


@pytest.fixture
def targets(seen) -> list[dict]:
    return [target.wire() for target in views.build_targets(seen, meta("location", "location-eng"))]


def requirement(identifier: str, target: str, scope: str, ranges=()) -> dict:
    return Requirement(id=identifier, statement=f"{target}을(를) 식별할 수 없게 처리", targets=[target], ranges=list(ranges), derived_from=["A-1"], scope=scope).wire()


def decided(target: str, scope: str, method: str, satisfies=(), ranges=()) -> dict:
    """A decision as the planner passes it around before ids and labels are attached (wire field names)."""
    return {"targetId": target, "scope": scope, "method": method, "shape": planner.DEFAULT_SHAPE.get(scope), "ranges": list(ranges), "satisfies": list(satisfies), "analysisId": None, "rationale": "이유", "risk": "medium"}


def proposed(target: str, scope: str, method: str, satisfies=(), **extra) -> dict:
    """A decision as the model writes it."""
    return {"target_id": target, "scope": scope, "method": method, "satisfies": list(satisfies), "analysis_id": None, "rationale_ko": "이유", "risk": "medium", **extra}


FACE, NAME, PLATE, LOCATION, UNSEEN = (
    requirement("REQ-1", "person-1", "face"),
    requirement("REQ-2", "text-1", "text"),
    requirement("REQ-3", "plate-1", "plate"),
    requirement("REQ-4", "meta-location", "metadata"),
    requirement("REQ-5", "person-2", "face"),
)


# ---------- the file as a target ----------


def test_the_file_is_a_target_only_when_it_records_where_it_was_shot(seen):
    without = views.build_targets(seen, meta())
    assert [t.id for t in without] == ["person-1", "person-2", "text-1", "plate-1"] == [t.id for t in views.build_targets(seen)]
    located = views.build_targets(seen, meta("location", "com.apple.quicktime.location.ISO6709"))
    assert [t.id for t in located] == ["person-1", "person-2", "text-1", "plate-1", "meta-location"]
    target = located[-1]
    assert views.LOCATION_TARGET == "meta-location" and (target.kind, target.label, target.ranges, target.tracks, target.personal) == ("metadata", "촬영 위치 정보", [], [], True)
    assert "태그 2개" in target.detail and REDACT_METHOD["metadata"] == "remove" and METHOD_LABEL["remove"] == "삭제"
    # the analyst reads it as a fact of the file and as a target that is nowhere on screen
    assert views.file_facts(meta()) == []
    (fact,) = views.file_facts(meta("location"))
    assert (fact.key, fact.value, fact.provenance, fact.editable, fact.confidence) == ("file.location_metadata", "present", "inferred", False, 1.0)
    text = views.observation_text(seen, [t.wire() for t in located], 10.0)
    assert "- meta-location (촬영 위치 정보): " in text and "파일 전체에 적용 (화면에 보이지 않음)" in text
    assert "meta-location" not in views.observation_text(seen, [t.wire() for t in without], 10.0)


def test_location_metadata_hints_at_its_reference_pattern(seen):
    fact = views.file_facts(meta("location"))[0].wire()
    assert "location_metadata" in knowledge.observation_signals(seen, [fact])
    assert "location_metadata" not in knowledge.observation_signals(seen, [])
    pattern = next(p for p in knowledge.load_patterns()["patterns"] if p["id"] == "P18")
    assert pattern["trigger"] == "location_metadata" and "메타데이터" in pattern["keywords"]
    assert "P18 촬영 위치 메타데이터" in knowledge.checklist_text(seen, [fact]).rsplit("이 영상의 관찰과 맞닿는 패턴: ", 1)[1]
    assert "P18" not in knowledge.checklist_text(seen, []).rsplit("이 영상의 관찰과 맞닿는 패턴: ", 1)[1]
    # an analysis that never mentions it leaves the pattern uncovered; one that does covers it
    assert "P18" in [p["id"] for p in knowledge.uncovered_patterns(seen, [fact], [{"question_of_law": "제3자 얼굴", "reasoning_ko": ""}])]
    assert "P18" not in [p["id"] for p in knowledge.uncovered_patterns(seen, [fact], [{"question_of_law": "촬영 위치 좌표 공개", "reasoning_ko": "GPS 좌표"}])]


def test_the_analyst_may_require_metadata_removal_only_for_the_metadata_target(targets):
    evidence = {"pipa#제2조": {"id": "pipa#제2조", "authority": 1}}
    analysis = {"id": "A-1", "question_of_law": "촬영 위치", "applicability": "applies", "reasoning_ko": "", "evidence_ids": ["pipa#제2조"], "affected_targets": ["meta-location"], "depends_on_facts": [], "conservative_assumption": None}

    def submit(target: str, scope: str) -> dict:
        wanted = analyst.RequirementIn(id="REQ-1", statement_ko="삭제", targets=[target], scope=scope, derived_from_analysis_ids=["A-1"])
        return {"analyses": [analysis], "requirements": [wanted.model_dump()], "unresolved": []}

    assert analyst.validate_submission(submit("meta-location", "metadata"), evidence, targets) == []
    assert analyst.validate_submission(submit("meta-location", "face"), evidence, targets) == ["REQ-1: 대상 meta-location에는 scope 'face'를 쓸 수 없습니다 (metadata)."]
    assert analyst.validate_submission(submit("person-1", "metadata"), evidence, targets) == ["REQ-1: 대상 person-1에는 scope 'metadata'를 쓸 수 없습니다 (body · face)."]
    assert analyst.sanitize(submit("person-1", "metadata"), evidence, targets, [])["requirements"] == []
    assert analyst.sanitize(submit("meta-location", "metadata"), evidence, targets, [])["requirements"][0]["targets"] == ["meta-location"]
    with pytest.raises(ValidationError):
        analyst.RequirementIn(id="REQ-1", statement_ko="x", targets=["person-1"], scope="region", derived_from_analysis_ids=["A-1"])


# ---------- is a requirement met? ----------


def test_a_metadata_requirement_is_met_only_by_a_remove_decision(seen):
    remove = decided("meta-location", "metadata", "remove", ["REQ-4"])
    assert coverage_gap(LOCATION, "meta-location", [remove], seen) is None
    assert coverage_gap(LOCATION, "meta-location", [decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE), remove], seen) is None
    for others in (
        [],
        [decided("meta-location", "metadata", "keep", ["REQ-4"])],  # kept
        [decided("meta-location", "metadata", "mosaic", ["REQ-4"], WHOLE)],  # a mosaic does nothing to a file tag
        [decided("meta-location", "metadata", "remove", [])],  # removed, but not for this requirement
        [decided("meta-location", "metadata", "remove", ["REQ-1"])],
        [decided("person-1", "face", "remove", ["REQ-4"])],  # another target
    ):
        gap = coverage_gap(LOCATION, "meta-location", others, seen)
        assert gap is not None and gap.startswith("REQ-4: 대상 meta-location을(를) 삭제하는 결정이 없습니다"), others


def test_a_requirement_on_screen_is_met_by_mosaics_that_cover_every_frame_it_needs(seen):
    def gap(*decisions: dict, needs: dict = FACE) -> str | None:
        return coverage_gap(needs, needs["targets"][0], list(decisions), seen)

    assert required_ranges(FACE, seen, "person-1") == WHOLE
    assert gap(decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE)) is None
    assert gap(decided("person-1", "body", "mosaic", ["REQ-1"], WHOLE)) is None  # the body includes the face
    assert gap(decided("person-1", "face", "mosaic", ["REQ-1"], [(0.0, 5.0)]), decided("person-1", "face", "mosaic", ["REQ-1"], [(5.0, 10.0)])) is None
    assert gap(decided("person-1", "face", "mosaic", ["REQ-1"], [(TOLERANCE_S, 10.0 - TOLERANCE_S)])) is None  # a rounding of the stored milliseconds
    # "0.0–9.9" leaves the last three frames of a 10 s video uncovered: the requirement is not met
    short = gap(decided("person-1", "face", "mosaic", ["REQ-1"], [(0.0, 9.9)]))
    assert short is not None and "다 덮지 못합니다" in short and "9.9" in short
    assert "다 덮지 못합니다" in gap(decided("person-1", "face", "mosaic", ["REQ-1"], [(0.0, 9.967)]))  # one frame short is short
    assert "다 덮지 못합니다" in gap(decided("person-1", "face", "mosaic", ["REQ-1"], [(0.0, 4.0), (6.0, 10.0)]))
    for useless in (
        decided("person-1", "face", "keep", ["REQ-1"], WHOLE),
        decided("person-1", "face", "remove", ["REQ-1"], WHOLE),  # not a way to redact a person
        decided("person-1", "face", "mosaic", [], WHOLE),  # not linked to the requirement
        decided("text-1", "text", "mosaic", ["REQ-1"], WHOLE),
    ):
        assert "처리 결정이 없습니다" in gap(useless)
    assert "처리 결정이 없습니다" in gap(decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE), needs={**FACE, "scope": "body"})  # a face mosaic is not the body
    # stated ranges are cut to where the target is visible; outside them nothing is needed
    part = {**NAME, "ranges": [(3.0, 6.0)]}
    assert required_ranges(part, seen, "text-1") == [(3.0, 4.367)]
    assert gap(decided("text-1", "text", "mosaic", ["REQ-2"], [(3.0, 4.367)]), needs=part) is None
    assert gap(needs={**NAME, "ranges": [(8.0, 9.0)]}) is None and gap(needs=UNSEEN) is None  # never visible there: nothing to cover


# ---------- the rule-based plan ----------


def test_the_rule_plan_removes_metadata_and_mosaics_everything_on_screen(seen, targets):
    again = {**requirement("REQ-6", "person-1", "face", [(2.0, 3.0)]), "statement": "같은 얼굴에 대한 두 번째 요건"}
    requirements = [FACE, NAME, PLATE, LOCATION, UNSEEN, again]
    decisions, unsatisfied = rule_plan(requirements, targets, seen)
    by_target = {d["targetId"]: d for d in decisions}
    assert list(by_target) == ["person-1", "text-1", "plate-1", "meta-location"] and len(decisions) == 4
    assert by_target["meta-location"] == {
        "targetId": "meta-location", "scope": "metadata", "method": "remove", "shape": None, "ranges": [], "satisfies": ["REQ-4"],
        "analysisId": None, "rationale": LOCATION["statement"], "risk": "medium",
    }  # fmt: skip
    assert {d["targetId"]: (d["scope"], d["method"], d["shape"], d["ranges"]) for d in decisions if d["scope"] != "metadata"} == {
        "person-1": ("face", "mosaic", "ellipse", WHOLE),
        "text-1": ("text", "mosaic", "box", TAG_SEEN),
        "plate-1": ("plate", "mosaic", "box", PLATE_SEEN),
    }
    assert by_target["person-1"]["satisfies"] == ["REQ-1", "REQ-6"]  # one mosaic for both requirements on the same face
    assert unsatisfied == [{"requirementId": "REQ-5", "reason": "person-2의 처리할 영역을 추적하지 못했습니다."}]
    assert validate_plan(decisions, unsatisfied, requirements, targets, seen) == []
    assert rule_plan([], targets, seen) == ([], [])


# ---------- the validator of a proposed plan ----------


def test_each_kind_of_target_has_one_way_to_be_redacted(seen, targets):
    requirements = [FACE, LOCATION]
    good = [decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE), decided("meta-location", "metadata", "remove", ["REQ-4"])]
    assert validate_plan(good, [], requirements, targets, seen) == []
    kept = [decided("person-1", "face", "keep", [], WHOLE), decided("meta-location", "metadata", "keep", [])]
    assert validate_plan(kept, [{"requirementId": "REQ-1", "reason": "x"}, {"requirementId": "REQ-4", "reason": "x"}], requirements, targets, seen) == []

    removed_person = validate_plan([decided("person-1", "face", "remove", ["REQ-1"], WHOLE), good[1]], [], requirements, targets, seen)
    assert "person-1: 이 대상의 처리 방식은 mosaic 또는 keep입니다 ('remove'는 쓸 수 없음)." in removed_person
    assert any(issue.startswith("REQ-1: 대상 person-1에 대한 처리 결정이 없습니다") for issue in removed_person)  # and the face is still not covered

    mosaicked_file = validate_plan([good[0], decided("meta-location", "metadata", "mosaic", ["REQ-4"], WHOLE)], [], requirements, targets, seen)
    assert mosaicked_file == [
        "meta-location: 이 대상의 처리 방식은 remove 또는 keep입니다 ('mosaic'는 쓸 수 없음).",
        "REQ-4: 대상 meta-location을(를) 삭제하는 결정이 없습니다 (satisfies에 REQ-4을(를) 넣은 remove 결정이 필요).",
    ]
    # scopes follow the kind of target too
    assert "meta-location: scope 'face'를 쓸 수 없는 대상입니다." in validate_plan([good[0], decided("meta-location", "face", "remove", ["REQ-4"])], [], requirements, targets, seen)
    assert "person-1: scope 'metadata'를 쓸 수 없는 대상입니다." in validate_plan([decided("person-1", "metadata", "mosaic", ["REQ-1"], WHOLE), good[1]], [], requirements, targets, seen)


def test_the_validator_names_what_else_is_wrong_with_a_plan(seen, targets):
    requirements = [FACE, NAME, LOCATION, UNSEEN]
    good = [decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE), decided("text-1", "text", "mosaic", ["REQ-2"], TAG_SEEN), decided("meta-location", "metadata", "remove", ["REQ-4"])]
    excused = [{"requirementId": "REQ-5", "reason": "추적 실패"}]
    assert validate_plan(good, excused, requirements, targets, seen) == []
    assert validate_plan(good, [], requirements, targets, seen) == []  # person-2 is never visible: nothing was needed anyway
    assert validate_plan([*good, decided("person-9", "face", "mosaic", ["REQ-1"], WHOLE)], excused, requirements, targets, seen) == ["없는 대상 ID 'person-9'에 대한 결정이 있습니다."]
    # a redaction nobody asked for, of either kind
    assert validate_plan([*good, decided("plate-1", "plate", "mosaic", [], PLATE_SEEN)], excused, requirements, targets, seen) == [
        "plate-1: 모자이크 결정이 어떤 요건에도 연결되지 않았습니다. 요건이 없으면 keep으로 두세요."
    ]
    unasked = validate_plan([*good[:2], decided("meta-location", "metadata", "remove", [])], excused, [FACE, NAME], targets, seen)
    assert unasked == ["meta-location: 삭제 결정이 어떤 요건에도 연결되지 않았습니다. 요건이 없으면 keep으로 두세요."]
    assert "text-1: satisfies에 이 대상을 포함하지 않는 요건이 있습니다." in validate_plan([good[0], decided("text-1", "text", "mosaic", ["REQ-1", "REQ-2"], TAG_SEEN), good[2]], excused, requirements, targets, seen)
    # a mosaic needs something tracked to draw on; removing a tag does not
    untracked = validate_plan([*good, decided("person-2", "face", "mosaic", ["REQ-5"], WHOLE)], [], requirements, targets, seen)
    assert untracked == ["person-2: 추적된 영역이 없어 처리할 수 없습니다. unsatisfied에 적으세요."]
    # an uncovered stretch is reported unless the plan says why it cannot be met
    short = [decided("person-1", "face", "mosaic", ["REQ-1"], [(0.0, 9.9)]), *good[1:]]
    assert len(validate_plan(short, excused, requirements, targets, seen)) == 1 and "REQ-1" in validate_plan(short, excused, requirements, targets, seen)[0]
    assert validate_plan(short, [*excused, {"requirementId": "REQ-1", "reason": "x"}], requirements, targets, seen) == []


# ---------- model output → decisions ----------


def test_the_model_chooses_what_to_redact_and_nothing_about_when_or_how():
    assert set(DecisionOut.model_fields) == {"target_id", "scope", "method", "satisfies", "analysis_id", "rationale_ko", "risk"}
    for gone in ("blur", "mask", "pixelate"):
        with pytest.raises(ValidationError):
            DecisionOut.model_validate(proposed("person-1", "face", gone))
    assert DecisionOut.model_validate(proposed("meta-location", "metadata", "remove")).method == "remove"


def test_decision_ranges_are_the_required_ranges_whatever_the_model_wrote(seen, targets):
    part = requirement("REQ-6", "text-1", "text", [(3.0, 6.0)])
    requirements = [FACE, NAME, PLATE, LOCATION, part]
    out = PlanOut.model_validate({
        "decisions": [
            proposed("person-1", "face", "mosaic", ["REQ-1"]),  # no ranges at all: the schema has none
            proposed("text-1", "text", "mosaic", ["REQ-6"], time_ranges=[[0.0, 9.9]], shape="ellipse", alternatives=["blur"]),  # a model that writes them anyway
            proposed("plate-1", "plate", "keep", [], analysis_id="A-2"),
            proposed("meta-location", "metadata", "remove", ["REQ-4", "REQ-99"], time_ranges=[[0.0, 10.0]]),
            proposed("person-1", "body", "mosaic", []),  # linked to nothing
        ],
        "unsatisfied": [{"requirement_id": "REQ-3", "reason": "번호판은 유지"}, {"requirement_id": "REQ-99", "reason": "없는 요건"}],
    })  # fmt: skip
    decisions, unsatisfied = planner._normalize(out, requirements, seen)
    face, tag, plate, location, body = decisions
    assert face == {"targetId": "person-1", "scope": "face", "method": "mosaic", "shape": "ellipse", "ranges": WHOLE, "satisfies": ["REQ-1"], "analysisId": None, "rationale": "이유", "risk": "medium"}
    assert face["ranges"] == required_ranges(FACE, seen, "person-1")  # to the end of the video, not to "9.9"
    assert (tag["ranges"], tag["shape"]) == ([(3.0, 4.367)], "box") and tag["ranges"] == required_ranges(part, seen, "text-1")
    assert (plate["method"], plate["ranges"], plate["analysisId"]) == ("keep", PLATE_SEEN, "A-2")  # a kept target is listed with all that is visible of it
    assert (location["ranges"], location["shape"], location["satisfies"]) == ([], None, ["REQ-4"])  # no place, no time, no unknown requirement
    assert (body["ranges"], body["satisfies"]) == (WHOLE, [])
    assert unsatisfied == [{"requirementId": "REQ-3", "reason": "번호판은 유지"}]
    assert all(set(d) == {"targetId", "scope", "method", "shape", "ranges", "satisfies", "analysisId", "rationale", "risk"} for d in decisions)
    # what the validator then says: only the unasked body mosaic and the tag requirement nobody took
    assert validate_plan(decisions, unsatisfied, requirements, targets, seen) == [
        "person-1: 모자이크 결정이 어떤 요건에도 연결되지 않았습니다. 요건이 없으면 keep으로 두세요.",
        "REQ-2: 대상 text-1에 대한 처리 결정이 없습니다 (satisfies에 REQ-2을(를) 넣은 결정이 필요).",
    ]
    both = planner._normalize(PlanOut.model_validate({"decisions": [proposed("text-1", "text", "mosaic", ["REQ-2", "REQ-6"])], "unsatisfied": []}), requirements, seen)[0][0]
    assert both["ranges"] == TAG_SEEN  # the union of what its requirements need


def test_a_finished_plan_lists_every_personal_target_and_gives_metadata_no_shape_or_range(seen, targets):
    requirements = [FACE, LOCATION]
    analyses = [{"id": "A-2", "targets": ["plate-1"], "applicability": "not_applies", "reasoning": "본인 차량", "citations": []}]
    decisions = [decided("meta-location", "metadata", "remove", ["REQ-4"]), decided("person-1", "face", "mosaic", ["REQ-1"], WHOLE)]
    plan = finish_plan(decisions, [], requirements, analyses, targets, version=3)
    assert plan.version == 3 and [(d.id, d.target_id, d.scope, d.method) for d in plan.decisions] == [
        ("D-1", "person-1", "face", "mosaic"),  # redactions first, in the order of the targets
        ("D-2", "meta-location", "metadata", "remove"),
        ("D-3", "text-1", "text", "keep"),  # personal and left out by the plan: listed as kept
        ("D-4", "plate-1", "plate", "keep"),
    ]  # person-2 was never visible: there is nothing to decide
    removed, tag, plate = plan.decisions[1:]
    assert (removed.shape, removed.ranges, removed.target_label, removed.satisfies) == (None, [], "촬영 위치 정보", ["REQ-4"])
    assert (tag.ranges, tag.risk, tag.analysis_id) == (TAG_SEEN, "uncertain", None) and (plate.rationale, plate.risk, plate.analysis_id) == ("본인 차량", "low", "A-2")
    wire = plan.wire()["decisions"]
    assert "shape" not in wire[1] and wire[0]["shape"] == "ellipse" and not any("alternatives" in d for d in wire)
    # without a requirement the location is listed too, as kept
    kept = finish_plan([], [], [], [], targets, version=1).decisions
    assert [(d.target_id, d.scope, d.method, d.shape, d.ranges) for d in kept if d.scope == "metadata"] == [("meta-location", "metadata", "keep", None, [])]
    assert {d.method for d in kept} == {"keep"} and [d.target_id for d in kept] == ["person-1", "text-1", "plate-1", "meta-location"]


# ---------- the loop around the model ----------


class ScriptedPlanner:
    """Stands in for the planner model: one scripted reply per call."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def json(self, slot: str, schema, payload: dict):
        assert slot == "llm.planner" and {"requirements", "targets"} <= set(payload)
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return schema.model_validate({"decisions": reply, "unsatisfied": []})


def services_with(model: ScriptedPlanner) -> Services:
    profile = as_fixture(resolve(load_catalog(PIPELINE), load_profile(PIPELINE, "baseline-v2")))
    return Services(settings=CoreSettings(_env_file=None), profile=profile, cost=CostMeter(0), fixture=model)


def test_a_plan_that_breaks_the_rules_is_sent_back_and_the_rule_plan_is_the_last_resort(seen, targets):
    requirements = [FACE, NAME, LOCATION]
    right = [proposed("person-1", "face", "mosaic", ["REQ-1"]), proposed("text-1", "text", "mosaic", ["REQ-2"]), proposed("meta-location", "metadata", "remove", ["REQ-4"])]
    wrong = [proposed("person-1", "face", "remove", ["REQ-1"]), proposed("text-1", "text", "mosaic", ["REQ-2"]), proposed("meta-location", "metadata", "mosaic", ["REQ-4"])]
    expected = [("person-1", "face", "mosaic", "ellipse", WHOLE), ("text-1", "text", "mosaic", "box", TAG_SEEN), ("meta-location", "metadata", "remove", None, []), ("plate-1", "plate", "keep", "box", PLATE_SEEN)]

    def shape_of(plan) -> list[tuple]:
        return [(d.target_id, d.scope, d.method, d.shape, d.ranges) for d in plan.decisions]

    first = ScriptedPlanner(right)
    assert shape_of(make_plan(services_with(first), seen, targets, requirements, [], [])) == expected and first.calls == 1
    retried = ScriptedPlanner(wrong, ModelCallError("시간 초과"), right)  # a rejected plan and a failed call, then a good one
    plan = make_plan(services_with(retried), seen, targets, requirements, [], [], version=2)
    assert shape_of(plan) == expected and retried.calls == 3 and plan.version == 2 and plan.unsatisfied == []
    stubborn = ScriptedPlanner(wrong, wrong, wrong, wrong)
    limit = int(services_with(stubborn).param("planner.max_generations", 3))
    plan = make_plan(services_with(stubborn), seen, targets, requirements, [], [])
    assert stubborn.calls == limit == 3  # no fourth try: the requirements are followed to the letter instead
    assert shape_of(plan) == expected and [d.satisfies for d in plan.decisions[:3]] == [["REQ-1"], ["REQ-2"], ["REQ-4"]]
    assert [d.rationale for d in plan.decisions[:3]] == [FACE["statement"], NAME["statement"], LOCATION["statement"]]
    idle = ScriptedPlanner()
    nothing = make_plan(services_with(idle), seen, targets, [], [], [])
    assert idle.calls == 0 and {d.method for d in nothing.decisions} == {"keep"} and "meta-location" in [d.target_id for d in nothing.decisions]
