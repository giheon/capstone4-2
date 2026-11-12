"""Facts the uploader stated in the optional situation memo (design §5.3).

The memo is never used as a search query: a model turns it into facts marked `declared`, which the user can
see and correct on the review screen.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import knowledge
from ..domain import Fact
from ..runtime import Services, call_json

PROMPT = """사용자가 영상을 올리면서 적은 메모에서, 개인정보 보호 판단에 쓰일 사실만 뽑으세요.
- 메모에 분명히 적힌 것만 뽑고 추측하지 않습니다. 해당하는 내용이 없으면 빈 목록을 답합니다.
- key는 소문자 점 표기입니다. 아래 참고 어휘에 맞는 것이 있으면 그 키와 값을 글자 그대로 쓰고(예: yes, no, public), 없으면 같은 방식으로 새 키와 짧은 영문 소문자 값을 만듭니다.
- 메모가 말한 것보다 넓게 읽지 않습니다. 예를 들어 "직원에게 말했다"는 촬영 사실을 표시했다는 뜻이 아닙니다.
- 특정 인물에 관한 사실인데 화면의 누구인지 알 수 없으면 key에 사람 번호를 넣지 말고 subject.<속성> 으로 적습니다.

참고 어휘
{vocabulary}

메모
\"\"\"{memo}\"\"\""""


class MemoFact(BaseModel):
    key: str = Field(description="정규화된 사실 키")
    label_ko: str = Field(description="사실의 짧은 이름")
    value: str = Field(description="값 (참고 어휘의 값 또는 짧은 영문 소문자)")
    value_label_ko: str = Field(description="사용자에게 보일 값 설명")
    confidence: float


class MemoFacts(BaseModel):
    facts: list[MemoFact]


def extract_memo_facts(services: Services, memo: str, *, node: str = "ingest") -> list[Fact]:
    memo = memo.strip()
    if not memo:
        return []
    prompt = PROMPT.format(vocabulary=knowledge.fact_vocabulary(), memo=memo[:2000])
    out = call_json(services, "llm.planner", MemoFacts, [{"role": "user", "content": prompt}], node=node, agent="ingest", payload={"memo": memo}, max_tokens=1200)
    seen: dict[str, Fact] = {}
    for item in out.facts:
        key = item.key.strip().lower()
        if key and key not in seen:
            seen[key] = Fact(key=key, label=item.label_ko, value=item.value, value_label=item.value_label_ko, provenance="declared", confidence=max(0.0, min(1.0, item.confidence)), source="상황 메모", editable=True)
    return list(seen.values())

