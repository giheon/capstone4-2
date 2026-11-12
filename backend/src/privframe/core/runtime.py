"""What a running case needs besides its state: the profile's parts, model clients, the corpus index, and the
custom stream events nodes use to talk to the run wrapper (design §4.1: settings stay out of graph state).

The graph gets a `CaseContext` as its LangGraph runtime context. Nothing here is checkpointed.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from .corpus import CorpusStore
from .llm import chat_model, cost_usd, usage
from .pipeline import Part, ResolvedProfile
from .retrieval import Index, RetrievalSpec, build_index
from .settings import CoreSettings, secret

T = TypeVar("T", bound=BaseModel)

FIXTURE = "fixture"  # adapter id of scripted stand-ins (FR-13: the whole graph runs without keys or model downloads)


_INDEXES: dict[tuple[str, RetrievalSpec], Index] = {}
_SEARCH_LOCK = threading.Lock()


class BudgetExceeded(RuntimeError):
    """The case reached its cost cap; the run pauses and can be resumed with a higher cap."""


class ModelCallError(RuntimeError):
    pass


# ---------- custom stream events ----------


def emit(kind: str, **body: Any) -> None:
    """Send a custom event to the run wrapper. A no-op outside a graph run (unit tests call nodes directly)."""
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()({"kind": kind, **body})
    except Exception:  # noqa: BLE001 - no stream writer in this context
        pass


def activity(text: str, tone: str = "info") -> None:
    """A sentence for the uploader's progress feed."""
    emit("activity", text=text, tone=tone)


def patch(**fields: Any) -> None:
    """Update the case snapshot the service screens read (CaseDetail fields, already in wire shape)."""
    emit("detail", patch=fields)


def note(channel: str, writer: str, summary: str) -> None:
    """A one-line summary of a state channel write, for the Lab's state view."""
    emit("state", channel=channel, writer=writer, summary=summary)


def wire(items: list[Any]) -> list[dict[str, Any]]:
    return [item.wire() for item in items]


# ---------- services ----------


class CostMeter:
    def __init__(self, cap_usd: float, spent: float = 0.0):
        self.cap_usd = cap_usd
        self._spent = spent
        self._lock = threading.Lock()

    def add(self, usd: float) -> float:
        with self._lock:
            self._spent += usd
            return self._spent

    @property
    def spent(self) -> float:
        return self._spent

    @property
    def exceeded(self) -> bool:
        return self.cap_usd > 0 and self._spent > self.cap_usd


@dataclass
class Services:
    settings: CoreSettings
    profile: ResolvedProfile
    cost: CostMeter
    corpus_dir: Path | None = None  # defaults to <data>/corpus
    fixture: Any = None  # core.fixture.Fixture when a part uses the fixture adapter
    tool_results: dict[str, Any] = field(default_factory=dict, repr=False)  # agent tool results by call id (see AnalystControl)
    worked_s: float = 0.0  # time the case has spent running, without the time it waited for the user
    segment_started: float | None = None  # monotonic start of the stretch running now
    _index: Index | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def part(self, slot: str) -> Part:
        return self.profile.part(slot)

    def param(self, name: str, default: float = 0) -> float:
        return self.profile.params.get(name, default)

    def is_fixture(self, slot: str) -> bool:
        return self.part(slot).candidate.adapter == FIXTURE

    @property
    def store(self) -> CorpusStore:
        return CorpusStore(self.corpus_dir or (self.settings.data_dir / "corpus"))

    def index(self) -> Index:
        """The corpus index for this profile's rag.* parts. Built on first use; an index with the same
        fingerprint is shared by every case in the process, so its models load once."""
        with self._lock:
            if self._index is None:
                embedder = self.fixture.embedder() if self.fixture is not None and self.is_fixture("rag.embedding") else None
                spec = RetrievalSpec.from_profile(self.profile)
                with _SEARCH_LOCK:
                    built = build_index(self.store, spec, device=self.settings.torch_device, embedder=embedder)
                    self._index = _INDEXES.setdefault((str(built.directory), spec), built)
            return self._index

    def search(self, query: str, *, k: int | None = None, kinds: list[str] | None = None) -> list[Any]:
        index = self.index()
        with _SEARCH_LOCK:  # the embedding and reranking models are shared between cases
            return index.search(query, k=k, kinds=kinds)

    def chat(self, slot: str, **options: Any) -> BaseChatModel:
        if self.is_fixture(slot):
            return self.fixture.chat(slot)
        return chat_model(self.part(slot), self.settings, **options)

    def charge(self, node: str, agent: str, slot: str, tokens_in: int, tokens_out: int, cached: int = 0, ms: float = 0, usd: float | None = None, cache_write: int = 0) -> float:
        """Record one model call: add its cost to the case total and tell the Lab. `usd` is the amount the
        route billed when it reports one; otherwise the cost is worked out from the catalog price."""
        part = self.part(slot)
        cost = usd if usd is not None else cost_usd(part, tokens_in, tokens_out, cached, cache_write)
        self.cost.add(cost)
        emit("llm", node=node, agent=agent, model=part.candidate.label, tokensIn=tokens_in, cachedIn=cached, tokensOut=tokens_out, costUsd=cost, ms=round(ms))
        return cost

    def elapsed(self) -> float:
        """Processing time so far in seconds (waiting for answers and reviews is not processing)."""
        return self.worked_s + (time.monotonic() - self.segment_started if self.segment_started is not None else 0.0)

    def check_budget(self) -> None:
        if self.cost.exceeded:
            raise BudgetExceeded(f"비용이 상한 ${self.cost.cap_usd:.2f}을 넘었습니다 (지금까지 ${self.cost.spent:.3f}).")


@dataclass
class CaseContext:
    """LangGraph runtime context of one case run."""

    case_id: str
    workdir: Path
    services: Services
    artifacts: Any = None  # core.artifacts.ArtifactStore: results of steps other runs already computed

    @property
    def profile(self) -> ResolvedProfile:
        return self.services.profile


def call_json(
    services: Services,
    slot: str,
    schema: type[T],
    messages: list[Any],
    *,
    node: str,
    agent: str,
    payload: dict[str, Any] | None = None,
    max_tokens: int = 4000,
) -> T:
    """One structured model call for a non-agent step (planner, memo facts). The schema must have every field
    required: strict JSON-schema endpoints reject optional properties."""
    services.check_budget()
    started = time.perf_counter()
    if services.is_fixture(slot):
        result = services.fixture.json(slot, schema, payload or {})
        services.charge(node, agent, slot, 0, 0, ms=(time.perf_counter() - started) * 1000)
        return result
    part = services.part(slot)
    model = services.chat(slot, max_tokens=max_tokens)
    try:
        if part.candidate.adapter == "anthropic":
            # Claude's own structured output (output_config.format); forcing a tool call is rejected by current models
            reply = model.with_structured_output(schema, method="json_schema", include_raw=True).invoke(messages)
            raw, parsed = reply["raw"], reply.get("parsed")
        else:
            response_format = {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}}
            raw, parsed = model.bind(response_format=response_format).invoke(messages), None
    except Exception as error:  # noqa: BLE001 - provider errors differ per endpoint
        raise ModelCallError(f"{part.candidate.label} 호출 실패: {error}") from error
    tokens_in, tokens_out, cached, written = usage(raw)
    billed = raw.response_metadata.get("cost")  # OpenRouter reports what the call actually cost
    services.charge(node, agent, slot, tokens_in, tokens_out, cached, (time.perf_counter() - started) * 1000, usd=float(billed) if isinstance(billed, (int, float)) else None, cache_write=written)
    if raw.response_metadata.get("stop_reason") == "refusal":
        raise ModelCallError(f"{part.candidate.label}이(가) 응답을 거절했습니다.")
    if isinstance(parsed, schema):
        return parsed
    try:
        return schema.model_validate(json.loads(_json_block(raw.text)))
    except Exception as error:  # noqa: BLE001
        raise ModelCallError(f"{part.candidate.label}의 응답을 구조화 결과로 읽지 못했습니다: {error}") from error


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema in the form strict endpoints accept: every object closed
    (`additionalProperties: false`) with all of its properties required. Pydantic alone does not emit this,
    and OpenAI rejects the request without it (seen in M4: "'additionalProperties' is required to be supplied")."""

    def close(node: Any) -> Any:
        if isinstance(node, dict):
            if "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)
        return node

    return close(schema.model_json_schema())


def _json_block(text: str) -> str:
    """The JSON object inside a reply that may wrap it in a code fence or prose."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


def tracer_callbacks(settings: CoreSettings) -> list[Any]:
    """LangSmith tracing for a run, with an explicit client (LANGSMITH_* environment lookups are cached at import)."""
    key = secret(settings.langsmith_api_key)
    if not key:
        return []
    from langchain_core.tracers import LangChainTracer

    return [LangChainTracer(project_name=settings.langsmith_project, client=_langsmith_client(key, settings.langsmith_endpoint))]


@lru_cache(maxsize=4)
def _langsmith_client(key: str, endpoint: str | None) -> Any:
    """One client per key and endpoint: each client starts its own background thread and asks the API who it is."""
    from langsmith import Client

    return Client(api_key=key, api_url=endpoint) if endpoint else Client(api_key=key)
