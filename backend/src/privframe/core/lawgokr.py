"""국가법령정보 Open API client (law.go.kr DRF; research R6).

Calls need the registered OC value *and* a registered server IP/domain. An unregistered host still gets HTTP 200,
with a '사용자 정보 검증에 실패' body, which this client raises as LawApiAuthError. The OC is a credential: it
never goes into saved files — `public_url` gives the same request without it, for provenance.
"""
import re
from typing import Any

import httpx

BASE = "https://www.law.go.kr/DRF"


class LawApiError(RuntimeError):
    pass


class LawApiAuthError(LawApiError):
    pass


def jo_code(article: str) -> str:
    """'제25조의2' → '002502' (JO = 4-digit article number + 2-digit branch number)."""
    match = re.fullmatch(r"\s*제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*", article)
    if not match:
        raise ValueError(f"조문 표기를 읽을 수 없습니다: {article!r}")
    return f"{int(match.group(1)):04d}{int(match.group(2) or 0):02d}"


class LawGoKr:
    def __init__(self, oc: str, client: httpx.Client | None = None, timeout: float = 20):
        self._oc = oc
        self._client = client or httpx.Client(timeout=timeout, headers={"User-Agent": "PrivFrame capstone research"})

    def _call(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._client.get(f"{BASE}/{endpoint}", params={"OC": self._oc, "type": "JSON", **params})
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as error:
            raise LawApiError(f"JSON이 아닌 응답입니다 ({response.headers.get('content-type')}): {response.text[:200]}") from error
        if isinstance(data, dict) and set(data) <= {"result", "msg"} and "result" in data:
            message = f"{data.get('result')} {data.get('msg', '')}".strip()
            if "검증" in str(data.get("result")):
                raise LawApiAuthError(f"{message} — open.law.go.kr에서 OC와 이 컴퓨터의 공인 IP를 등록했는지 확인하세요.")
            raise LawApiError(message)
        return data

    def search(self, target: str, query: str | None = None, **params: Any) -> dict[str, Any]:
        """lawSearch.do: target = law · prec · detc · expc · admrul · ppc …"""
        return self._call("lawSearch.do", {"target": target, **({"query": query} if query else {}), **params})

    def service(self, target: str, **params: Any) -> dict[str, Any]:
        """lawService.do: one document (law · lawjosub · prec · detc · expc · admrul · ppc) by ID/MST."""
        return self._call("lawService.do", {"target": target, **params})

    @staticmethod
    def public_url(endpoint: str, params: dict[str, Any]) -> str:
        return str(httpx.URL(f"{BASE}/{endpoint}", params={k: v for k, v in params.items() if k != "OC"}))

    @staticmethod
    def items(result: dict[str, Any]) -> list[dict[str, Any]]:
        """The hit list of a lawSearch response, whatever its wrapper key (LawSearch.law, PrecSearch.prec …)."""
        for wrapper in result.values():
            if isinstance(wrapper, dict):
                for value in wrapper.values():
                    if isinstance(value, list):
                        return [v for v in value if isinstance(v, dict)]
                    if isinstance(value, dict) and any(k.endswith(("ID", "일련번호")) for k in value):
                        return [value]  # a single hit comes back as an object
        return []

    @classmethod
    def count(cls, result: dict[str, Any]) -> int:
        for wrapper in result.values():
            if isinstance(wrapper, dict) and str(wrapper.get("totalCnt", "")).isdigit():
                return int(wrapper["totalCnt"])
        return len(cls.items(result))
