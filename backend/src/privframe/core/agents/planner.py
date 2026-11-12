"""Planner: requirements → redaction decisions, with a deterministic validator in the loop (design §5.6).

The model proposes; code checks that every requirement is met by a decision that covers its targets, scope
and time ranges, and sends concrete problems back (at most `planner.max_generations` attempts). If no attempt
validates, a rule-based plan that redacts exactly what the requirements name is used instead, so a case never
proceeds without a plan that satisfies its requirements.

The model chooses what to redact and why; it does not choose when or how. Time ranges come from the
requirements and the tracks (a model that restates "0.0–9.9" for a video ending at 9.97 leaves frames
uncovered), and there is one way to redact each kind of target: a mosaic on screen, removal for file metadata.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from ..domain import METHOD_LABEL, REDACT_METHOD, Decision, Plan, Requirement, Unsatisfied, VideoUnderstanding, clip_ranges, merge_ranges
from ..geometry import TOLERANCE_S, seen
from ..runtime import ModelCallError, Services, activity, call_json, emit

SCOPES_BY_KIND = {"person": {"face", "body"}, "text": {"text"}, "plate": {"plate"}, "metadata": {"metadata"}}
DEFAULT_SHAPE = {"face": "ellipse", "body": "box", "text": "box", "plate": "box"}  # metadata has no shape

PROMPT = """당신은 영상 비식별화 계획을 세우는 Planner입니다. 법률 분석가가 정리한 보호 요건을 처리 결정으로 바꿉니다.

규칙
- 모든 요건은 그 대상과 구간을 덮는 결정으로 충족되어야 합니다. 충족할 수 없으면 unsatisfied에 이유를 적습니다.
- 요건이 없는 대상은 처리하지 않습니다(method=keep). 과하게 가리면 영상의 쓸모가 줄어듭니다.
- method는 대상의 종류로 정해집니다. 화면에 보이는 대상(인물 · 글자 · 번호판)은 mosaic(모자이크) 또는 keep(유지), 파일 메타데이터(meta-로 시작하는 대상)는 remove(삭제) 또는 keep(유지)입니다. 다른 처리 방식은 없고, 강도와 구간은 시스템이 정합니다.
- scope는 요건의 scope를 따릅니다(메타데이터 대상은 metadata).
- satisfies에는 이 결정이 충족하는 요건 ID를, keep 결정에는 유지 근거가 되는 analysis_id를 적습니다(없으면 null).
- rationale_ko는 한두 문장으로 적습니다.

[보호 요건]
{requirements}

[분석 요약]
{analyses}

[대상]
{targets}

[사실]
{facts}
{feedback}"""


class DecisionOut(BaseModel):
    target_id: str
    scope: Literal["face", "body", "text", "plate", "metadata"]
    method: Literal["keep", "mosaic", "remove"]
    satisfies: list[str]
    analysis_id: str | None
    rationale_ko: str
    risk: Literal["high", "medium", "low", "uncertain"]


class UnsatisfiedOut(BaseModel):
    requirement_id: str
    reason: str


class PlanOut(BaseModel):
    decisions: list[DecisionOut]
    unsatisfied: list[UnsatisfiedOut]


def visible_ranges(understanding: VideoUnderstanding, target_id: str, scope: str) -> list[tuple[float, float]]:
    return seen(understanding, target_id, scope)


def required_ranges(requirement: dict[str, Any], understanding: VideoUnderstanding, target_id: str) -> list[tuple[float, float]]:
    visible = visible_ranges(understanding, target_id, requirement["scope"])
    stated = [(float(a), float(b)) for a, b in requirement.get("ranges") or []]
    return clip_ranges(stated, visible) if stated else visible


def _covers(needed: list[tuple[float, float]], given: list[tuple[float, float]]) -> bool:
    merged = merge_ranges(given, gap=TOLERANCE_S)
    return all(any(c - TOLERANCE_S <= a and b <= d + TOLERANCE_S for c, d in merged) for a, b in needed)


def coverage_gap(requirement: dict[str, Any], target: str, decisions: list[dict[str, Any]], understanding: VideoUnderstanding) -> str | None:
    """Why `requirement` is not met for `target` by the decisions, or None when it is."""
    mine = [d for d in decisions if d["targetId"] == target and requirement["id"] in d["satisfies"]]
    if requirement["scope"] == "metadata":
        return None if any(d["method"] == "remove" for d in mine) else f"{requirement['id']}: 대상 {target}을(를) 삭제하는 결정이 없습니다 (satisfies에 {requirement['id']}을(를) 넣은 remove 결정이 필요)."
    needed = required_ranges(requirement, understanding, target)
    if not needed:
        return None  # the target is never visible in the required ranges
    scopes = {requirement["scope"], "body"} if requirement["scope"] == "face" else {requirement["scope"]}
    given = [(float(r[0]), float(r[1])) for d in mine if d["method"] == "mosaic" and d["scope"] in scopes for r in d["ranges"]]
    if not given:
        return f"{requirement['id']}: 대상 {target}에 대한 처리 결정이 없습니다 (satisfies에 {requirement['id']}을(를) 넣은 결정이 필요)."
    if not _covers(needed, given):
        return f"{requirement['id']}: 대상 {target}의 필요한 구간 {needed}을(를) 결정 구간 {given}이(가) 다 덮지 못합니다."
    return None


def validate_plan(decisions: list[dict[str, Any]], unsatisfied: list[dict[str, Any]], requirements: list[dict[str, Any]], targets: list[dict[str, Any]], understanding: VideoUnderstanding) -> list[str]:
    """Problems with a proposed plan, in Korean. `decisions` use the wire field names."""
    issues: list[str] = []
    kinds = {t["id"]: t["kind"] for t in targets}
    by_id = {r["id"]: r for r in requirements}
    excused = {u["requirementId"] for u in unsatisfied}
    for decision in decisions:
        target, scope = decision["targetId"], decision["scope"]
        if target not in kinds:
            issues.append(f"없는 대상 ID '{target}'에 대한 결정이 있습니다.")
            continue
        if scope not in SCOPES_BY_KIND.get(kinds[target], set()):
            issues.append(f"{target}: scope '{scope}'를 쓸 수 없는 대상입니다.")
        if decision["method"] == "keep":
            continue
        if decision["method"] != REDACT_METHOD[kinds[target]]:
            issues.append(f"{target}: 이 대상의 처리 방식은 {REDACT_METHOD[kinds[target]]} 또는 keep입니다 ('{decision['method']}'는 쓸 수 없음).")
        linked = [by_id[r] for r in decision["satisfies"] if r in by_id]
        if not linked:
            issues.append(f"{target}: {METHOD_LABEL[decision['method']]} 결정이 어떤 요건에도 연결되지 않았습니다. 요건이 없으면 keep으로 두세요.")
        if any(target not in r["targets"] for r in linked):
            issues.append(f"{target}: satisfies에 이 대상을 포함하지 않는 요건이 있습니다.")
        if kinds[target] != "metadata" and not understanding.tracks_for(target, scope):
            issues.append(f"{target}: 추적된 영역이 없어 처리할 수 없습니다. unsatisfied에 적으세요.")
    for requirement in requirements:
        if requirement["id"] in excused:
            continue
        issues.extend(gap for target in requirement["targets"] if (gap := coverage_gap(requirement, target, decisions, understanding)))
    return issues


def rule_plan(requirements: list[dict[str, Any]], targets: list[dict[str, Any]], understanding: VideoUnderstanding) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One redaction per (requirement target, scope); a requirement with nothing to track is unsatisfied."""
    kinds = {t["id"]: t["kind"] for t in targets}
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    unsatisfied: list[dict[str, Any]] = []
    for requirement in requirements:
        scope = requirement["scope"]
        for target in requirement["targets"]:
            needed = [] if scope == "metadata" else required_ranges(requirement, understanding, target)
            if scope != "metadata" and not needed:
                unsatisfied.append({"requirementId": requirement["id"], "reason": f"{target}의 처리할 영역을 추적하지 못했습니다."})
                continue
            entry = decisions.setdefault((target, scope), {"targetId": target, "scope": scope, "method": REDACT_METHOD[kinds.get(target, "person")], "shape": DEFAULT_SHAPE.get(scope), "ranges": [], "satisfies": [], "analysisId": None, "rationale": "", "risk": "medium"})
            entry["ranges"] = merge_ranges([*entry["ranges"], *needed])
            entry["satisfies"].append(requirement["id"])
            entry["rationale"] = requirement["statement"]
    return list(decisions.values()), unsatisfied


def _normalize(out: PlanOut, requirements: list[dict[str, Any]], understanding: VideoUnderstanding) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Model output → wire-shaped decisions. Ranges are never the model's: a redaction covers what its
    requirements need, a kept target is listed with everything that is visible of it."""
    by_id = {r["id"]: r for r in requirements}
    decisions = []
    for item in out.decisions:
        satisfies = [r for r in item.satisfies if r in by_id]
        visible = visible_ranges(understanding, item.target_id, item.scope)
        needed = merge_ranges([r for rid in satisfies for r in required_ranges(by_id[rid], understanding, item.target_id)]) if item.scope != "metadata" else []
        ranges = visible if item.method == "keep" else needed or visible
        decisions.append({"targetId": item.target_id, "scope": item.scope, "method": item.method, "shape": DEFAULT_SHAPE.get(item.scope), "ranges": ranges, "satisfies": satisfies, "analysisId": item.analysis_id, "rationale": item.rationale_ko, "risk": item.risk})
    return decisions, [{"requirementId": u.requirement_id, "reason": u.reason} for u in out.unsatisfied if u.requirement_id in by_id]


def make_plan(
    services: Services,
    understanding: VideoUnderstanding,
    targets: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    *,
    version: int = 1,
) -> Plan:
    decisions: list[dict[str, Any]] = []
    unsatisfied: list[dict[str, Any]] = []
    accepted = not requirements  # nothing to plan: every target is kept
    feedback = ""
    attempts = int(services.param("planner.max_generations", 3))
    payload = {"requirements": requirements, "targets": targets}
    for attempt in range(1, attempts + 1):
        if accepted:
            break
        prompt = PROMPT.format(
            requirements="\n".join(f"- {r['id']} (대상 {', '.join(r['targets'])} · scope {r['scope']} · 구간 {r.get('ranges') or '보이는 전 구간'}): {r['statement']}" for r in requirements),
            analyses="\n".join(f"- {a['id']} [{a['applicability']}] {a['question']} → 대상 {', '.join(a['targets']) or '없음'}" for a in analyses),
            targets="\n".join(f"- {t['id']} ({t['kind']}, {t['label']}): " + ("파일 메타데이터" if t["kind"] == "metadata" else f"보이는 구간 {t['ranges']}") for t in targets),
            facts="\n".join(f"- {f['key']} = {f['value']} ({f['valueLabel']}, {f['provenance']})" for f in facts) or "- 없음",
            feedback=feedback,
        )
        try:
            out = call_json(services, "llm.planner", PlanOut, [{"role": "user", "content": prompt}], node="plan", agent="planner", payload=payload)
        except ModelCallError as error:
            emit("validator", node="plan", ok=False, issues=[f"생성 {attempt}회차 실패: {error}"])
            continue
        decisions, unsatisfied = _normalize(out, requirements, understanding)
        issues = validate_plan(decisions, unsatisfied, requirements, targets, understanding)
        emit("validator", node="plan", ok=not issues, issues=issues)
        if not issues:
            accepted = True
            break
        feedback = "\n[이전 계획의 문제 — 고쳐서 다시 작성]\n" + "\n".join(f"- {i}" for i in issues)
    if not accepted:
        activity("계획 모델의 결과가 검증을 통과하지 못해, 요건을 그대로 따르는 규칙 기반 계획을 씁니다.", "warn")
        decisions, unsatisfied = rule_plan(requirements, targets, understanding)
        emit("validator", node="plan", ok=True, issues=[])
    return finish_plan(decisions, unsatisfied, requirements, analyses, targets, version)


def finish_plan(decisions: list[dict[str, Any]], unsatisfied: list[dict[str, Any]], requirements: list[dict[str, Any]], analyses: list[dict[str, Any]], targets: list[dict[str, Any]], version: int) -> Plan:
    """Attach labels, citations and ids, and add a keep decision for every personal target the plan left out,
    so the review screen lists everything the system saw."""
    labels = {t["id"]: t["label"] for t in targets}
    kinds = {t["id"]: t["kind"] for t in targets}
    by_id = {r["id"]: Requirement.model_validate(r) for r in requirements}
    covered = {d["targetId"] for d in decisions}
    for target in targets:
        kind = kinds[target["id"]]
        if target["id"] in covered or (kind != "metadata" and not target["ranges"]):
            continue
        reason = next((a for a in analyses if target["id"] in a["targets"] and a["applicability"] == "not_applies"), None)
        if not target["personal"] and reason is None:
            continue
        scope = "face" if kind == "person" else kind
        decisions.append({"targetId": target["id"], "scope": scope, "method": "keep", "shape": DEFAULT_SHAPE.get(scope), "ranges": target["ranges"], "satisfies": [], "analysisId": reason["id"] if reason else None, "rationale": reason["reasoning"] if reason else "이 대상을 처리해야 한다는 요건이 도출되지 않아 그대로 둡니다.", "risk": "low" if reason else "uncertain"})
    order = {t["id"]: i for i, t in enumerate(targets)}
    decisions.sort(key=lambda d: (d["method"] == "keep", order.get(d["targetId"], 999)))
    out: list[Decision] = []
    for number, item in enumerate(decisions, start=1):
        linked = [by_id[r] for r in item["satisfies"] if r in by_id]
        citations = {c.id: c for r in linked for c in r.citations}
        if not linked and item.get("analysisId"):
            analysis = next((a for a in analyses if a["id"] == item["analysisId"]), None)
            citations = {c["id"]: c for c in (analysis or {}).get("citations", [])}
        out.append(
            Decision(
                id=f"D-{number}",
                target_id=item["targetId"],
                target_label=labels.get(item["targetId"], item["targetId"]),
                scope=item["scope"],
                method=item["method"],
                shape=item.get("shape"),
                ranges=item["ranges"],
                satisfies=item["satisfies"],
                analysis_id=item.get("analysisId"),
                rationale=item["rationale"] or (linked[0].statement if linked else ""),
                citations=list(citations.values()),
                risk=item.get("risk", "medium"),
            )
        )
    return Plan(version=version, decisions=out, unsatisfied=[Unsatisfied.model_validate(u) for u in unsatisfied])
