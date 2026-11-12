"""Fills a new catalog candidate from OpenRouter's public model list: name, price, inputs, tool support.

Python port of frontend/mock/openrouter.ts. The list needs no API key and the request carries nothing that
identifies the user or the case, only a generic User-Agent.
"""
import math
import re
import threading
import time
from typing import Any

import httpx

from .pipeline_store import PipelineError

MODELS_URL = "https://openrouter.ai/api/v1/models"
TTL_SECONDS = 10 * 60
TIMEOUT = httpx.Timeout(15, connect=5)
HEADERS = {"Accept": "application/json", "User-Agent": "PrivFrame"}

_cache: tuple[float, list[dict]] | None = None
_lock = threading.Lock()


def _models() -> list[dict]:
    global _cache
    with _lock:  # one download at a time; later callers within ten minutes reuse it
        if _cache and time.monotonic() - _cache[0] < TTL_SECONDS:
            return _cache[1]
        try:
            response = httpx.get(MODELS_URL, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        except httpx.HTTPError as error:
            message = "OpenRouter에 연결할 수 없습니다. 네트워크를 확인하거나 값을 직접 입력하세요."
            raise PipelineError(502, message) from error
        if not response.is_success:
            raise PipelineError(502, f"OpenRouter 모델 목록을 가져오지 못했습니다 ({response.status_code}).")
        try:
            data = response.json().get("data") or []
        except (ValueError, AttributeError) as error:
            message = f"OpenRouter 모델 목록을 가져오지 못했습니다 ({response.status_code})."
            raise PipelineError(502, message) from error
        _cache = (time.monotonic(), [m for m in data if isinstance(m, dict)])
        return _cache[1]


def _per_million(value: Any) -> float | int | None:
    """USD per token (a decimal string) as USD per 1M tokens, rounded to 3 places like the Lab does."""
    try:
        scaled = float(0 if value is None else value) * 1_000_000 * 1000
    except (TypeError, ValueError):
        return None
    if not math.isfinite(scaled):
        return None
    whole = math.floor(scaled)
    price = (whole + 1 if scaled - whole >= 0.5 else whole) / 1000  # Math.round: halves go up
    return int(price) if price.is_integer() else price


def lookup_openrouter(ref: str) -> dict:
    """CandidateLookup (contract.ts) for an OpenRouter slug such as `anthropic/claude-sonnet-5.5`."""
    slug = (ref or "").strip()
    if "/" not in slug:
        raise PipelineError(400, "OpenRouter slug는 '회사/모델' 형식입니다 (예: anthropic/claude-sonnet-5.5).")
    models = _models()
    model = next((m for m in models if m.get("id") == slug), None)
    model = model or next((m for m in models if m.get("canonical_slug") == slug), None)
    if model is None:
        raise PipelineError(404, f"OpenRouter 공개 목록에 '{slug}'이(가) 없습니다.")
    supported = model.get("supported_parameters") or []
    inputs = (model.get("architecture") or {}).get("input_modalities") or []
    caps = [cap for cap, present in (
        ("tools", "tools" in supported), ("json", "structured_outputs" in supported),
        ("image", "image" in inputs), ("video", "video" in inputs),
    ) if present]
    pricing = model.get("pricing") or {}
    price = {"in": _per_million(pricing.get("prompt")), "out": _per_million(pricing.get("completion"))}
    name = model.get("name")
    found = {
        "ref": model["id"],
        "label": re.sub(r"^[^:]+:\s*", "", model["id"] if name is None else name),
        "price": price,
        "caps": caps,
        # a starting guess from price; the person adding the model can change it
        "tier": "frontier" if (price["in"] or 0) >= 1.5 else "efficient",
    }
    if model.get("context_length") is not None:
        found["contextLength"] = model["context_length"]
    return found
