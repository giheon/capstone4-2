"""The pipeline cut into sections that can be run and compared on their own (FR-14 · FR-17).

A section is a stretch of the case graph between two top-level nodes, together with the catalog parts and
parameters that are read inside it, and the points at which it asks the user. An experiment is a set of runs
over one range of sections; a run continues an earlier run from the checkpoint in front of the section it
starts at and stops at the end of the range. What lies before is reused, not run again.

A section that asks the user is never answered for: a run that goes through one waits for the person.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """One node of a section: the parts and parameters its result depends on, and the steps it reads.
    The step store keys a result on exactly these (core/graph.py), so this table is also what tells an
    experiment which steps a changed part makes run again."""

    node: str
    slots: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    stored: bool = False  # the result is kept in the step store and reused for the same inputs


# what the legal analyst reads: one step, and the whole of its section
ANALYST_SLOTS = ("llm.analyst", "vlm.inspect", "rag.seed", "rag.chunking", "rag.embedding", "rag.retrieval", "rag.bm25_tokenizer", "rag.reranker", "rag.store", "agent.questions")
ANALYST_PARAMS = ("rag.top_k", "analyst.max_tool_calls", "analyst.max_searches", "analyst.max_question_rounds", "analyst.max_questions", "analyst.max_retries")

STEPS: dict[str, Step] = {
    step.node: step
    for step in (
        Step("perceive/make_proxy", params=("perception.proxy_short_side",), stored=True),
        Step("perceive/detect_track", slots=("perception.scene_cut", "perception.face_detector", "perception.object_detector", "perception.tracker", "perception.plate_detector"), params=("perception.analysis_fps",), after=("perceive/make_proxy",), stored=True),
        Step("perceive/group_identities", slots=("perception.face_embedding",), after=("perceive/detect_track",), stored=True),
        Step("perceive/ocr", slots=("perception.ocr",), after=("perceive/detect_track",), stored=True),
        Step("perceive/describe_segments", slots=("vlm.scene", "vlm.input_mode", "perception.face_detector"), after=("perceive/detect_track",), stored=True),
        Step("perceive/describe_persons", slots=("vlm.person", "vlm.input_mode", "perception.face_detector"), after=("perceive/group_identities",), stored=True),
        Step("perceive/assemble", after=("perceive/group_identities", "perceive/ocr", "perceive/describe_segments", "perceive/describe_persons")),
        Step("legal_analyst", slots=ANALYST_SLOTS, params=ANALYST_PARAMS, after=("perceive/assemble",)),
        Step("plan", slots=("llm.planner",), params=("planner.max_generations",), after=("legal_analyst",)),
        Step("render_original", slots=("render.encoder",), after=("plan",)),
        Step("verify_output", after=("render_original",)),
    )
}


def affected_steps(names: list[str]) -> set[str]:
    """The steps that run again when the given parts or parameters change: those that read one of them, and
    every step after such a step."""
    changed = {node for node, step in STEPS.items() if set(names) & {*step.slots, *step.params}}
    grew = True
    while grew:
        more = {node for node, step in STEPS.items() if node not in changed and set(step.after) & changed}
        grew = bool(more)
        changed |= more
    return changed


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    description: str
    entry: tuple[str, ...]  # the top-level graph nodes the section can start at (a run continues in front of the first one reached)
    nodes: tuple[str, ...]  # run-event node ids inside the section (for timing and cost)
    slots: tuple[str, ...] = ()  # catalog parts read inside the section
    params: tuple[str, ...] = ()  # profile parameters read inside the section
    steps: tuple[str, ...] = ()  # node ids worth showing one by one, in order
    asks: tuple[str, ...] = ()  # what the section asks the user (interrupt payload kinds)
    stop_after: tuple[str, ...] = ()  # a run that ends with this section ends after these nodes …
    stop_before: tuple[str, ...] = ()  # … or in front of these
    last: str = ""  # the node shown as the last one run when a run ends with this section


ASK_LABELS = {"identity": "같은 사람 확인", "questions": "질문", "review": "계획 검토", "audit": "감사 확인"}

SECTIONS: tuple[Section, ...] = (
    Section(
        id="perception",
        label="영상 정보 추출",
        description="원본에서 장면 · 사람 · 글자 · 번호판을 찾아 추적하고, VLM으로 장면과 인물을 설명한다.",
        entry=("perceive",),
        nodes=("perceive",),
        slots=(
            "perception.scene_cut", "perception.face_detector", "perception.object_detector", "perception.tracker", "perception.plate_detector",
            "perception.face_embedding", "perception.ocr", "vlm.input_mode", "vlm.scene", "vlm.person",
        ),  # fmt: skip
        params=("perception.analysis_fps", "perception.proxy_short_side"),
        steps=("perceive/make_proxy", "perceive/detect_track", "perceive/group_identities", "perceive/ocr", "perceive/describe_segments", "perceive/describe_persons", "perceive/assemble"),
        stop_after=("perceive",),
        last="perceive",
    ),
    Section(
        id="analysis",
        label="법률 분석",
        description="같은 사람인지 불확실한 대상을 확인받고, Legal Analyst가 법령 · 판례를 검색 · 열람하며 필요한 사실을 물어 보호 요건을 도출한다.",
        entry=("await_identity", "legal_analyst"),
        nodes=("await_identity", "legal_analyst"),
        slots=ANALYST_SLOTS,
        params=ANALYST_PARAMS,
        steps=("legal_analyst",),
        asks=("identity", "questions"),
        stop_after=("legal_analyst",),
        last="legal_analyst",
    ),
    Section(
        id="plan",
        label="계획",
        description="Planner가 보호 요건을 결정(대상 · 범위 · 모자이크 또는 유지)으로 바꾸고 검증기가 대조한다.",
        entry=("plan",),
        nodes=("plan", "review_guide"),
        slots=("llm.planner",),
        params=("planner.max_generations",),
        steps=("plan",),
        stop_after=("review_guide",),
        last="review_guide",
    ),
    Section(
        id="review",
        label="검토 · 감사",
        description="사용자가 계획을 검토 · 수정하고, 수정된 계획이 모든 요건을 여전히 충족하는지 감사한다.",
        entry=("await_review",),
        nodes=("await_review", "auditor", "await_audit_confirm"),
        steps=("auditor",),
        asks=("review", "audit"),
        stop_before=("render_original",),
        last="auditor",
    ),
    Section(
        id="render",
        label="렌더 · 검증",
        description="승인된 계획대로 원본 스트림에 모자이크를 입히고 출력을 검증한다.",
        entry=("render_original",),
        nodes=("render_original", "verify_output", "report"),
        slots=("render.encoder",),
        steps=("render_original", "verify_output"),
        last="report",
    ),
)
ORDER = [section.id for section in SECTIONS]
BY_ID = {section.id: section for section in SECTIONS}


def span(first: str, last: str) -> list[Section]:
    """The sections from `first` to `last`, inclusive."""
    a, b = ORDER.index(first), ORDER.index(last)
    if b < a:
        raise ValueError(f"구간의 끝({last})이 시작({first})보다 앞에 있습니다.")
    return list(SECTIONS[a : b + 1])


def stop_at(last: str) -> dict[str, list[str] | str]:
    """Where a run ends that ends with section `last`: `after`/`before` are the graph's interrupt nodes and
    `shown` the node reported as the last one run. Empty for the last section (the run goes to the end)."""
    section = BY_ID[last]
    if not section.stop_after and not section.stop_before:
        return {}
    return {"after": list(section.stop_after), "before": list(section.stop_before), "shown": section.last}


def asks(sections: list[Section]) -> list[tuple[Section, str]]:
    """Every point in the sections at which the user is asked."""
    return [(section, kind) for section in sections for kind in section.asks]


def interactions(sections: list[Section]) -> list[dict[str, str]]:
    """The same points as the API names them."""
    return [{"section": section.id, "kind": kind, "label": ASK_LABELS[kind]} for section, kind in asks(sections)]


def asks_before(node: str) -> bool:
    """Whether the user is asked anywhere in front of the section that starts at `node`."""
    index = next(i for i, section in enumerate(SECTIONS) if node in section.entry)
    return any(section.asks for section in SECTIONS[:index])


def section_of(name: str) -> Section | None:
    """The section a part or parameter belongs to."""
    return next((section for section in SECTIONS if name in section.slots or name in section.params), None)


def first_changed(names: list[str], within: list[Section]) -> Section | None:
    """The earliest section of `within` that one of the changed parts or parameters belongs to."""
    changed = {section.id for name in names if (section := section_of(name)) is not None}
    return next((section for section in within if section.id in changed), None)
