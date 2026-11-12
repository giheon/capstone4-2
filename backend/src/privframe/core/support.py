"""What this backend can run today, per catalog part. The catalog lists candidates the design considers;
some have no adapter yet, and some parts belong to steps a later milestone completes. The Lab shows these
notes next to a profile so that choosing a candidate never promises more than a run will do."""
from __future__ import annotations

from typing import Any

from .pipeline import ResolvedProfile
from .retrieval import support as retrieval_support

LATER = {
    "llm.review_guide": "M4에서는 이 모델을 호출하지 않습니다. 검토 안내는 요건에서 조립하고, 모델이 쓰는 안내와 유사 사례는 M5에서 더합니다.",
    "llm.auditor": "M4의 감사는 요건 대조(결정적)만 하며 이 모델을 호출하지 않습니다. 독립 모델 감사는 M5에서 더합니다.",
    "llm.report": "M4의 보고서는 데이터에서 조립하며 이 모델을 호출하지 않습니다. 서술은 M5에서 더합니다.",
}


def backend_issues(profile: ResolvedProfile) -> list[dict[str, Any]]:
    from .perception.common import RUNNABLE

    issues: list[dict[str, Any]] = []

    def add(level: str, slot: str, text: str) -> None:
        issues.append({"level": level, "slot": slot, "text": text})

    for slot_id, part in profile.parts.items():
        candidate = part.candidate
        if slot_id in RUNNABLE and not RUNNABLE[slot_id](candidate):
            add("error", slot_id, f"'{candidate.label}'을(를) 실행하는 어댑터가 아직 없습니다. 이 프로필로 실행하면 영상 분석에서 멈춥니다.")
        elif slot_id.startswith("rag."):
            ok, reason = retrieval_support(slot_id, candidate)
            if not ok:
                add("error", slot_id, f"'{candidate.label}': {reason}. 이 프로필로 실행하면 법률 검색에서 멈춥니다.")
        elif slot_id in ("vlm.scene", "vlm.person", "vlm.inspect") and candidate.adapter not in ("openrouter", "anthropic"):
            add("warn", slot_id, f"'{candidate.label}'({candidate.adapter}) 어댑터가 아직 없어 이 단계 없이 진행합니다(장면 설명 · 인물 속성 · 시각 재점검이 빠짐).")
        elif slot_id == "perception.segmenter" and candidate.adapter != "none":
            add("info", slot_id, "윤곽 마스크는 M5에서 연결합니다. 지금은 윤곽 결정도 타원 · 사각형으로 그립니다.")
        elif slot_id == "agent.strategy" and candidate.id != "proposed":
            add("warn", slot_id, f"'{candidate.label}' 전략은 M5에서 구현합니다. 지금은 제안 구조(case 그래프)로 실행합니다.")
        elif slot_id in LATER and candidate.adapter != "none":
            add("info", slot_id, LATER[slot_id])
    return issues


KEY_OF_ADAPTER = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def missing_keys(profile: ResolvedProfile, present: dict[str, bool]) -> list[dict[str, Any]]:
    """Parts whose API key is not configured (`present`: CoreSettings.present()). Such a run stops where the
    part is first called."""
    issues = []
    for slot_id, part in profile.parts.items():
        key = KEY_OF_ADAPTER.get(part.candidate.adapter)
        if key and not present.get(key) and slot_id not in LATER:  # the parts in LATER are not called yet
            issues.append({"level": "error", "slot": slot_id, "text": f"'{part.candidate.label}'은(는) {key}가 필요한데 .env에 없습니다."})
    return issues
