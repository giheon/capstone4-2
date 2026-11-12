"""The topology the Lab draws for the case graph, and how graph transitions map to its edges.

The curated layout was authored with the screens (frontend/mock/graph.ts) and is kept as data
(`data/graph.json`). Node ids are the ones the event normalizer produces from the compiled LangGraph, so the
drawing follows the real run. Two things differ from the M2 mock: the review → re-analysis edge exists as a
real transition, and the descriptions say what M4 actually does in the steps that M5 completes.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data"
REEVALUATE = "await_review->legal_analyst/model"
REPAIR = "verify_output->render_original"

# (from node, to node, edge id in the drawing) for transitions between top-level graph nodes
_TOP = [
    ("__start__", "ingest", "__start__->ingest"),
    ("ingest", "perceive", "ingest->perceive"),
    ("perceive", "await_identity", "perceive->await_identity"),
    ("perceive", "legal_analyst", "perceive->legal_analyst/model"),
    ("await_identity", "legal_analyst", "await_identity->legal_analyst/model"),
    ("await_review", "legal_analyst", REEVALUATE),
    ("legal_analyst", "plan", "legal_analyst/validate->plan"),
    ("plan", "review_guide", "plan->review_guide"),
    ("review_guide", "await_review", "review_guide->await_review"),
    ("await_audit_confirm", "await_review", "await_audit_confirm->await_review"),
    ("await_review", "auditor", "await_review->auditor/model"),
    ("auditor", "await_audit_confirm", "auditor/model->await_audit_confirm"),
    ("auditor", "render_original", "auditor->render_original"),
    ("await_audit_confirm", "render_original", "await_audit_confirm->render_original"),
    ("render_original", "verify_output", "render_original->verify_output"),
    ("verify_output", "report", "verify_output->report"),
    ("verify_output", "render_original", REPAIR),
]
_PERCEIVE = [
    ("ingest_probe", "make_proxy"),
    ("make_proxy", "detect_track"),
    ("detect_track", "group_identities"),
    ("detect_track", "ocr"),
    ("detect_track", "describe_segments"),
    ("group_identities", "describe_persons"),
    ("describe_persons", "assemble"),
    ("ocr", "assemble"),
    ("describe_segments", "assemble"),
]
JOINS = {"perceive/assemble"}

M4_NOTES = {
    "review_guide": "결정마다 근거와 수정 시 위험을 요건에서 조립한다. 실제 유사 사례 검색과 모델이 쓰는 안내는 M5(FR-08)에서 더한다.",
    "auditor": "M4: 검토에서 바뀐 계획이 모든 요건을 여전히 충족하는지 결정적으로 대조한다. 독립 모델이 도구로 재검증하는 감사(FR-09)는 M5에서 더한다.",
    "report": "표와 인용을 데이터에서 조립한다(JSON · HTML). 모델이 쓰는 서술은 M5에서 더한다.",
    "plan": "요건을 결정(대상 · 범위 · 모자이크 또는 유지)으로 바꾸고, 모든 요건이 충족되는지 결정적으로 검사한다. 구간은 요건과 추적 결과에서 계산한다.",
    "render_original": "원본을 디코드해 결정 구간의 프레임에 모자이크를 입히고, PTS · time_base를 유지해 재인코딩한다. 오디오 · 자막 · 메타는 바이트 복사(삭제하기로 한 위치 태그만 제외).",
    "verify_output": "프레임 수 · PTS · 오디오 framemd5 · 메타 diff · 영역 밖 PSNR/SSIM을 확인하고, 가려야 할 모든 프레임에 모자이크가 실제로 들어갔는지, 가린 대상이 어느 프레임에서든 알아볼 수 있게 남았는지 본다. 남았으면 그 자리를 더 가려 다시 렌더한다.",
}
M4_SUBLABELS = {"auditor": "요건 대조 (모델 감사는 M5)", "verify_output": "전체 프레임 · 실패 시 미완료"}


def transitions() -> dict[str, list[tuple[str, str]]]:
    table: dict[str, list[tuple[str, str]]] = {}
    for source, target, edge in _TOP:
        table.setdefault(target, []).append((source, edge))
    for source, target in _PERCEIVE:
        table.setdefault(f"perceive/{target}", []).append((f"perceive/{source}", f"perceive/{source}->perceive/{target}"))
    return table


@lru_cache(maxsize=1)
def graph_spec() -> dict[str, Any]:
    spec = json.loads((DATA / "graph.json").read_text(encoding="utf-8"))
    spec["version"] = "case@1.0 (M4 · LangGraph)"
    for node in spec["nodes"]:
        if node["id"] in M4_NOTES:
            node["description"] = M4_NOTES[node["id"]]
        if node["id"] in M4_SUBLABELS:
            node["sublabel"] = M4_SUBLABELS[node["id"]]
    # up from the review node, along a corridor between the two rows (below the row-wrap corridor at 540), into the model's underside
    spec["edges"].append({"id": REEVALUATE, "view": "main", "source": "await_review", "target": "legal_analyst/model", "kind": "retry", "sourceHandle": "t", "targetHandle": "b", "corridorY": 566, "label": "사실 수정 · 재평가"})
    # back under the two nodes when the output check finds something still showing
    spec["edges"].append({"id": REPAIR, "view": "main", "source": "verify_output", "target": "render_original", "kind": "retry", "sourceHandle": "b", "targetHandle": "b", "corridorY": 778, "label": "노출 발견 · 더 가림"})
    return spec


def datasets() -> dict[str, Any]:
    """Datasets and evaluators as planned. Measured values come from experiments, which run in M5."""
    data = json.loads((DATA / "datasets.json").read_text(encoding="utf-8"))
    evaluators = [{k: v for k, v in e.items() if k != "lastValue"} for e in data["evaluators"]]
    return {"datasets": data["datasets"], "evaluators": evaluators}


def node_labels() -> dict[str, str]:
    """The name the Lab shows for each graph node."""
    return {node["id"]: node["label"] for node in graph_spec()["nodes"]}
