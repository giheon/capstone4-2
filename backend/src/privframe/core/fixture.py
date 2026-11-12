"""Scripted stand-ins for every model and detector, selected by the `fixture` candidates of a profile (FR-13).

With the fixture profile the whole case graph — agent loop, validator, question interrupt, planner, render,
verification, report — runs without API keys, network or model downloads, and gives the same result every
time. The stand-ins are deliberately simple: they exercise the real control flow, not legal reasoning.
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from .domain import IdentityAmbiguity, PersonGroup, SegmentInfo, TextItem, Track, TrackPoint, VideoAsset, VideoUnderstanding
from .retrieval import HashEmbedder
from .video import sample_indices

EVIDENCE_ID = re.compile(r"\[([^\[\]\s]+#[^\[\]]+)\]")
TARGET_ID = re.compile(r"^- ((?:person|text|plate)-\d+|meta-[a-z]+) \(", re.M)


class FixtureAnalyst(BaseChatModel):
    """Plays the Legal Analyst: search once, ask one question when there is a person, then finalize.
    `bad_first_submission` makes the first finalize cite an unknown evidence id so the validator loop runs."""

    ask: bool = True
    bad_first_submission: bool = False
    ask_with_other_tools: bool = False  # ask_user in the same turn as another tool call

    @property
    def _llm_type(self) -> str:
        return "privframe-fixture-analyst"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FixtureAnalyst":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        brief = next((str(m.content) for m in messages if isinstance(m, HumanMessage)), "")
        results = [m for m in messages if isinstance(m, ToolMessage)]
        names = [m.name for m in results]
        targets = TARGET_ID.findall(brief)
        persons = [t for t in targets if t.startswith("person-")]
        number = len(results) + 1
        if "search_law" not in names:
            call = {"name": "search_law", "args": {"query": "동의 없는 제3자 얼굴 영상 공개 요건"}, "id": f"fx-{number}"}
        elif self.ask and persons and "ask_user" not in names and "재평가" not in brief:
            call = {"name": "ask_user", "args": {"questions": [self._question(persons[0])]}, "id": f"fx-{number}"}
        else:
            call = {"name": "finalize", "args": self._submission(brief, results, targets, names.count("finalize")), "id": f"fx-{number}"}
        calls = [call]
        if call["name"] == "ask_user" and self.ask_with_other_tools:
            calls.insert(0, {"name": "checklist", "args": {}, "id": f"fx-{number}-checklist"})
        message = AIMessage(content="", tool_calls=calls, usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _question(person: str) -> dict[str, Any]:
        return {
            "fact_key": "purpose.publication_scope",
            "fact_label_ko": "공개 범위",
            "prompt_ko": "이 영상을 어디까지 공개할 예정인가요?",
            "options": [{"value": "public", "label": "불특정 다수에게 공개"}, {"value": "private", "label": "공개하지 않고 보관"}],
            "why_ko": "공개 범위에 따라 다른 사람의 얼굴을 알아볼 수 없게 해야 하는지가 달라집니다.",
            "evidence_ids": [],
            "impact": [{"option": "public", "effect_ko": "동의가 확인되지 않은 사람의 얼굴을 처리합니다."}, {"option": "private", "effect_ko": "처리하지 않습니다."}],
            "targets": [person],
        }

    def _submission(self, brief: str, results: list[ToolMessage], targets: list[str], finalized: int) -> dict[str, Any]:
        text = brief + "\n" + "\n".join(str(m.content) for m in results)
        evidence = list(dict.fromkeys(EVIDENCE_ID.findall(text)))
        cited = ["unknown#없는근거"] if self.bad_first_submission and finalized == 0 else evidence[:2]
        answered = re.search(r"purpose\.publication_scope = (\w+)", text)
        declared = re.search(r"purpose\.publication_scope = (\w+) ", brief)
        scope = (answered or declared).group(1) if (answered or declared) else "unknown"
        protect = scope not in ("private", "limited")
        scope_of = {"person": "face", "text": "text", "plate": "plate", "meta": "metadata"}
        located = " · 촬영 위치 메타데이터(좌표)" if any(t.startswith("meta-") for t in targets) else ""
        analysis = {
            "id": "A-1",
            "question_of_law": f"동의가 확인되지 않은 제3자의 얼굴·이름·번호판을 공개 영상에서 처리해야 하는가 (제3자 초상, 명찰 글자, 차량 번호판, 동의 범위, 결합 식별 단서{located})",
            "applicability": ("applies" if cited else "uncertain") if protect else "not_applies" if cited else "uncertain",
            "reasoning_ko": f"공개 범위가 {scope}입니다. 고정 응답 분석입니다.",
            "evidence_ids": cited,
            "affected_targets": targets,
            "depends_on_facts": ["purpose.publication_scope"],
            "conservative_assumption": None if cited else "근거를 찾지 못해 보수적으로 처리",
            "pattern_ref": "P01",
            "confidence": 0.5,
        }
        requirements = []
        if protect or not cited:
            for index, target in enumerate(targets, start=1):
                requirements.append({"id": f"REQ-{index}", "statement_ko": f"공개 영상이므로 {target}을(를) 식별할 수 없게 처리", "targets": [target], "scope": scope_of[target.split("-")[0]], "time_ranges": None, "derived_from_analysis_ids": ["A-1"], "evidence_ids": cited})
        unresolved = [] if answered or declared else [{"item": "공개 범위", "assumption": "불특정 다수 공개로 가정"}]
        return {"analyses": [analysis], "requirements": requirements, "unresolved": unresolved}


class ScriptedChecker:
    """An output checker that reports the leaks it was given (core/verify.py's Checker)."""

    def __init__(self, leaks: list[Any]):
        self.leaks = leaks

    def inside(self, output: Any, original: Any, frame: int, regions: Any) -> int:
        return 0

    def scan(self, frame: int, small: Any, scale: float, full: Any, regions: Any) -> None:
        return None

    def summary(self) -> tuple[str, str]:
        return "", "고정 응답 검사"


class Fixture:
    def __init__(self, *, ask: bool = True, bad_first_submission: bool = False, ambiguity: bool = False, ask_with_other_tools: bool = False, leak_rounds: int = 0):
        self.ask = ask
        self.bad_first_submission = bad_first_submission
        self.ambiguity = ambiguity
        self.ask_with_other_tools = ask_with_other_tools
        self.leak_rounds = leak_rounds  # how many output checks in a row report a face that is still showing

    # ---------- models ----------

    def embedder(self) -> HashEmbedder:
        return HashEmbedder()

    def chat(self, slot: str) -> BaseChatModel:
        if slot != "llm.analyst":
            raise ValueError(f"fixture has no chat model for {slot}")
        return FixtureAnalyst(ask=self.ask, bad_first_submission=self.bad_first_submission, ask_with_other_tools=self.ask_with_other_tools)

    def json(self, slot: str, schema: type[BaseModel], payload: dict[str, Any]) -> Any:
        if schema.__name__ == "MemoFacts":
            pairs = re.findall(r"([a-z][\w.\-]*)=(\w+)", payload.get("memo", ""))
            return schema.model_validate({"facts": [{"key": k, "label_ko": k, "value": v, "value_label_ko": v, "confidence": 1.0} for k, v in pairs]})
        if schema.__name__ == "PlanOut":
            decisions = [
                {"target_id": target, "scope": r["scope"], "method": "remove" if r["scope"] == "metadata" else "mosaic", "satisfies": [r["id"]], "analysis_id": None, "rationale_ko": r["statement"], "risk": "medium"}
                for r in payload.get("requirements", [])
                for target in r["targets"]
            ]
            return schema.model_validate({"decisions": decisions, "unsatisfied": []})
        raise ValueError(f"fixture has no structured answer for {schema.__name__}")

    def scene(self, segment: SegmentInfo) -> dict[str, Any]:
        return {"label_ko": "예시 장면", "place_type": "cafe", "setting": "카페 안 (고정 응답)", "activity": "인터뷰", "people_count": 1, "minors_present": False, "sensitivity_flags": [], "camera_awareness": "aware", "visible_text_summary": "명찰", "summary_ko": "한 사람이 카메라를 보고 이야기한다.", "confidence": 0.9}

    def person(self, person: PersonGroup) -> dict[str, Any]:
        return {"age_band": "adult", "role_cues": ["명찰"], "identifiability_cues": [], "description_ko": "성인 한 명 (고정 응답)", "confidence": 0.9}

    def inspect(self, question: str) -> dict[str, Any]:
        return {"answer": "고정 응답: 확인할 수 없습니다.", "verdict": "unclear", "confidence": 0.5}

    def checker(self, round_number: int, decisions: list[Any], width: int, height: int) -> ScriptedChecker | None:
        """The output check of a fixture run: nothing to report, unless the fixture was told to find a leak
        (a face of the first redacted decision at the top-left corner of frame 0) in the first rounds."""
        if round_number >= self.leak_rounds:
            return None
        from .verify import Leak

        redacted = next((d for d in decisions if d.method == "mosaic"), None)
        if redacted is None:
            return None
        return ScriptedChecker([Leak(0, redacted.id, (width * 0.02, height * 0.02, width * 0.1, height * 0.1), f"{redacted.target_label} 얼굴 (고정 응답)", (0, 0))])

    # ---------- perception ----------

    def tracks(self, asset: VideoAsset, times: list[float]) -> tuple[list[SegmentInfo], list[Track]]:
        """One scene and one person: a face in the upper middle and a body box around it, for the whole video."""
        last = len(times) - 1
        frames = sample_indices(times, 6.0)
        keyframes = sorted({frames[round(i * (len(frames) - 1) / 3)] for i in range(4)}) if frames else [0]
        segment = SegmentInfo(id="S1", start=times[0], end=times[-1], start_frame=0, end_frame=last, keyframes=keyframes)
        width, height = asset.meta.display_width, asset.meta.display_height
        face = (width * 0.40, height * 0.18, width * 0.20, height * 0.28)
        body = (width * 0.28, height * 0.12, width * 0.44, height * 0.84)
        tracks = [
            Track(id="T1", kind="face", segment="S1", points=[TrackPoint(f=f, t=times[f], box=face, score=0.99) for f in frames]),
            Track(id="T2", kind="person", segment="S1", points=[TrackPoint(f=f, t=times[f], box=body, score=0.99) for f in frames]),
        ]
        if self.ambiguity and len(frames) >= 4:
            half = len(frames) // 2
            tracks[0].points = tracks[0].points[:half]
            tracks.append(Track(id="T3", kind="face", segment="S1", points=[TrackPoint(f=f, t=times[f], box=face, score=0.9) for f in frames[half:]]))
        return [segment], tracks

    def persons(self, tracks: list[Track]) -> tuple[list[PersonGroup], list[IdentityAmbiguity]]:
        faces = [t for t in tracks if t.kind == "face"]
        bodies = [t.id for t in tracks if t.kind == "person"]
        persons = [PersonGroup(id="person-1", face_tracks=[faces[0].id], body_tracks=bodies, prominence=1.0)]
        ambiguities = []
        if len(faces) > 1:
            persons.append(PersonGroup(id="person-2", face_tracks=[faces[1].id], prominence=0.5))
            ambiguities.append(IdentityAmbiguity(id="amb-1", a="person-1", b="person-2", similarity=0.3, at_a=faces[0].start, at_b=faces[1].start))
        return persons, ambiguities

    def texts(self, asset: VideoAsset, times: list[float]) -> tuple[list[Track], list[TextItem]]:
        frames = sample_indices(times, 6.0)  # as dense as the other tracks, so the whole stretch is redacted
        if frames and frames[-1] != len(times) - 1:
            frames.append(len(times) - 1)
        width, height = asset.meta.display_width, asset.meta.display_height
        box = (width * 0.42, height * 0.56, width * 0.16, height * 0.07)
        track = Track(id="X1", kind="text", segment="S1", text="김민지 바리스타", points=[TrackPoint(f=f, t=times[f], box=box, score=0.9) for f in frames])
        return [track], [TextItem(id="text-1", text="김민지 바리스타", kind="nametag", tracks=["X1"], attached_person="person-1", confidence=0.9)]

    def apply_identity(self, understanding: VideoUnderstanding, ambiguity_id: str, answer: str) -> VideoUnderstanding:
        updated = understanding.model_copy(deep=True)
        item = next(a for a in updated.ambiguities if a.id == ambiguity_id)
        updated.ambiguities = [a for a in updated.ambiguities if a.id != ambiguity_id]
        if answer != "different":
            keep = next(p for p in updated.persons if p.id == item.a)
            gone = next(p for p in updated.persons if p.id == item.b)
            keep.face_tracks += gone.face_tracks
            keep.body_tracks += gone.body_tracks
            updated.persons = [p for p in updated.persons if p.id != item.b]
            for text in updated.texts:
                if text.attached_person == item.b:
                    text.attached_person = item.a
        return updated


FIXTURE_SLOTS = ("perception.face_detector", "perception.object_detector", "perception.face_embedding", "perception.ocr", "vlm.scene", "vlm.person", "vlm.inspect", "rag.embedding", "llm.analyst", "llm.planner", "llm.review_guide", "llm.report")
OFF_SLOTS = ("perception.plate_detector", "perception.segmenter", "rag.reranker")


def as_fixture(profile: Any) -> Any:
    """A copy of a resolved profile whose models and detectors are the scripted stand-ins. Used when the
    catalog's `fixture` profile is not wanted, e.g. to run any profile's budgets and options offline."""
    from dataclasses import replace

    from .pipeline import Candidate, Part, config_hash

    parts = dict(profile.parts)
    for slot in FIXTURE_SLOTS:
        # the stand-in remembers which candidate it replaces (ref), so two profiles that differ in this part
        # still count as different inputs to the steps that use it
        parts[slot] = Part(slot=parts[slot].slot, candidate=Candidate(id="fixture", label="고정 응답", adapter="fixture", short="fixture", ref=parts[slot].candidate.id), kind="builtin")
    for slot in OFF_SLOTS:
        parts[slot] = Part(slot=parts[slot].slot, candidate=Candidate(id="none", label="사용 안 함", adapter="none", short="없음"), kind="option")
    digest = config_hash({k: v.candidate.id for k, v in parts.items()}, profile.params)
    return replace(profile, name=f"{profile.name} (fixture)", parts=parts, config_hash=digest)
