"""Legal Analyst: a tool-using agent that derives protection requirements with cited evidence (design §5.2 · §5.3).

Built with LangChain `create_agent`. The model decides what to search, read, look at again or ask; everything
that must be reliable is deterministic code around it:

- tools register every piece of evidence they return, and a submission may cite only those ids;
- a middleware enforces the budgets (tool calls, searches, question rounds, repeated queries);
- `finalize` hands the result to a validator, which sends problems back to the model (at most `max_retries`
  times) and asks one coverage question about reference patterns the analysis seems to have skipped;
- `ask_user` passes the questions through a gate before interrupting the run for the user's answers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, NotRequired

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from .. import knowledge
from ..domain import Fact, FactOption, Impact, Question, QuestionOption, VideoAsset, VideoUnderstanding
from ..retrieval import RetrievalError, get_document as read_document
from ..runtime import BudgetExceeded, CaseContext, ModelCallError, Services, emit
from ..video import index_at
from ..vlm import Vision, box_at

AGENT = "legal_analyst"
KINDS = Literal["law", "admrul", "prec", "detc", "expc", "ppc", "guideline", "mediation"]
SCOPES_BY_KIND = {"person": {"face", "body"}, "text": {"text"}, "plate": {"plate"}, "region": {"region"}, "metadata": {"metadata"}}

SYSTEM_PROMPT = """당신은 한국 개인정보 보호법과 초상권 법리에 따라 영상의 비식별화 요건을 도출하는 법률 분석가(Legal Analyst)입니다.
목표: 사용자가 밝힌 목적대로 이 영상을 쓸 때 누구의 무엇을 어느 구간에서 알아볼 수 없게 해야 하는지(보호 요건)를 근거와 함께 정리합니다.

원칙
1. 관찰, 알려진 사실, 도구로 찾은 근거만으로 판단합니다. 기억에 있는 조문이나 판례를 근거로 쓰지 않습니다.
2. 모든 분석은 근거 ID를 인용합니다. 근거 ID는 시드 문서와 search_law · get_document 결과에 [대괄호]로 표시된 것만 쓸 수 있습니다.
3. 근거가 부족하면 applicability를 uncertain으로 두고 보수적 가정(conservative_assumption)을 적습니다.
4. 권위 등급은 규범(1) · 해석(2) · 참고(3)입니다. 참고(3) 자료만으로 "의무"를 단정하지 않습니다. 판례는 사안이 다르면 그대로 적용하지 않으며, 인용하기 전에 get_document로 해당 부분을 열어 결론과 맥락을 확인합니다.
5. 사용자에게 묻는 것은 (가) 영상과 메모로 알 수 없고 (나) 답에 따라 요건이 달라지는 사실뿐입니다. 이미 아는 사실, 영상으로 확인할 수 있는 것(inspect_visual), 답이 달라져도 결과가 같은 것은 묻지 않습니다. 화면 속 인물이 본인인지, 공개에 동의했는지처럼 영상으로 알 수 없는 사실은 추측하지 말고 묻습니다.
6. 참고 패턴(checklist)은 놓친 쟁점을 떠올리는 참고일 뿐이고 분류를 강제하지 않습니다. 패턴에 없는 쟁점도 분석합니다.
7. 처리 방식은 정하지 않습니다(화면의 대상은 모자이크로 가리고, 파일 메타데이터는 삭제하는 한 가지뿐입니다). 요건은 "어느 대상의 어느 부분을 어느 구간에서 식별할 수 없게"까지만 정합니다.
8. [대상]에 파일 메타데이터(meta-로 시작하는 ID, 예: 촬영 위치 좌표)가 있으면 그것도 대상입니다. 화면에는 보이지 않지만 파일을 받는 사람이 읽을 수 있으므로, 공개 범위와 사실에 비추어 누구의 무엇을 드러내는지 다른 대상과 같은 방식으로 근거를 찾아 판단합니다. 지워야 한다는 결론이면 요건으로, 지울 필요가 없다는 결론이면 not_applies 분석으로 남깁니다.

도구
- search_law(query, kinds): 법령 · 판례 · 결정례 · 안내서 검색. 쟁점마다 구체적인 질의를 쓰고 같은 질의를 반복하지 않습니다.
- get_document(ref, unit): 근거 ID나 문서의 원문 열람.
- inspect_visual(question, target, start, end): 특정 대상이나 구간을 다시 봅니다.
- checklist(): 참고 패턴과 이 영상의 관찰이 맞닿는 지점.
- ask_user(questions): 사실이 부족할 때 한 번에 모아 묻습니다. 다른 도구와 같은 턴에 함께 호출하지 말고 단독으로 호출합니다. 질문마다 fact_key, 짧은 이름, 선택지, 묻는 이유(어느 근거의 어떤 요건 때문인지), 그 근거의 ID(evidence_ids), 선택지별로 처리가 어떻게 달라지는지(impact)를 적습니다. 꼭 필요한 질문만 합니다.
- finalize(analyses, requirements, unresolved): 마지막에 반드시 호출합니다. 검증기가 인용과 연결을 확인하고 문제가 있으면 사유를 돌려줍니다.

권장 순서: 관찰과 사실 확인 → 적용되는 법 체계와 쟁점 정리 → 근거 검색과 열람 → 부족한 사실을 모아 질문 → 답을 반영해 다시 평가 → finalize.

finalize 작성 규칙
- analyses: id는 A-1부터. 처리할 필요가 없다고 본 대상(본인, 동의한 사람, 개인정보가 아닌 글자 등)도 not_applies 분석으로 남겨 유지의 근거가 되게 합니다. depends_on_facts에는 판단이 의존하는 사실 키를 적습니다.
- requirements: id는 REQ-1부터. statement_ko는 "불특정 다수에게 공개하므로 동의가 확인되지 않은 person-2의 얼굴을 식별할 수 없게 처리"처럼 이유와 대상을 함께 적습니다. targets는 대상 ID이고 scope는 대상의 종류를 따릅니다: 인물(person-N)은 face 또는 body, 글자(text-N)는 text, 번호판(plate-N)은 plate, 파일 메타데이터(meta-…)는 metadata(구간 없음). 한 요건에는 같은 종류의 대상만 넣습니다(인물에 붙은 명찰을 가리려면 그 글자 대상으로 요건을 따로 만듭니다). time_ranges를 비우면 대상이 보이는 전 구간입니다.
- unresolved: 답을 받지 못했거나 근거가 부족한 사항과 그때 적용한 가정."""


# ---------- tool input schemas ----------


class OptionIn(BaseModel):
    value: str = Field(description="짧은 영문 소문자 값 (예: public)")
    label: str = Field(description="사용자에게 보이는 한국어 선택지")
    detail: str | None = Field(default=None, description="선택지 보충 설명")


class ImpactIn(BaseModel):
    option: str = Field(description="options의 value")
    effect_ko: str = Field(description="이 답이면 처리가 어떻게 달라지는지")


class QuestionIn(BaseModel):
    fact_key: str = Field(description="정규화된 사실 키 (예: purpose.publication_scope, person-2.publication_consent)")
    fact_label_ko: str = Field(description="사실의 짧은 이름 (예: 공개 범위)")
    prompt_ko: str = Field(description="사용자에게 보일 질문 한 문장")
    options: list[OptionIn]
    why_ko: str = Field(description="왜 묻는지: 어느 근거의 어떤 요건 때문인지")
    evidence_ids: list[str] = Field(default_factory=list)
    impact: list[ImpactIn] = Field(description="선택지별로 처리가 어떻게 달라지는지")
    targets: list[str] = Field(default_factory=list, description="관련 대상 ID")


class AnalysisIn(BaseModel):
    id: str
    question_of_law: str
    applicability: Literal["applies", "not_applies", "uncertain"]
    reasoning_ko: str
    evidence_ids: list[str] = Field(default_factory=list)
    affected_targets: list[str] = Field(default_factory=list)
    depends_on_facts: list[str] = Field(default_factory=list)
    conservative_assumption: str | None = None
    pattern_ref: str | None = None
    confidence: float = 0.5


class RequirementIn(BaseModel):
    id: str
    statement_ko: str
    targets: list[str]
    scope: Literal["face", "body", "text", "plate", "metadata"]
    time_ranges: list[tuple[float, float]] | None = None
    derived_from_analysis_ids: list[str]
    evidence_ids: list[str] = Field(default_factory=list)


class UnresolvedIn(BaseModel):
    item: str
    assumption: str


# ---------- agent state ----------


def _merge_dict(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


def _merge_by(key: str):
    def merge(left: list | None, right: list | None) -> list:
        merged = {item[key]: item for item in left or []}
        merged.update({item[key]: item for item in right or []})
        return list(merged.values())

    return merge


class AnalystState(AgentState):
    evidence: Annotated[dict[str, dict[str, Any]], _merge_dict]
    facts: Annotated[list[dict[str, Any]], _merge_by("key")]
    questions: Annotated[list[dict[str, Any]], _merge_by("id")]
    submission: NotRequired[dict[str, Any] | None]
    result: NotRequired[dict[str, Any] | None]
    retries: NotRequired[int]
    coverage_asked: NotRequired[bool]
    nudges: NotRequired[int]
    issues: NotRequired[list[str]]


@dataclass
class AnalystDeps:
    services: Services
    understanding: VideoUnderstanding
    asset: VideoAsset
    vision: Vision
    targets: list[dict[str, Any]]
    times: list[float]
    allow_questions: bool = True

    def limit(self, name: str, default: int) -> int:
        return int(self.services.param(name, default))


# ---------- question gate ----------


def gate_questions(asked: list[QuestionIn], state: dict[str, Any], deps: AnalystDeps) -> tuple[list[Question], list[Question]]:
    """Decide which of the agent's questions reach the user (design §5.3). Returns (to ask, removed)."""
    evidence = state.get("evidence") or {}
    previous = state.get("questions") or []
    known = {f["key"] for f in state.get("facts") or [] if f["provenance"] in ("declared", "answered") or (f.get("confidence") or 0) >= 0.85}
    asked_keys = {q["factKey"] for q in previous if q["status"] != "removed"}
    rounds = max((q["round"] for q in previous if q["status"] != "removed"), default=0)
    this_round = rounds + 1
    max_rounds, per_round = deps.limit("analyst.max_question_rounds", 2), deps.limit("analyst.max_questions", 6)
    enabled = deps.allow_questions and deps.services.part("agent.questions").candidate.id != "disabled"
    keep: list[Question] = []
    removed: list[Question] = []
    number = len(previous)
    seen: set[str] = set()
    for item in asked:
        number += 1
        effects = {" ".join(i.effect_ko.split()) for i in item.impact}
        reason = None
        if not enabled:
            reason = "이 실행은 사용자 질문을 쓰지 않아 보수적 가정으로 진행합니다."
        elif this_round > max_rounds:
            reason = f"질문 라운드 상한 {max_rounds}회를 넘었습니다."
        elif item.fact_key in known:
            reason = "이미 아는 사실입니다."
        elif item.fact_key in asked_keys or item.fact_key in seen:
            reason = "이미 물은 질문입니다."
        elif len(item.options) < 2:
            reason = "선택지가 둘 이상이어야 합니다."
        elif len(effects) < 2:
            reason = "어느 답이든 처리가 달라지지 않습니다."
        elif len(keep) >= per_round:
            reason = f"한 번에 묻는 질문은 {per_round}개까지입니다."
        seen.add(item.fact_key)
        first_target = next((t for t in deps.targets if t["id"] in item.targets and t["ranges"]), None)
        question = Question(
            id=f"Q{number}",
            fact_key=item.fact_key,
            round=this_round,
            prompt=item.prompt_ko,
            note=item.fact_label_ko,
            options=[QuestionOption(value=o.value, label=o.label, detail=o.detail) for o in item.options],
            basis=item.why_ko,
            citations=knowledge.citations_for(item.evidence_ids, evidence),
            impact=[Impact(option=i.option, effect=i.effect_ko) for i in item.impact],
            status="removed" if reason else "pending",
            removed_reason=reason,
            targets=[t for t in item.targets if any(x["id"] == t for x in deps.targets)],
            frame_at=first_target["ranges"][0][0] if first_target else None,
        )
        (removed if reason else keep).append(question)
    return keep, removed


def apply_answers(questions: list[Question], answers: dict[str, str]) -> tuple[list[Question], list[Fact]]:
    """Turn the user's answers into facts. "later" defers a question; "unknown" is recorded as not known."""
    facts: list[Fact] = []
    for question in questions:
        value = answers.get(question.id, "later")
        if value == "later":
            question.status = "deferred"
            continue
        question.status, question.answer = "answered", value
        option = next((o for o in question.options if o.value == value), None)
        facts.append(
            Fact(
                key=question.fact_key,
                label=question.note or question.fact_key,
                value=value,
                value_label="모름" if value == "unknown" else (option.label if option else value),
                provenance="answered",
                source=f"질문 {question.id} 답변",
                editable=True,
                options=[FactOption(value=o.value, label=o.label) for o in question.options],
            )
        )
    return questions, facts


# ---------- validator ----------


def validate_submission(submission: dict[str, Any], evidence: dict[str, dict[str, Any]], targets: list[dict[str, Any]], today: date | None = None) -> list[str]:
    """Deterministic checks on a finalize submission (design §5.2). Returns the problems in Korean."""
    issues: list[str] = []
    target_kind = {t["id"]: t["kind"] for t in targets}
    analyses = submission.get("analyses") or []
    ids = [a["id"] for a in analyses]
    if not analyses:
        issues.append("analyses가 비어 있습니다. 적어도 하나의 분석이 필요합니다.")
    if len(set(ids)) != len(ids):
        issues.append("analysis id가 겹칩니다.")
    for analysis in analyses:
        unknown = [e for e in analysis.get("evidence_ids", []) if e not in evidence]
        if unknown:
            issues.append(f"{analysis['id']}: 검색·열람 결과에 없는 근거 ID {unknown}. 도구 결과의 [대괄호] ID만 쓸 수 있습니다.")
        cited = [evidence[e] for e in analysis.get("evidence_ids", []) if e in evidence]
        if analysis["applicability"] != "uncertain" and not cited:
            issues.append(f"{analysis['id']}: 근거 인용이 없습니다. 근거를 찾거나 uncertain으로 두고 보수적 가정을 적으세요.")
        if analysis["applicability"] == "applies" and cited and all(e["authority"] == 3 for e in cited):
            issues.append(f"{analysis['id']}: 참고(3) 자료만으로 적용을 단정했습니다. 규범(1)이나 해석(2) 근거를 더하거나 uncertain으로 두세요.")
        if analysis["applicability"] == "uncertain" and not (analysis.get("conservative_assumption") or "").strip():
            issues.append(f"{analysis['id']}: uncertain이면 conservative_assumption을 적어야 합니다.")
        missing = [t for t in analysis.get("affected_targets", []) if t not in target_kind]
        if missing:
            issues.append(f"{analysis['id']}: 없는 대상 ID {missing}.")
    by_id = {a["id"]: a for a in analyses}
    requirement_ids = [r["id"] for r in submission.get("requirements") or []]
    if len(set(requirement_ids)) != len(requirement_ids):
        issues.append("requirement id가 겹칩니다.")
    for requirement in submission.get("requirements") or []:
        sources = [by_id.get(a) for a in requirement.get("derived_from_analysis_ids", [])]
        if not sources or any(s is None for s in sources):
            issues.append(f"{requirement['id']}: derived_from_analysis_ids가 비었거나 없는 분석을 가리킵니다.")
        elif all(s["applicability"] == "not_applies" for s in sources if s):
            issues.append(f"{requirement['id']}: not_applies 분석만으로는 요건을 만들 수 없습니다.")
        unknown = [e for e in requirement.get("evidence_ids", []) if e not in evidence]
        if unknown:
            issues.append(f"{requirement['id']}: 검색·열람 결과에 없는 근거 ID {unknown}.")
        if not requirement.get("targets"):
            issues.append(f"{requirement['id']}: targets가 비어 있습니다.")
        for target in requirement.get("targets", []):
            if target not in target_kind:
                issues.append(f"{requirement['id']}: 없는 대상 ID '{target}'.")
            elif requirement["scope"] not in SCOPES_BY_KIND.get(target_kind[target], set()):
                allowed = " · ".join(sorted(SCOPES_BY_KIND.get(target_kind[target], set())))
                issues.append(f"{requirement['id']}: 대상 {target}에는 scope '{requirement['scope']}'를 쓸 수 없습니다 ({allowed}).")
        for start, end in requirement.get("time_ranges") or []:
            if end <= start or start < 0:
                issues.append(f"{requirement['id']}: 잘못된 구간 [{start}, {end}].")
    return issues


def sanitize(submission: dict[str, Any], evidence: dict[str, dict[str, Any]], targets: list[dict[str, Any]], issues: list[str]) -> dict[str, Any]:
    """Make an unfixed submission safe to use: drop what cannot be verified and keep the result conservative."""
    known = {t["id"]: t["kind"] for t in targets}
    analyses = []
    for analysis in submission.get("analyses") or []:
        analysis = {**analysis, "evidence_ids": [e for e in analysis.get("evidence_ids", []) if e in evidence], "affected_targets": [t for t in analysis.get("affected_targets", []) if t in known]}
        if analysis["applicability"] != "uncertain" and not analysis["evidence_ids"]:
            analysis.update(applicability="uncertain", conservative_assumption=analysis.get("conservative_assumption") or "근거를 확인하지 못해 보수적으로 적용되는 것으로 가정")
        analyses.append(analysis)
    ids = {a["id"] for a in analyses}
    requirements = []
    for requirement in submission.get("requirements") or []:
        requirement = {**requirement, "evidence_ids": [e for e in requirement.get("evidence_ids", []) if e in evidence]}
        requirement["targets"] = [t for t in requirement.get("targets", []) if t in known and requirement["scope"] in SCOPES_BY_KIND.get(known[t], set())]
        requirement["derived_from_analysis_ids"] = [a for a in requirement.get("derived_from_analysis_ids", []) if a in ids]
        requirement["time_ranges"] = [r for r in requirement.get("time_ranges") or [] if r[1] > r[0] >= 0] or None
        if requirement["targets"]:
            requirements.append(requirement)
    unresolved = list(submission.get("unresolved") or []) + [{"item": f"검증에서 고치지 못한 사항: {issue}", "assumption": "검증할 수 없는 인용과 대상은 빼고 보수적으로 진행"} for issue in issues]
    return {"analyses": analyses, "requirements": requirements, "unresolved": unresolved}


# ---------- middleware: budgets, validator, nudges ----------


def _executed(messages: list[Any], name: str | None = None) -> int:
    return sum(1 for m in messages if isinstance(m, ToolMessage) and not m.additional_kwargs.get("blocked") and (name is None or m.name == name))


def _blocked(request: Any, text: str) -> ToolMessage:
    call = request.tool_call
    return ToolMessage(text, tool_call_id=call["id"], name=call["name"], status="error", additional_kwargs={"blocked": True})


def _cached(message: Any) -> Any:
    """A copy of the message whose last content block is a cache breakpoint (everything up to it is cached)."""
    content = message.content
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
    if not blocks or isinstance(message, AIMessage):
        return message
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return message.model_copy(update={"content": blocks})


class AnalystControl(AgentMiddleware):
    state_schema = AnalystState

    def __init__(self, deps: AnalystDeps):
        super().__init__()
        self.deps = deps
        self.max_tools = deps.limit("analyst.max_tool_calls", 25)
        self.max_searches = deps.limit("analyst.max_searches", 12)
        self.max_retries = deps.limit("analyst.max_retries", 2)
        # Claude caches a prompt only when the request marks it; other providers cache implicitly. The Anthropic
        # API takes one request-level marker (it caches up to the last block); through OpenRouter the marker
        # has to sit on a content block.
        part = deps.services.part("llm.analyst")
        self.cache = "request" if part.candidate.adapter == "anthropic" else "blocks" if part.vendor == "anthropic" else None

    @property
    def name(self) -> str:
        return "validate"

    def before_agent(self, state: AnalystState, runtime: Any) -> dict[str, Any] | None:
        """Runs once per analysis (not again when a question resumes), so the opening events are not repeated."""
        seeded = list((state.get("evidence") or {}).values())
        emit("activity", text="법령과 판례를 찾아 보호 요건을 정리하고 있습니다.", tone="info")
        emit("message", agent=AGENT, role="system", text=SYSTEM_PROMPT[:420] + " …")
        emit("state", channel="facts", writer=AGENT, summary=f"알려진 사실 {len(state.get('facts') or [])}건")
        if seeded:
            emit("state", channel="seed_bundle", writer=AGENT, summary=f"핵심 문서 {len(seeded)}건 사전 로딩: " + " · ".join(e["label"] for e in seeded[:9]))
        for key, used, limit in (("tool_calls", 0, self.max_tools), ("searches", 0, self.max_searches), ("question_rounds", 0, self.deps.limit("analyst.max_question_rounds", 2)), ("retries", 0, self.max_retries)):
            emit("budget", agent=AGENT, key=key, used=used, limit=limit)
        return None

    # -- budgets --

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        """Stop at the case's cost cap, and mark the prompt for caching: the loop resends a growing history
        every turn, and without cache breakpoints Claude bills all of it at the full rate each time
        (measured in M4: 122k uncached input tokens in six turns)."""
        self.deps.services.check_budget()
        if self.cache == "request":
            request = request.override(model_settings={**(request.model_settings or {}), "cache_control": {"type": "ephemeral"}})
        elif self.cache == "blocks" and request.messages:
            system = request.system_message
            request = request.override(system_message=_cached(system) if system is not None else None, messages=[*request.messages[:-1], _cached(request.messages[-1])])
        return handler(request)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        done = self.deps.services.tool_results
        key = f"{call['id']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False, default=str)}"
        if key in done:
            # When ask_user interrupts, LangGraph runs the other tool calls of the same turn again on resume.
            # Their results are kept so a search or a visual inspection is not paid for twice.
            return done[key]
        repeated = done.get(f"started:{key}", False)  # ask_user itself, running again with the user's answer
        done[f"started:{key}"] = True
        messages = request.state["messages"]
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        position = next((i for i, c in enumerate(last_ai.tool_calls) if c["id"] == call["id"]), 0) if last_ai else 0
        used = _executed(messages) + position + 1
        name = call["name"]
        if name != "finalize" and used > self.max_tools - 1:  # the last call is kept for finalize
            emit("budget", agent=AGENT, key="tool_calls", used=self.max_tools, limit=self.max_tools)
            return _blocked(request, f"도구 호출 예산({self.max_tools}회)이 끝났습니다. 지금까지의 근거로 finalize를 호출하세요. 확인하지 못한 것은 uncertain과 unresolved에 적으세요.")
        if name == "search_law":
            query = " ".join(str(call["args"].get("query", "")).split())
            kinds = sorted(call["args"].get("kinds") or [])
            for message in messages:
                if isinstance(message, AIMessage) and message is not last_ai:
                    for earlier in message.tool_calls:
                        if earlier["name"] == "search_law" and " ".join(str(earlier["args"].get("query", "")).split()) == query and sorted(earlier["args"].get("kinds") or []) == kinds:
                            return _blocked(request, "같은 질의를 이미 검색했습니다. 이전 결과를 쓰거나 다른 질의로 검색하세요.")
            searches = _executed(messages, "search_law") + sum(1 for c in (last_ai.tool_calls[:position] if last_ai else []) if c["name"] == "search_law") + 1
            if searches > self.max_searches:
                return _blocked(request, f"검색 예산({self.max_searches}회)이 끝났습니다. 찾은 근거로 판단하고, 부족하면 uncertain으로 두세요.")
            emit("budget", agent=AGENT, key="searches", used=searches, limit=self.max_searches)
        if not repeated:
            emit("budget", agent=AGENT, key="tool_calls", used=min(used, self.max_tools), limit=self.max_tools)
        done[key] = result = handler(request)
        return result

    # -- validator (runs before every model turn; acts only when a submission is waiting) --

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AnalystState, runtime: Any) -> dict[str, Any] | None:
        submission = state.get("submission")
        if not submission:
            turns = sum(1 for m in state["messages"] if isinstance(m, AIMessage))
            if turns > self.max_tools + 8:  # the model keeps going without submitting
                emit("activity", text="분석이 예산 안에 끝나지 않아 보수적 가정으로 마칩니다.", tone="warn")
                return {"result": {"analyses": [], "requirements": [], "unresolved": [], "forced": True}, "jump_to": "end"}
            return None
        node = f"{AGENT}/validate"
        task = f"validate#{state.get('retries', 0)}-{int(bool(state.get('coverage_asked')))}-{len(state['messages'])}"
        emit("edge", edge=f"{AGENT}/finalize->{node}", dir="fwd")
        emit("node.start", node=node, task=task)
        evidence = state.get("evidence") or {}
        issues = validate_submission(submission, evidence, self.deps.targets)
        retries = state.get("retries", 0)
        update: dict[str, Any]
        if issues and retries < self.max_retries:
            emit("validator", node=node, ok=False, issues=issues)
            emit("budget", agent=AGENT, key="retries", used=retries + 1, limit=self.max_retries)
            feedback = "검증기가 다음 문제를 찾았습니다. 고쳐서 finalize를 다시 호출하세요.\n" + "\n".join(f"- {i}" for i in issues)
            update = {"submission": None, "retries": retries + 1, "messages": [HumanMessage(feedback)]}
            emit("edge", edge=f"{node}->{AGENT}/model", dir="fwd")
        elif issues:
            emit("validator", node=node, ok=False, issues=[*issues, "재시도 상한에 닿아 미해결로 기록합니다."])
            emit("activity", text=f"인용 검증에서 {len(issues)}건을 고치지 못해 미해결로 남겼습니다.", tone="warn")
            update = {"result": sanitize(submission, evidence, self.deps.targets, issues), "issues": issues, "submission": None, "jump_to": "end"}
        else:
            uncovered = [] if state.get("coverage_asked") else knowledge.uncovered_patterns(self.deps.understanding, state.get("facts") or [], submission.get("analyses") or [])
            if uncovered:
                names = ", ".join(f"{p['id']} {p['name']}" for p in uncovered)
                emit("validator", node=node, ok=False, issues=[f"커버리지 점검: 관찰에 있는 '{p['name']}' 패턴이 검토되지 않은 것 같습니다 (질문으로 1회 되돌림)" for p in uncovered])
                emit("edge", edge=f"{node}->{AGENT}/model", dir="fwd")
                question = f"커버리지 점검(1회, 강제 아님): 관찰과 맞닿는 참고 패턴 {names}을(를) 분석에서 다루지 않은 것 같습니다. 이미 검토했다면 같은 결과로, 아니라면 보완해서 finalize를 다시 호출하세요."
                update = {"submission": None, "coverage_asked": True, "messages": [HumanMessage(question)]}
            else:
                emit("validator", node=node, ok=True, issues=[])
                update = {"result": submission, "submission": None, "jump_to": "end"}
        emit("node.end", node=node, task=task, result="ok", costUsd=0, writes=["validation"])
        return update

    @hook_config(can_jump_to=["model", "end"])
    def after_model(self, state: AnalystState, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not last.tool_calls and not state.get("result"):
            nudges = state.get("nudges", 0)
            if nudges >= 2:
                emit("activity", text="분석 모델이 결과를 제출하지 않아 보수적 가정으로 마칩니다.", tone="warn")
                return {"result": {"analyses": [], "requirements": [], "unresolved": [], "forced": True}, "jump_to": "end"}
            return {"nudges": nudges + 1, "messages": [HumanMessage("분석 결과는 finalize 도구로 제출해야 합니다. 지금 finalize를 호출하세요.")], "jump_to": "model"}
        return None


# ---------- tools ----------


def build_tools(deps: AnalystDeps) -> list[Any]:
    services = deps.services
    top_k = deps.limit("rag.top_k", 8)

    @tool
    def search_law(query: str, runtime: ToolRuntime[CaseContext, AnalystState], kinds: list[KINDS] | None = None) -> Command:
        """한국 개인정보 법령, 판례, 결정례, 안내서를 검색합니다. 결과의 [대괄호] 값이 인용에 쓰는 근거 ID입니다.

        Args:
            query: 쟁점을 드러내는 구체적인 한국어 질의 (예: "촬영 동의의 범위를 넘는 공표")
            kinds: 문서 종류로 좁힐 때만 지정. law(법령) admrul(고시) prec(판례) detc(헌재) expc(법령해석례) ppc(개인정보위 결정) guideline(안내서) mediation(분쟁조정)
        """
        try:
            found = services.search(query, k=top_k, kinds=list(kinds) if kinds else None)
        except RetrievalError as error:
            return Command(update={"messages": [ToolMessage(f"검색 실패: {error}", name="search_law", tool_call_id=runtime.tool_call_id, status="error")]})
        items: dict[str, dict[str, Any]] = {}
        for hit in found:
            item = knowledge.make_evidence(hit.doc_id, hit.unit_id, hit.citation, hit.label, hit.kind, hit.authority, hit.text)
            items.setdefault(item["id"], item)
        lines = [f"{knowledge.distribution(list(items.values()))} — {len(items)}건"]
        lines += [f"[{e['id']}] {e['label']} · {knowledge.AUTHORITY_LABEL[e['authority']]}\n  {e['text']}" for e in items.values()]
        if not items:
            lines.append("검색 결과가 없습니다. 질의를 바꾸거나 근거 부족(uncertain)으로 기록하세요.")
        return Command(update={"evidence": items, "messages": [ToolMessage("\n".join(lines), name="search_law", tool_call_id=runtime.tool_call_id)]})

    @tool
    def get_document(ref: str, runtime: ToolRuntime[CaseContext, AnalystState], unit: str | None = None) -> Command:
        """문서의 원문을 엽니다. 요약이 아니라 원문입니다.

        Args:
            ref: 근거 ID(예: "pipa#제17조"), 문서 ID 또는 인용 표기(예: "개인정보 보호법")
            unit: 조문이나 섹션 ID(예: "제25조의2", "판결요지"). ref가 근거 ID면 생략합니다.
        """
        doc_ref, _, from_id = ref.partition("#")
        unit_id = unit or from_id or None
        try:
            document = read_document(services.store, doc_ref, unit_id)
        except RetrievalError as error:
            return Command(update={"messages": [ToolMessage(f"열람 실패: {error}", name="get_document", tool_call_id=runtime.tool_call_id, status="error")]})
        units = document["units"]
        if unit_id is None and len(units) > 3:
            listing = ", ".join(f"{u['unit_id']}" for u in units[:60])
            text = f"{document['citation']} — 단위 {len(units)}개. unit을 지정해 다시 여세요: {listing}{' …' if len(units) > 60 else ''}"
            return Command(update={"messages": [ToolMessage(text, name="get_document", tool_call_id=runtime.tool_call_id)]})
        items: dict[str, dict[str, Any]] = {}
        parts = []
        for u in units[:3]:
            item = knowledge.make_evidence(document["doc_id"], u["unit_id"], document["citation"], u["label"], document["kind"], document["authority"], u["text"])
            items[item["id"]] = item
            body = u["text"] if len(u["text"]) <= 3200 else u["text"][:3200] + " …(이하 생략)"
            parts.append(f"[{item['id']}] {item['label']} · {knowledge.AUTHORITY_LABEL[item['authority']]}\n{body}")
        return Command(update={"evidence": items, "messages": [ToolMessage("\n\n".join(parts), name="get_document", tool_call_id=runtime.tool_call_id)]})

    @tool
    def inspect_visual(question: str, target: str | None = None, start: float | None = None, end: float | None = None) -> str:
        """영상의 특정 대상이나 구간을 다시 봅니다 (예: "가슴의 글자가 이름이 적힌 명찰인가", "미성년자로 보이는 사람이 있는가").

        Args:
            question: 예/아니오로 답할 수 있는 구체적인 질문
            target: 대상 ID (예: person-2, text-1). 지정하면 그 대상 주변을 확대해 봅니다.
            start: 볼 구간의 시작(초). target이 없을 때 사용
            end: 볼 구간의 끝(초)
        """
        frames = _inspection_frames(deps, target, start, end)
        try:
            answer = deps.vision.inspect(question, frames=frames, node=f"{AGENT}/inspect_visual", agent=AGENT)
        except ModelCallError as error:  # the model can go on without this look; a tool error must not end the case
            return f"시각 점검에 실패했습니다: {error}. 다시 시도하거나, 확인하지 못한 것으로 두고 uncertain과 unresolved에 기록하세요."
        return f"{answer.get('answer', '')} (판정 {answer.get('verdict', 'unclear')}, 신뢰도 {float(answer.get('confidence') or 0):.2f})"

    @tool
    def checklist(runtime: ToolRuntime[CaseContext, AnalystState]) -> str:
        """참고 패턴 목록과 이 영상의 관찰이 맞닿는 지점을 돌려줍니다. 놓친 쟁점을 떠올리는 참고이며 분류를 강제하지 않습니다."""
        return knowledge.checklist_text(deps.understanding, runtime.state.get("facts") or [])

    @tool
    def ask_user(questions: list[QuestionIn], runtime: ToolRuntime[CaseContext, AnalystState]) -> Command:
        """판단에 필요한데 영상과 메모로 알 수 없는 사실을 사용자에게 묻습니다. 여러 질문을 한 번에 모아 호출하세요.
        이미 아는 사실, 이미 물은 것, 답에 따라 처리가 달라지지 않는 질문은 게이트에서 빠집니다."""
        state = runtime.state
        keep, removed = gate_questions(questions, state, deps)
        if not keep:
            reasons = "; ".join(f"{q.fact_key}: {q.removed_reason}" for q in removed) or "질문이 없습니다"
            emit("state", channel="questions", writer="질문 게이트", summary=f"생성 {len(questions)} → 질문 0 ({reasons})")
            text = f"질문하지 않았습니다 ({reasons}). 보수적 가정으로 진행하고 unresolved에 기록하세요."
            return Command(update={"questions": [q.wire() for q in removed], "messages": [ToolMessage(text, name="ask_user", tool_call_id=runtime.tool_call_id)]})
        round_number, max_rounds = keep[0].round, deps.limit("analyst.max_question_rounds", 2)
        emit("state", channel="questions", writer="질문 게이트", summary=f"생성 {len(questions)} → 제거 {len(removed)} → 질문 {len(keep)}")
        emit("budget", agent=AGENT, key="question_rounds", used=round_number, limit=max_rounds)
        answers = interrupt(
            {
                "payload": "questions",
                "summary": f"질문 {len(keep)}건 (라운드 {round_number}/{max_rounds})",
                "questions": [q.wire() for q in keep],
                "removed": [q.wire() for q in removed],
            }
        )
        answered, facts = apply_answers(keep, dict(answers or {}))
        lines = []
        for question in answered:
            if question.status == "deferred":
                lines.append(f"- {question.fact_key}: 답하지 않음(나중에) → 보수적 가정으로 진행하고 unresolved에 기록")
            elif question.answer == "unknown":
                lines.append(f"- {question.fact_key}: 모름 → 보수적 가정으로 진행하고 unresolved에 기록")
            else:
                label = next((o.label for o in question.options if o.value == question.answer), question.answer)
                lines.append(f"- {question.fact_key} = {question.answer} ({label})")
        text = "사용자 답변:\n" + "\n".join(lines) + "\n답변에 의존하는 분석만 다시 평가하세요."
        return Command(
            update={
                "questions": [q.wire() for q in [*answered, *removed]],
                "facts": [f.wire() for f in facts],
                "messages": [ToolMessage(text, name="ask_user", tool_call_id=runtime.tool_call_id)],
            }
        )

    @tool
    def finalize(analyses: list[AnalysisIn], requirements: list[RequirementIn], runtime: ToolRuntime[CaseContext, AnalystState], unresolved: list[UnresolvedIn] | None = None) -> Command:
        """분석 결과를 제출합니다. 검증기가 인용과 연결을 확인하고, 문제가 있으면 사유를 돌려줍니다.

        Args:
            analyses: 법적 질문별 분석. 유지해도 되는 대상도 not_applies 분석으로 남깁니다.
            requirements: 보호 요건. 각 요건은 하나 이상의 분석에서 나와야 합니다.
            unresolved: 답을 받지 못했거나 근거가 부족한 사항과 적용한 가정
        """
        submission = {
            "analyses": [a.model_dump() for a in analyses],
            "requirements": [r.model_dump() for r in requirements],
            "unresolved": [u.model_dump() for u in unresolved or []],
        }
        text = f"제출됨: 분석 {len(analyses)}건 · 요건 {len(requirements)}건 · 미해결 {len(unresolved or [])}건 → 인용 검증기"
        return Command(update={"submission": submission, "messages": [ToolMessage(text, name="finalize", tool_call_id=runtime.tool_call_id)]})

    return [search_law, get_document, inspect_visual, checklist, ask_user, finalize]


def _inspection_frames(deps: AnalystDeps, target: str | None, start: float | None, end: float | None) -> list[tuple[int, Any]]:
    """Up to four (frame index, crop) pairs for a visual inspection."""
    understanding, times = deps.understanding, deps.times
    if target:
        kind = next((t["kind"] for t in deps.targets if t["id"] == target), None)
        tracks = understanding.tracks_for(target, "body" if kind == "person" else "text")
        points = sorted((p for t in tracks for p in t.points), key=lambda p: p.f)
        if start is not None or end is not None:
            lo, hi = start or 0.0, end if end is not None else times[-1]
            points = [p for p in points if lo <= p.t <= hi] or points
        if points:
            step = max(1, len(points) // 3)
            picks = points[::step][:3]
            return [(p.f, _union(understanding, target, p.f) or p.box) for p in picks]
    lo = start or 0.0
    hi = end if end is not None else times[-1]
    count = 4 if hi - lo > 2 else 2
    return [(index_at(times, lo + (hi - lo) * (i + 0.5) / count), None) for i in range(count)]


def _union(understanding: VideoUnderstanding, target: str, frame: int) -> Any:
    """Body box of a person at a frame when there is one (so the crop shows clothing and nearby text)."""
    for track in understanding.tracks_for(target, "body"):
        box = box_at(track, frame)
        if box is not None:
            return box
    return None


def build_analyst(deps: AnalystDeps) -> Any:
    model = deps.services.chat("llm.analyst", max_tokens=6000, effort="medium")  # multi-step tool use: medium is the recommended start
    return create_agent(model, tools=build_tools(deps), system_prompt=SYSTEM_PROMPT, middleware=[AnalystControl(deps)], context_schema=CaseContext, name=AGENT)


__all__ = ["AGENT", "AnalystDeps", "AnalystState", "BudgetExceeded", "build_analyst", "gate_questions", "apply_answers", "validate_submission", "sanitize"]
