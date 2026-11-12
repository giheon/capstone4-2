from conftest import PIPELINE
import pytest
from langchain_core.messages import AIMessage

from privframe.core import llm
from privframe.core.llm import MissingCredential, chat_model, cost_usd, provider_preferences, refusal_fallback, usage
from privframe.core.pipeline import load_catalog, load_profile, resolve
from privframe.core.settings import CoreSettings



def part(profile_id: str, slot: str):
    return resolve(load_catalog(PIPELINE), load_profile(PIPELINE, profile_id)).part(slot)


def test_openrouter_model_takes_the_timeout_in_milliseconds():
    # a seconds value passed straight through made every request time out and retry for minutes (M3 smoke)
    settings = CoreSettings(openrouter_api_key="sk-or-test")
    model = chat_model(part("baseline-v2", "llm.analyst"), settings, timeout_s=90)
    assert model.request_timeout == 90_000
    assert model.model_name == "anthropic/claude-sonnet-5.5"
    assert model.openrouter_provider == {"require_parameters": True, "data_collection": "deny"}
    # reasoning models on OpenRouter reject temperature; sending it by default left no endpoint to route to
    assert model.temperature is None
    # `effort` is for the Anthropic route; here it must not turn into a parameter no endpoint accepts
    assert chat_model(part("baseline-v2", "llm.analyst"), settings, effort="low").openrouter_provider == model.openrouter_provider


def test_zero_data_retention_is_opt_in():
    assert provider_preferences(CoreSettings(openrouter_api_key="k", openrouter_zdr=True))["zdr"] is True


def test_claude_candidates_of_the_anthropic_adapter_are_called_on_the_anthropic_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = CoreSettings(_env_file=None, anthropic_api_key="sk-ant-test", openrouter_api_key=None)
    analyst = chat_model(part("claude-api", "llm.analyst"), settings, timeout_s=90, max_tokens=4000, effort="low", temperature=0.2)
    assert type(analyst).__name__ == "ChatAnthropic" and analyst.model == "claude-sonnet-5-5"  # the model id, not an OpenRouter slug
    assert analyst.default_request_timeout == 90.0  # seconds on this route
    assert analyst.max_tokens == llm.CLAUDE_MAX_TOKENS  # thinking counts against the limit: a small cap is raised
    assert analyst.output_config == {"effort": "low"} and analyst.temperature is None
    assert chat_model(part("claude-api", "llm.analyst"), settings).output_config is None  # effort only when asked for
    assert chat_model(part("claude-api", "llm.analyst"), settings, max_tokens=32000).max_tokens == 32000
    # a refused request continues on another model where the API can do that (not Haiku)
    assert (analyst.betas, analyst.model_kwargs) == ([llm.REFUSAL_FALLBACK_BETA], {"fallbacks": "default"})
    report = chat_model(part("claude-api", "llm.report"), settings)
    assert (report.model, report.betas, report.model_kwargs) == ("claude-haiku-5-5", None, {})
    assert refusal_fallback("claude-opus-5-5") == {"betas": [llm.REFUSAL_FALLBACK_BETA], "fallbacks": "default"} and refusal_fallback("claude-haiku-5-5") == {}

    with pytest.raises(MissingCredential, match="ANTHROPIC_API_KEY"):
        chat_model(part("claude-api", "llm.analyst"), CoreSettings(_env_file=None, openrouter_api_key="sk-or-test"))
    with pytest.raises(MissingCredential, match="OPENROUTER_API_KEY"):
        chat_model(part("baseline-v2", "llm.analyst"), settings)  # the other route's key is of no use
    with pytest.raises(ValueError, match="대화 모델로 부를 수 없습니다"):
        chat_model(part("baseline-v2", "perception.face_detector"), settings)


def test_usage_counts_cached_and_cache_writing_tokens_of_a_reply():
    details = {"cache_read": 600, "cache_creation": 0, "ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 50}
    reply = AIMessage(content="", usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200, "input_token_details": details})
    assert usage(reply) == (1000, 200, 600, 150)  # input counts every prompt token; writes are summed over their lifetimes
    plain = AIMessage(content="", usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})
    assert usage(plain) == (12, 3, 0, 0) and usage(AIMessage(content="")) == (0, 0, 0, 0)


def test_cost_follows_the_catalog_price_with_cache_reads_cheaper_and_cache_writes_dearer():
    analyst = part("claude-api", "llm.analyst")
    assert analyst.candidate.price == (2.0, 10.0)  # USD per 1M tokens in the catalog
    assert cost_usd(analyst, 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert cost_usd(analyst, 1_000_000, 0, cached_in=1_000_000) == pytest.approx(0.2)  # a cache read: 10% of the input price
    assert cost_usd(analyst, 1_000_000, 0, cache_write=1_000_000) == pytest.approx(2.5)  # a cache write: 125%
    assert cost_usd(analyst, 1000, 200, 600, 150) == pytest.approx((250 * 2 + 600 * 0.2 + 150 * 2.5 + 200 * 10) / 1e6)
    assert cost_usd(part("baseline-v2", "perception.face_detector"), 1000, 1000) == 0.0  # a local part costs nothing
