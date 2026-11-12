"""Chat models for the API parts of a profile (design §10.4, R4).

Two routes, chosen by the candidate's adapter:

- `openrouter`: any vendor's model by slug. Provider routing asks for endpoints that honour every request
  parameter (strict JSON schema, tools) and that do not keep prompts for training; PRIVFRAME_OPENROUTER_ZDR=1
  narrows routing to zero-data-retention endpoints.
- `anthropic`: Claude models by id, called on the Anthropic API directly (ANTHROPIC_API_KEY). Used by the
  test profile so development runs draw on Anthropic credits instead of the OpenRouter balance.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from .pipeline import Part
from .settings import CoreSettings, secret

# Claude answers that end with stop_reason "refusal" are re-run on another model by the API itself when the
# request opts in. Only these model families take the parameter (Haiku has no server-side fallback).
REFUSAL_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
CLAUDE_MAX_TOKENS = 16000  # thinking counts against the limit on current Claude models; a low cap cuts answers short
CACHE_READ_RATE, CACHE_WRITE_RATE = 0.1, 1.25  # of the input price (Anthropic's prompt-cache billing)


class MissingCredential(RuntimeError):
    pass


def provider_preferences(settings: CoreSettings) -> dict[str, object]:
    preferences: dict[str, object] = {"require_parameters": True, "data_collection": "deny"}
    if settings.openrouter_zdr:
        preferences["zdr"] = True
    return preferences


def anthropic_key(settings: CoreSettings) -> str:
    key = secret(settings.anthropic_api_key)
    if not key:
        raise MissingCredential("ANTHROPIC_API_KEY가 없습니다. 저장소 루트의 .env에 넣으세요.")
    return key


def refusal_fallback(model: str) -> dict[str, Any]:
    """Request fields that let the API continue a refused request on another model, or {} when the model
    does not support it. They belong on the beta Messages endpoint."""
    if model.startswith(REFUSAL_FALLBACK_MODELS):
        return {"betas": [REFUSAL_FALLBACK_BETA], "fallbacks": "default"}
    return {}


def chat_model(part: Part, settings: CoreSettings, *, temperature: float | None = None, max_tokens: int | None = None, timeout_s: float = 120, effort: str | None = None) -> BaseChatModel:
    """The chat model of an API part.

    `temperature` is sent only when given and only to OpenRouter: OpenAI GPT-5.x and Claude 5.5 endpoints do
    not accept it, and with require_parameters an unsupported parameter leaves no endpoint to route to (M3
    smoke). `effort` (low · medium · high …) sets how much a Claude model thinks; other routes ignore it.
    """
    candidate = part.candidate
    if not candidate.ref:
        raise ValueError(f"'{part.slot.label}'의 후보 '{candidate.label}'에 모델 이름(ref)이 없습니다.")
    if candidate.adapter == "anthropic":
        from langchain_anthropic import ChatAnthropic

        extras = refusal_fallback(candidate.ref)
        return ChatAnthropic(
            model=candidate.ref,
            api_key=anthropic_key(settings),
            max_tokens=max(max_tokens or 0, CLAUDE_MAX_TOKENS),
            timeout=timeout_s,
            max_retries=2,
            betas=extras.get("betas"),
            model_kwargs={"fallbacks": extras["fallbacks"]} if extras else {},
            output_config={"effort": effort} if effort else None,
        )
    if candidate.adapter == "openrouter":
        from langchain_openrouter import ChatOpenRouter

        key = secret(settings.openrouter_api_key)
        if not key:
            raise MissingCredential("OPENROUTER_API_KEY가 없습니다. 저장소 루트의 .env에 넣으세요.")
        return ChatOpenRouter(
            model=candidate.ref,
            api_key=key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=int(timeout_s * 1000),  # langchain-openrouter takes milliseconds; seconds here would time out every request
            openrouter_provider=provider_preferences(settings),
            app_title="PrivFrame",
        )
    raise ValueError(f"'{part.slot.label}'의 후보 '{candidate.label}'({candidate.adapter})은(는) 대화 모델로 부를 수 없습니다.")


def usage(message: BaseMessage) -> tuple[int, int, int, int]:
    """(input, output, cache-read, cache-write tokens) of a model reply. Input counts every prompt token,
    cached or not. The Anthropic integration reports cache writes under their lifetime, so those are summed."""
    data = getattr(message, "usage_metadata", None) or {}
    details = data.get("input_token_details") or {}
    written = sum(int(details.get(key) or 0) for key in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return int(data.get("input_tokens") or 0), int(data.get("output_tokens") or 0), int(details.get("cache_read") or 0), written


def cost_usd(part: Part, tokens_in: int, tokens_out: int, cached_in: int = 0, cache_write: int = 0) -> float:
    """Cost at the catalog price: cache reads at 10% of the input price, cache writes at 125% (the same rule
    as the Lab's mock engine). A route that reports the billed amount (OpenRouter) does not need this."""
    price_in, price_out = part.candidate.price or (0.0, 0.0)
    plain = max(0, tokens_in - cached_in - cache_write)
    return (plain * price_in + cached_in * price_in * CACHE_READ_RATE + cache_write * price_in * CACHE_WRITE_RATE + tokens_out * price_out) / 1_000_000
