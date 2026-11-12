"""The case report: a per-second script of what was done and why, plus the record of questions, edits, audit
and output verification (design FR-11). Tables and citations are assembled from data, not written by a model.
"""
from __future__ import annotations

import html
from typing import Any

from .util import now_iso
from .domain import METHOD_LABEL, SCOPE_LABEL, SHAPE_LABEL, Contract, ScriptRow, VerificationRow
from .understanding import timecode


class OutputInfo(Contract):
    filename: str
    size_bytes: int
    codec: str


class Report(Contract):
    finished_at: str
    elapsed_sec: float
    cost_usd: float
    output: OutputInfo
    verification: list[VerificationRow]
    script: list[ScriptRow]
    history: list[str]
    notices: list[str]
    citations: list[dict[str, Any]]


def build_script(plan: dict[str, Any], requirements: list[dict[str, Any]], segments: list[dict[str, Any]], duration: float = 0.0) -> list[ScriptRow]:
    """One row per time range of every decision, ordered by time. A decision about file metadata has no place
    on the timeline and is listed for the whole file."""
    statements = {r["id"]: r["statement"] for r in requirements}

    def scene_at(t: float) -> str:
        # a range that starts exactly on a cut belongs to the scene that begins there
        segment = next((s for s in segments if s["start"] - 1e-6 <= t < s["end"] - 1e-6), segments[-1] if segments else None)
        return (segment["label"] or segment["id"]) if segment else ""

    rows: list[ScriptRow] = []
    for decision in plan["decisions"]:
        basis = " / ".join(statements[r] for r in decision["satisfies"] if r in statements) or decision["rationale"]
        if decision["scope"] == "metadata":
            rows.append(ScriptRow(start=0.0, end=round(duration, 2), scene="파일 전체", target=decision["targetLabel"], treatment=METHOD_LABEL[decision["method"]], basis=basis))
            continue
        if decision["method"] == "keep":
            treatment, basis = "유지", decision["rationale"]
        else:
            treatment = f"{SCOPE_LABEL[decision['scope']]} {METHOD_LABEL[decision['method']]} · {SHAPE_LABEL[decision['shape']]}"
        for start, end in decision["ranges"]:
            rows.append(ScriptRow(start=round(start, 2), end=round(end, 2), scene=scene_at(start), target=decision["targetLabel"], treatment=treatment, basis=basis))
    return sorted(rows, key=lambda r: (r.start, r.target))


def build_history(questions: list[dict[str, Any]], identity: dict[str, Any] | None, edits: list[str], findings: list[dict[str, Any]]) -> list[str]:
    history: list[str] = []
    if identity and identity.get("status") == "answered":
        label = next((o["label"] for o in identity["options"] if o["value"] == identity.get("answer")), identity.get("answer"))
        history.append(f"대상 확인: {identity['prompt']} → {label}")
    for question in questions:
        if question["status"] == "removed":
            continue
        if question["status"] == "answered":
            label = "모름 (보수적 가정 적용)" if question.get("answer") == "unknown" else next((o["label"] for o in question["options"] if o["value"] == question.get("answer")), question.get("answer"))
            history.append(f"{question['id']} {question['prompt']} → {label}")
        else:
            history.append(f"{question['id']} {question['prompt']} → 미답 (보수적 가정 적용)")
    history.extend(edits)
    for finding in findings:
        if finding["status"] == "applied":
            history.append(f"감사 {finding['id']} 적용: {finding['title']}")
        elif finding["status"] == "rejected":
            history.append(f"감사 {finding['id']} 위험 수용: {finding['title']} (사유: {finding.get('rejectReason') or '없음'})")
    return history


def build_notices(unresolved: list[dict[str, Any]], questions: list[dict[str, Any]], plan: dict[str, Any], has_audio: bool, location_tags: list[str] | None = None, removed_tags: list[str] | None = None) -> list[str]:
    notices = [f"확인하지 못한 사항: {u['item']} → {u['assumption']}" for u in unresolved]
    notices += [f"요건 {u['requirementId']}을(를) 충족하지 못했습니다: {u['reason']}" for u in plan.get("unsatisfied", [])]
    deferred = [q["id"] for q in questions if q["status"] == "deferred"]
    if deferred:
        notices.append(f"답하지 않은 질문({', '.join(deferred)})은 보수적 가정으로 처리했습니다.")
    if has_audio:
        notices.append("음성은 처리하지 않았습니다. 대화에 이름이나 장소가 들릴 수 있습니다.")
    if removed_tags:
        notices.append(f"원본 파일의 촬영 위치 메타데이터({', '.join(removed_tags)})는 계획에 따라 출력 파일에서 삭제했습니다.")
    elif location_tags:
        notices.append(f"원본 파일의 촬영 위치 메타데이터({', '.join(location_tags)})는 삭제해야 한다는 요건이 없어 출력 파일에 그대로 있습니다. 스크립트의 '촬영 위치 정보' 행에 이유가 있습니다.")
    notices.append("이 보고서는 법률 자문이 아닙니다. 근거는 수집한 법령·판례 원문에 한정되며, 최종 판단과 책임은 게시하는 사람에게 있습니다.")
    return notices


def collect_citations(plan: dict[str, Any], requirements: list[dict[str, Any]], analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for source in (*plan["decisions"], *requirements, *analyses):
        for citation in source.get("citations", []):
            found.setdefault(citation["id"], citation)
    return sorted(found.values(), key=lambda c: (c["authority"], c["label"]))


def assemble(*, state: dict[str, Any], elapsed_sec: float, cost_usd: float, output: dict[str, Any], verification: list[dict[str, Any]], has_audio: bool, location_tags: list[str] | None = None, removed_tags: list[str] | None = None) -> Report:
    plan = state["plan"]
    requirements = state.get("requirements") or []
    return Report(
        finished_at=now_iso(),
        elapsed_sec=round(elapsed_sec, 1),
        cost_usd=round(cost_usd, 5),
        output=OutputInfo(filename=output["filename"], size_bytes=output["sizeBytes"], codec=output["codec"]),
        verification=[VerificationRow.model_validate(v) for v in verification],
        script=build_script(plan, requirements, state.get("segments") or [], float(((state.get("video") or {}).get("meta") or {}).get("duration_s") or 0.0)),
        history=build_history(state.get("questions") or [], state.get("identity"), state.get("edits") or [], state.get("findings") or []),
        notices=build_notices(state.get("unresolved") or [], state.get("questions") or [], plan, has_audio, location_tags, removed_tags),
        citations=collect_citations(plan, requirements, state.get("analyses") or []),
    )


def to_html(title: str, report: dict[str, Any], plan: dict[str, Any], requirements: list[dict[str, Any]]) -> str:
    """A self-contained HTML rendering of the report for reading and printing."""
    e = html.escape

    def table(headers: list[str], rows: list[list[str]]) -> str:
        head = "".join(f"<th>{e(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in row) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    def items(values: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{e(v)}</li>" for v in values) + "</ul>" if values else "<p>없음</p>"

    verdict = {"pass": "통과", "fail": "실패", "info": "참고"}
    authority = {1: "규범", 2: "해석", 3: "참고"}
    sections = [
        f"<h1>{e(title)} — 비식별화 보고서</h1>",
        f"<p>완료 {e(report['finishedAt'])} · 처리 시간 {report['elapsedSec']}초 · 비용 ${report['costUsd']:.3f} · 출력 {e(report['output']['filename'])} ({e(report['output']['codec'])})</p>",
        "<h2>초 단위 스크립트</h2>",
        table(["구간", "장면", "대상", "처리", "근거"], [[f"{timecode(r['start'])}–{timecode(r['end'])}", r["scene"], r["target"], r["treatment"], r["basis"]] for r in report["script"]]),
        "<h2>보호 요건</h2>",
        table(["ID", "요건", "대상", "근거"], [[r["id"], r["statement"], ", ".join(r["targets"]), "; ".join(c["label"] for c in r["citations"])] for r in requirements]),
        "<h2>결정</h2>",
        table(["ID", "대상", "처리", "충족 요건", "이유"], [[d["id"], d["targetLabel"], METHOD_LABEL[d["method"]], ", ".join(d["satisfies"]), d["rationale"]] for d in plan["decisions"]]),
        "<h2>출력 검증</h2>",
        table(["검사", "원본", "출력", "결과", "비고"], [[v["check"], v["original"], v["output"], verdict[v["verdict"]], v.get("note") or ""] for v in report["verification"]]),
        "<h2>질문 · 수정 · 감사 기록</h2>",
        items(report["history"]),
        "<h2>고지</h2>",
        items(report["notices"]),
        "<h2>인용</h2>",
        table(["등급", "종류", "출처"], [[authority[c["authority"]], c["kind"], c["label"]] for c in report["citations"]]),
    ]
    style = "body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:960px;margin:32px auto;padding:0 16px;line-height:1.6;color:#1c2126}table{border-collapse:collapse;width:100%;margin:8px 0 24px;font-size:14px}th,td{border:1px solid #d5dade;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f1f4f6}h2{margin-top:28px;font-size:18px}"
    return f"<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>{e(title)} 보고서</title><style>{style}</style></head><body>{''.join(sections)}</body></html>"
