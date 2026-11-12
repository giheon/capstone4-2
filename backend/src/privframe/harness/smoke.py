"""M3 smoke checks: is each tool reachable and fast enough for the design's assumptions?

    uv run --group models python -m privframe.harness.smoke              # everything that can run
    uv run --group models python -m privframe.harness.smoke stream rag   # chosen targets

Targets without credentials: env, catalog, models-public, adapters, stream (U11), perception (U6).
Targets that need .env keys: openrouter (U4), langsmith, law, corpus (LAW_OC for API documents, OpenRouter for
the tool-calling step; parts without a key are reported, not failed). Results go to data/smoke/<stamp>/.
Frames sent to external APIs come only from a synthetic clip; team videos stay on this machine.
"""
import argparse
import base64
import importlib.metadata as md
import json
import platform
import shutil
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from privframe.core.pipeline import list_profiles, load_catalog, load_profile, resolve, service_profiles
from privframe.core.settings import ROOT, CoreSettings, secret

OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"
TEAM_VIDEO = ROOT / "data" / "videos" / "PrivFrame_ABA" / "A_B_A.mp4"
YUNET = ROOT / "models" / "face_detection_yunet_2023mar.onnx"


@dataclass
class Result:
    status: str  # pass | fail | skip | warn
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def _rel(path: Path) -> str:
    """Repository-relative path when possible (results may live outside the repo, e.g. in tests)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _version(package: str) -> str:
    try:
        return md.version(package)
    except md.PackageNotFoundError:
        return "—"


def _get_json(url: str, timeout: float = 30) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        return json.load(response)


# ---------- no credentials ----------


def smoke_env(settings: CoreSettings) -> Result:
    import av
    import cv2

    versions = {p: _version(p) for p in ["langgraph", "langchain", "langchain-core", "langsmith", "langchain-openrouter", "av", "opencv-python-headless", "torch", "sentence-transformers", "rfdetr", "rapidocr", "open-image-models", "trackers", "kiwipiepy", "bm25s"]}
    try:
        import torch

        accel = "mps" if torch.backends.mps.is_available() else "cpu"
    except ImportError:
        accel = "torch 미설치 (uv sync --group models)"
    details = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "credentials": settings.present(),
        "versions": versions,
        "ffmpeg_cli": shutil.which("ffmpeg") is not None,
        "ffmpeg_in_pyav": ".".join(str(x) for x in av.library_versions.get("libavcodec", ())),
        "cv2": cv2.__version__,
        "torch_device": accel,
    }
    missing = [k for k, v in settings.present().items() if not v and k != "GEMINI_API_KEY"]
    status = "warn" if missing else "pass"
    return Result(status, f"키 없음: {', '.join(missing)}" if missing else "필수 키가 모두 있음", details)


def smoke_catalog(settings: CoreSettings) -> Result:
    catalog = load_catalog(settings.pipeline_dir)
    rows = {}
    for profile_id in list_profiles(settings.pipeline_dir):
        resolved = resolve(catalog, load_profile(settings.pipeline_dir, profile_id))
        rows[profile_id] = {"config_hash": resolved.config_hash, "frames_leave_device": resolved.frames_leave_device}
    unknown = [f"{s.id}:{c.id}" for s in catalog.slots for c in s.candidates if c.adapter not in catalog.adapters]
    details = {"parts": len(catalog.slots), "candidates": sum(len(s.candidates) for s in catalog.slots), "profiles": rows, "service": service_profiles(settings.pipeline_dir), "unknown_adapters": unknown}
    return Result("fail" if unknown else "pass", f"부품 {details['parts']} · 후보 {details['candidates']} · 프로필 {len(rows)}", details)


def smoke_models_public(settings: CoreSettings) -> Result:
    """OpenRouter slugs, prices and capabilities, and Hugging Face repos named in the catalog, against public lists."""
    catalog = load_catalog(settings.pipeline_dir)
    listing = {m["id"]: m for m in _get_json(OPENROUTER_MODELS)["data"]}
    problems, checked = [], 0
    for slot in catalog.slots:
        for c in slot.candidates:
            if c.adapter != "openrouter" or not c.ref:
                continue
            checked += 1
            m = listing.get(c.ref)
            if not m:
                problems.append(f"{slot.id}/{c.id}: OpenRouter에 '{c.ref}' 없음")
                continue
            params = set(m.get("supported_parameters") or [])
            inputs = set((m.get("architecture") or {}).get("input_modalities") or [])
            caps = {"tools": "tools" in params, "json": "structured_outputs" in params, "image": "image" in inputs, "video": "video" in inputs}
            for cap in c.caps:
                if not caps.get(cap, False):
                    problems.append(f"{slot.id}/{c.id}: 카탈로그는 {cap} 지원으로 적었지만 목록에는 없음")
            listed = (round(float(m["pricing"]["prompt"]) * 1e6, 3), round(float(m["pricing"]["completion"]) * 1e6, 3))
            if c.price and (abs(listed[0] - c.price[0]) > 1e-6 or abs(listed[1] - c.price[1]) > 1e-6):
                problems.append(f"{slot.id}/{c.id}: 단가 카탈로그 {c.price} ≠ 목록 {listed}")
    hf_refs = sorted({c.ref for s in catalog.slots for c in s.candidates if c.adapter in {"sentence-transformers", "cross-encoder", "mlx-vlm", "transformers-detection"} and c.ref})
    hf_missing = []
    for ref in hf_refs:
        try:
            _get_json(f"https://huggingface.co/api/models/{urllib.parse.quote(ref, safe='/')}", timeout=20)
        except Exception as error:  # noqa: BLE001 — any failure means the reference cannot be used as written
            hf_missing.append(f"{ref} ({error})")
    problems += [f"Hugging Face 저장소 확인 실패: {x}" for x in hf_missing]
    status = "pass" if not problems else "warn"
    return Result(status, f"OpenRouter 후보 {checked}개 · HF 저장소 {len(hf_refs)}개 확인, 불일치 {len(problems)}건", {"problems": problems, "hf_checked": hf_refs})


def smoke_stream(settings: CoreSettings) -> Result:
    """U11: a case-shaped LangGraph (subgraph, create_agent with tools, interrupt in a tool, custom events)
    streams into every normalized event kind the Lab draws."""
    import operator
    from typing import Annotated, TypedDict

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.types import Command, interrupt

    from privframe.core.events import RunEventNormalizer

    class FakeToolModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # the fake replays scripted tool calls
            return self

    class CaseState(TypedDict, total=False):
        video: str
        understanding: Annotated[list, operator.add]
        messages: Annotated[list, add_messages]
        plan: str

    meta = {"model_name": "anthropic/claude-sonnet-5.5"}
    usage = lambda i, o, c=0: {"input_tokens": i, "output_tokens": o, "total_tokens": i + o, "input_token_details": {"cache_read": c}}  # noqa: E731
    script = iter(
        [
            AIMessage(content="", tool_calls=[{"name": "search_law", "args": {"query": "제3자 얼굴 영상 공개 동의 요건"}, "id": "call_1"}], usage_metadata=usage(6400, 310), response_metadata=meta),
            AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"question": "어디에 공개할 예정인가요?"}, "id": "call_2"}], usage_metadata=usage(7100, 160, 5400), response_metadata=meta),
            AIMessage(content="요건 3건을 정리해 제출한다.", usage_metadata=usage(8800, 900, 7400), response_metadata=meta),
        ]
    )

    @tool
    def search_law(query: str) -> str:
        """법령·판례 하이브리드 검색"""
        get_stream_writer()({"kind": "budget", "agent": "legal_analyst", "key": "searches", "used": 1, "limit": 12})
        return "규범 3 · 해석 2 — 제17조제1항, 대법원 2004다16280"

    @tool
    def ask_user(question: str) -> str:
        """사실이 부족할 때 사용자에게 묻는다 (질문 게이트 뒤 interrupt)"""
        return str(interrupt({"payload": "questions", "summary": f"질문 1건: {question}"}))

    def ingest(state: CaseState):
        get_stream_writer()({"kind": "activity", "text": "원본을 수정하지 않고 보존했습니다.", "tone": "info"})
        return {"video": "원본 보존 · 1920×1080"}

    def describe_segments(state: CaseState):
        get_stream_writer()({"kind": "llm", "node": "perceive/describe_segments", "agent": "perceive", "model": "qwen/qwen3.6-35b-a3b", "tokensIn": 9200, "cachedIn": 0, "tokensOut": 860, "costUsd": 0.0017, "ms": 700})
        return {"understanding": ["장면 4 · 카페 영업장"]}

    perceive = (
        StateGraph(CaseState)
        .add_node("detect_track", lambda s: {"understanding": ["얼굴 9 · 전신 6"]})
        .add_node("describe_segments", describe_segments)
        .add_edge(START, "detect_track")
        .add_edge("detect_track", "describe_segments")
        .add_edge("describe_segments", END)
        .compile()
    )
    analyst = create_agent(FakeToolModel(messages=script), tools=[search_law, ask_user], name="legal_analyst")

    def plan(state: CaseState):
        get_stream_writer()({"kind": "validator", "node": "plan", "ok": True, "issues": []})
        return {"plan": "결정 5건"}

    graph = (
        StateGraph(CaseState)
        .add_node("ingest", ingest)
        .add_node("perceive", perceive)
        .add_node("legal_analyst", analyst)
        .add_node("plan", plan)
        .add_edge(START, "ingest")
        .add_edge("ingest", "perceive")
        .add_edge("perceive", "legal_analyst")
        .add_edge("legal_analyst", "plan")
        .add_edge("plan", END)
        .compile(checkpointer=InMemorySaver())
    )
    norm = RunEventNormalizer(prices={"anthropic/claude-sonnet-5.5": (2.0, 10.0)})
    config = {"configurable": {"thread_id": "u11-smoke"}}
    modes = ["tasks", "checkpoints", "updates", "custom"]
    norm.run_start("U11 스모크 (가짜 모델)", "baseline-v2", "c6fe6e0e", {"analyst": "Claude Sonnet 5.5"})
    norm.emit("run.status", status="ANALYZING")
    for part in graph.stream({"messages": [{"role": "user", "content": "관찰 요약 · 알려진 사실"}]}, config, stream_mode=modes, subgraphs=True):
        norm.feed(*part)
    pending = [i for task in graph.get_state(config).tasks for i in task.interrupts]
    if not pending:
        return Result("fail", "ask_user가 interrupt를 일으키지 않았습니다", {"events": len(norm.events)})
    norm.emit("run.status", status="AWAITING_ANSWERS")
    norm.resume(pending[0].id, "Q1=불특정 다수")
    for part in graph.stream(Command(resume="불특정 다수"), config, stream_mode=modes, subgraphs=True):
        norm.feed(*part)
    norm.emit("run.end", status="COMPLETED")

    expected = {"run.start", "run.status", "node.start", "node.end", "edge", "llm", "tool.call", "tool.result", "message", "state", "interrupt", "resume", "checkpoint", "budget", "validator", "activity", "run.end"}
    kinds: dict[str, int] = {}
    for event in norm.events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
    nodes = sorted({e["node"] for e in norm.events if e["kind"] == "node.start"})
    missing = sorted(expected - kinds.keys())
    out = settings_out(settings) / "u11-events.jsonl"
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in norm.events), encoding="utf-8")
    details = {"kinds": kinds, "missing": missing, "nodes": nodes, "file": _rel(out)}
    return Result("pass" if not missing else "fail", f"이벤트 {len(norm.events)}개 · 종류 {len(kinds)}/{len(expected)}" + (f" · 빠짐 {missing}" if missing else ""), details)


def smoke_adapters(settings: CoreSettings) -> Result:
    """Which rag.* catalog candidates this backend can run today (the Lab lists them all; some have no adapter yet)."""
    from privframe.core.retrieval import support

    catalog = load_catalog(settings.pipeline_dir)
    rows, missing = {}, []
    for slot in catalog.slots:
        if not slot.id.startswith("rag."):
            continue
        rows[slot.id] = {}
        for candidate in slot.candidates:
            ok, reason = support(slot.id, candidate)
            rows[slot.id][candidate.id] = "구현됨" if ok and not reason else reason
            if not ok:
                missing.append(f"{slot.label}: {candidate.label}")
    service = resolve(catalog, load_profile(settings.pipeline_dir, service_profiles(settings.pipeline_dir)["default"]))
    blocked = [s for s in rows if not support(s, service.part(s).candidate)[0]]
    status = "fail" if blocked else "pass"
    return Result(status, f"서비스 프로필의 검색 부품은 모두 구현됨 · 카탈로그 후보 중 미구현 {len(missing)}개" if not blocked else f"서비스 프로필 부품 미구현: {blocked}", {"support": rows, "not_implemented": missing})


CORPUS_LIST = ROOT / "configs" / "corpus" / "smoke.yaml"
# (query, citations of which at least one must be in the top 5)
CORPUS_QUERIES = [
    ("촬영 동의와 공표 동의", {"대법원 2021다219116"}),
    ("폭행 장면 촬영 증거", {"대법원 2020다227455"}),
    ("이동형 영상정보처리기기 촬영 사실 표시", {"개인정보 보호법", "표준 개인정보 보호지침", "이동형 영상정보처리기기 안내서"}),
]
CORPUS_QUESTION = "모델 촬영 계약으로 찍은 사진을 계약에서 정한 범위를 넘어 계속 광고에 써도 되는지, 근거가 되는 판례를 찾아 답해라."


def smoke_corpus(settings: CoreSettings) -> Result:
    """Real documents end to end: collect → store → split → embed and index → search → read → an LLM answers
    through the search tools → trace. The list is a small smoke set, not the service corpus."""
    from privframe.core.parsers import parse_all
    from privframe.core.retrieval import Index, build_index, get_document
    from privframe.harness.corpus import fetch, open_store, spec_for

    details: dict[str, Any] = {}
    problems: list[str] = []
    notes: list[str] = []
    store = open_store(settings)

    # 1. collect, twice: the second pass must find nothing new
    first = fetch(settings, CORPUS_LIST)
    second = fetch(settings, CORPUS_LIST)
    details["fetch"] = {o.entry_id: o.status for o in first}
    problems += [f"수집 실패 {o.entry_id}: {o.detail}" for o in first if o.status == "failed"]
    notes += [f"{o.entry_id}: {o.detail}" for o in first if o.status == "unavailable"]
    changed = [o.entry_id for o in second if o.status in ("new", "updated")]
    if changed:
        problems.append(f"다시 수집했을 때 바뀐 것으로 처리된 문서: {changed}")

    # 2. split into units
    parsed = parse_all(store)
    problems += [f"분할 실패 {r.doc_id}: {s}" for r, s in parsed if s.startswith("error")]
    documents = store.documents()
    details["documents"] = {d.record.doc_id: {"kind": d.record.kind, "source": d.record.source, "parser": d.parser, "units": len(d.units), "indexed": d.record.index} for d in documents}
    if not any(d.record.index for d in documents):
        return Result("fail", "색인할 문서가 없습니다", details)

    # 3. index with the service profile's rag.* parts
    spec, profile_id, config_hash = spec_for(settings, None)
    t0 = time.perf_counter()
    index = build_index(store, spec, device=settings.torch_device)
    details["index"] = {"fingerprint": index.fingerprint, "chunks": index.meta["chunks"], "dimensions": index.meta["dimensions"], "device": index.meta["device"], "build_seconds": index.meta["seconds"], "open_seconds": round(time.perf_counter() - t0, 1), "spec": index.meta["spec"]}
    indexed_citations = {c.citation for c in index.chunks}

    # 4. search: hybrid + rerank
    details["search"] = []
    for query, expected in CORPUS_QUERIES:
        t0 = time.perf_counter()
        hits = index.search(query, k=5)
        found = [h.citation for h in hits]
        row = {"query": query, "top5": [f"{h.citation} › {h.label}" for h in hits], "seconds": round(time.perf_counter() - t0, 2)}
        if not (expected & indexed_citations):
            row["result"] = "기대 문서가 색인에 없음 (수집 불가)"
            notes.append(f"질의 '{query}'의 기대 문서가 색인에 없습니다")
        elif expected & set(found):
            row["result"] = "pass"
        else:
            row["result"] = "fail"
            problems.append(f"질의 '{query}'의 상위 5개에 기대 문서가 없습니다: {found}")
        details["search"].append(row)

    # 5. swap query-time parts through the profile: BM25 only, no reranker (same index)
    swapped_spec, _, _ = spec_for(settings, None, {"rag.retrieval": "bm25", "rag.reranker": "none"})
    swapped = Index(index.directory, swapped_spec, device=settings.torch_device)
    lexical = swapped.search(CORPUS_QUERIES[1][0], k=3)
    details["part_swap"] = {"mode": swapped_spec.mode, "reranker": swapped_spec.reranker_adapter, "top3": [f"{h.citation} › {h.label}" for h in lexical], "ranks": [h.ranks for h in lexical]}
    if not lexical or any("dense" in h.ranks or "rerank" in h.ranks for h in lexical):
        problems.append("부품 교체(BM25만, 리랭커 없음)가 반영되지 않았습니다")

    # 6. read the original text
    try:
        article = get_document(store, "개인정보 보호법", "제25조의2")
        details["get_document"] = {"ref": "개인정보 보호법 제25조의2", "chars": len(article["units"][0]["text"]), "starts": article["units"][0]["text"][:60]}
        if "이동형" not in article["units"][0]["text"]:
            problems.append("get_document가 제25조의2 원문을 돌려주지 않았습니다")
    except Exception as error:  # noqa: BLE001
        notes.append(f"get_document: {error}")

    # 7–8. an LLM answers one question through the tools; the run is traced
    key = secret(settings.openrouter_api_key)
    if not key:
        notes.append("OPENROUTER_API_KEY가 없어 LLM 도구 호출 단계를 건너뜀")
    else:
        try:
            details["llm"] = _corpus_agent_round(settings, store, index, profile_id, config_hash)
            if details["llm"]["unknown_citations"]:
                problems.append(f"검색하지 않은 문서를 인용: {details['llm']['unknown_citations']}")
            if not details["llm"]["tool_calls"].get("search_law"):
                problems.append("모델이 search_law를 호출하지 않았습니다")
            if details["llm"].get("trace") == "not found":
                problems.append("LangSmith에서 추적을 찾지 못했습니다")
        except Exception as error:  # noqa: BLE001
            problems.append(f"LLM 도구 호출 단계: {type(error).__name__}: {error}")
    details["notes"] = notes
    indexed = sum(1 for d in documents if d.record.index)
    summary = f"문서 {len(documents)}건(색인 {indexed}) · 청크 {index.meta['chunks']} · 검색 {sum(1 for r in details['search'] if r['result'] == 'pass')}/{len(CORPUS_QUERIES)}"
    if "llm" in details:
        summary += f" · LLM 도구 호출 {sum(details['llm']['tool_calls'].values())}회"
    if problems:
        return Result("fail", " / ".join(problems), details)
    return Result("warn" if notes else "pass", summary + (f" · 참고 {len(notes)}건" if notes else ""), details)


def _corpus_agent_round(settings: CoreSettings, store: Any, index: Any, profile_id: str, config_hash: str) -> dict[str, Any]:
    """One question answered by the profile's analyst model with search_law and get_document as real tools."""
    from langchain.agents import create_agent
    from langchain.agents.structured_output import ProviderStrategy
    from langchain_core.tools import tool
    from pydantic import BaseModel, Field

    from privframe.core.llm import chat_model, cost_usd
    from privframe.core.retrieval import get_document

    profile = resolve(load_catalog(settings.pipeline_dir), load_profile(settings.pipeline_dir, profile_id))
    analyst = profile.part("llm.analyst")
    seen: set[str] = set()
    calls: dict[str, int] = {}
    budget = 4  # the real agents get budgets from the profile; the smoke only needs a short loop that ends
    over_budget = "도구 호출 한도에 도달했습니다. 지금까지 찾은 근거만으로 답하세요."

    @tool
    def search_law(query: str, kinds: list[str] | None = None) -> str:
        """법령·판례·결정문·안내서를 검색한다. kinds로 종류를 좁힐 수 있다(law, admrul, prec, detc, expc, ppc, guideline).
        결과의 doc_id와 unit_id로 get_document를 호출하면 원문을 읽을 수 있다."""
        if sum(calls.values()) >= budget:
            return over_budget
        calls["search_law"] = calls.get("search_law", 0) + 1
        hits = index.search(query, k=5, kinds=kinds or None)
        seen.update(h.doc_id for h in hits)
        return json.dumps([{"doc_id": h.doc_id, "unit_id": h.unit_id, "citation": h.citation, "label": h.label, "text": h.text[:300]} for h in hits], ensure_ascii=False)

    @tool
    def get_document_tool(doc_id: str, unit_id: str | None = None) -> str:
        """문서의 원문을 읽는다. unit_id를 주면 그 조문·쪽·항목만 돌려준다."""
        if sum(calls.values()) >= budget:
            return over_budget
        calls["get_document"] = calls.get("get_document", 0) + 1
        try:
            document = get_document(store, doc_id, unit_id)
        except Exception as error:  # noqa: BLE001 — the model should see why the lookup failed
            return f"오류: {error}"
        seen.add(document["doc_id"])
        # the smoke keeps tool output short; M4 decides how much text an agent turn carries
        return json.dumps({**document, "units": [{**u, "text": u["text"][:2000]} for u in document["units"][:2]]}, ensure_ascii=False)

    get_document_tool.name = "get_document"

    class Citation(BaseModel):
        doc_id: str = Field(description="search_law나 get_document가 돌려준 doc_id")
        unit_id: str

    class Answer(BaseModel):
        answer: str = Field(description="두세 문장의 한국어 답")
        citations: list[Citation]

    agent = create_agent(
        chat_model(analyst, settings, max_tokens=2000),
        tools=[search_law, get_document_tool],
        system_prompt="한국 개인정보·초상권 법률 조사 보조. 반드시 search_law로 근거를 찾고, 인용하려는 판례는 get_document로 원문을 읽은 뒤 답한다. 검색 결과에 없는 문서는 인용하지 않는다. 근거를 찾으면 더 찾지 말고 바로 답한다.",
        # the provider's own JSON-schema output; the tool-based strategy forces a tool_choice value that
        # OpenRouter's Claude endpoints reject when require_parameters is on
        response_format=ProviderStrategy(Answer),
    )
    marker = f"m3-corpus-{int(time.time())}"
    config: dict[str, Any] = {"recursion_limit": 40, "run_name": "privframe-m3-corpus-smoke", "metadata": {"profile": profile_id, "config_hash": config_hash, "index_fingerprint": index.fingerprint, "marker": marker}}
    client = None
    if secret(settings.langsmith_api_key):
        from langchain_core.tracers import LangChainTracer
        from langsmith import Client

        client = Client(api_key=secret(settings.langsmith_api_key), api_url=settings.langsmith_endpoint) if settings.langsmith_endpoint else Client(api_key=secret(settings.langsmith_api_key))
        config["callbacks"] = [LangChainTracer(project_name=settings.langsmith_project, client=client)]
    t0 = time.perf_counter()
    state = agent.invoke({"messages": [{"role": "user", "content": CORPUS_QUESTION}]}, config)
    seconds = round(time.perf_counter() - t0, 1)
    answer = state.get("structured_response")
    tokens_in = sum((m.usage_metadata or {}).get("input_tokens", 0) for m in state["messages"] if getattr(m, "usage_metadata", None))
    tokens_out = sum((m.usage_metadata or {}).get("output_tokens", 0) for m in state["messages"] if getattr(m, "usage_metadata", None))
    cited = [c.doc_id for c in answer.citations] if answer else []
    out: dict[str, Any] = {
        "model": analyst.candidate.ref,
        "question": CORPUS_QUESTION,
        "answer": answer.answer if answer else None,
        "citations": [f"{c.doc_id} {c.unit_id}" for c in answer.citations] if answer else [],
        "unknown_citations": sorted(set(cited) - seen),
        "tool_calls": calls,
        "seconds": seconds,
        "tokens": {"in": tokens_in, "out": tokens_out},
        "cost_usd_catalog_price": round(cost_usd(analyst, tokens_in, tokens_out), 4),
    }
    if answer is None:
        raise RuntimeError("구조화된 답(structured_response)이 없습니다")
    if client is not None:
        client.flush()
        out["trace"] = "not found"
        for _ in range(20):
            try:
                runs = list(client.list_runs(project_name=settings.langsmith_project, filter=f'has(metadata, \'{{"marker": "{marker}"}}\')', limit=1))
                if runs:
                    out["trace"] = client.get_run_url(run=runs[0], project_name=settings.langsmith_project)
                    break
            except Exception:  # noqa: BLE001 — retried below
                pass
            time.sleep(2)
    return out


def smoke_perception(settings: CoreSettings) -> Result:
    """U6: CPU perception cost per minute of video at the profile's analysis rate and proxy size."""
    import av
    import cv2
    import numpy as np

    profile = resolve(load_catalog(settings.pipeline_dir), load_profile(settings.pipeline_dir, service_profiles(settings.pipeline_dir)["default"]))
    fps = float(profile.params["perception.analysis_fps"])
    short_side = int(profile.params["perception.proxy_short_side"])
    if not TEAM_VIDEO.exists():
        return Result("skip", f"벤치마크 영상이 없습니다 ({_rel(TEAM_VIDEO)})")
    frames: list[np.ndarray] = []
    started = time.perf_counter()
    with av.open(str(TEAM_VIDEO)) as container:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else float(container.duration or 0) / 1e6
        step, next_t = 1.0 / fps, 0.0
        for frame in container.decode(stream):
            if frame.time is None or frame.time + 1e-6 < next_t:
                continue
            next_t += step
            image = frame.to_ndarray(format="bgr24")
            h, w = image.shape[:2]
            scale = short_side / min(h, w)
            frames.append(cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA))
            if len(frames) >= int(fps * 20):  # 20 s of analysis frames is enough to time each stage
                break
    decode_s = time.perf_counter() - started
    sampled = len(frames)
    sampled_seconds = sampled / fps
    stages: dict[str, float] = {"decode_resize": decode_s / sampled_seconds}

    def per_second(name: str, fn: Callable[[np.ndarray], Any], every: int = 1) -> None:
        subset = frames[::every]
        fn(subset[0])  # warm-up (lazy model download, graph build)
        t0 = time.perf_counter()
        for image in subset:
            fn(image)
        stages[name] = (time.perf_counter() - t0) / sampled_seconds

    face = cv2.FaceDetectorYN.create(str(YUNET), "", (frames[0].shape[1], frames[0].shape[0]), 0.8)
    per_second("face_yunet", lambda img: face.detect(img))
    notes: dict[str, str] = {}
    cpu = ["CPUExecutionProvider"]  # U6 is about CPU-only perception; ONNX Runtime would otherwise pick CoreML
    try:
        from open_image_models import create_detector

        onnx_detector = create_detector("rf-detr-nano-384-coco", providers=cpu)
        per_second("objects_rfdetr_nano_onnx", lambda img: onnx_detector.predict(img))
        plates = create_detector(profile.part("perception.plate_detector").candidate.ref, providers=cpu)
        per_second("plates_yolo_v9_t384", lambda img: plates.predict(img))
    except Exception as error:  # noqa: BLE001
        notes["open_image_models"] = f"실행 실패: {error}"
    try:
        from rfdetr import RFDETRNano

        torch_detector = RFDETRNano(device="cpu")
        per_second("objects_rfdetr_nano_torch", lambda img: torch_detector.predict(img[:, :, ::-1].copy(), threshold=0.5), every=4)
    except Exception as error:  # noqa: BLE001
        notes["objects_rfdetr_nano_torch"] = f"실행 실패: {error}"
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        ref = profile.part("perception.ocr").candidate.ref or ""
        ocr = RapidOCR(
            params={
                "Global.log_level": "warning",
                "Det.ocr_version": OCRVersion.PPOCRV6,
                "Det.model_type": ModelType.SMALL if "det_small" in ref else ModelType.TINY,
                "Rec.lang_type": LangRec.KOREAN,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            }
        )
        per_second("ocr_rapidocr_ko_1fps", lambda img: ocr(img), every=max(1, int(fps)))  # design §7.4: OCR on 1 fps keyframes
    except Exception as error:  # noqa: BLE001
        notes["ocr_rapidocr_ko_1fps"] = f"실행 실패: {error}"
    # the torch build is timed only for comparison; the pipeline would use one object detector, not both
    pipeline = [k for k in stages if k != "objects_rfdetr_nano_torch"]
    per_minute = {k: round(v * 60, 1) for k, v in stages.items()}
    total = round(sum(per_minute[k] for k in pipeline), 1)
    details = {
        "video": _rel(TEAM_VIDEO),
        "video_seconds": round(duration, 1),
        "timed_seconds": round(sampled_seconds, 1),
        "analysis_fps": fps,
        "proxy_short_side": short_side,
        "seconds_per_video_minute": per_minute,
        "total_seconds_per_minute": total,
        "total_counts": pipeline,
        "notes": notes,
        "target": "U6: 1분 영상의 지각 처리 ≤ 180 s (VLM 제외)",
    }
    status = "pass" if total <= 180 and not notes else ("warn" if notes else "fail")
    return Result(status, f"1분 영상당 {total} s (목표 ≤ 180 s, VLM 제외)", details)


# ---------- credentials ----------


def _synthetic_clip(seconds: int = 4, fps: int = 12) -> bytes:
    """A clip with no real person: a coloured background, a name-tag-like label and a plate-like box.
    Drawn with Pillow and encoded with PyAV (Homebrew's ffmpeg build has no drawtext filter)."""
    import io

    import av
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font_path = next((f for f in ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"] if Path(f).exists()), None)
    font = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    image = Image.new("RGB", (640, 360), (59, 91, 107))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 90, 330, 160), fill=(240, 240, 240))
    draw.text((95, 105), "김민지 바리스타", font=font, fill=(10, 10, 10))
    draw.rectangle((420, 290, 610, 345), fill=(255, 255, 255))
    draw.text((440, 300), "12가 3456", font=font, fill=(10, 10, 10))
    pixels = np.asarray(image)
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        for _ in range(seconds * fps):
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


def smoke_openrouter(settings: CoreSettings) -> Result:
    """Structured output (planner), tool calling (analyst) and U4 video input (scene VLM) through OpenRouter."""
    key = secret(settings.openrouter_api_key)
    if not key:
        return Result("skip", "OPENROUTER_API_KEY가 없습니다")
    import httpx
    from pydantic import BaseModel

    from privframe.core.llm import chat_model

    profile = resolve(load_catalog(settings.pipeline_dir), load_profile(settings.pipeline_dir, service_profiles(settings.pipeline_dir)["default"]))
    details: dict[str, Any] = {}
    failures: list[str] = []

    class Decision(BaseModel):
        target: str
        method: str
        reason: str

    try:
        planner = chat_model(profile.part("llm.planner"), settings, max_tokens=300)
        t0 = time.perf_counter()
        out = planner.with_structured_output(Decision, method="json_schema", strict=True).invoke(
            "직원 얼굴(person-3)을 공개 영상에서 어떻게 처리할지 한 줄로 정해라. method는 keep|mosaic 중 하나."
        )
        details["planner_json"] = {"model": profile.part("llm.planner").candidate.ref, "ok": isinstance(out, Decision), "value": out.model_dump(), "s": round(time.perf_counter() - t0, 1)}
    except Exception as error:  # noqa: BLE001
        failures.append(f"planner json_schema: {error}")
    try:
        from langchain_core.tools import tool

        @tool
        def search_law(query: str, kinds: list[str]) -> str:
            """한국 개인정보 법령·판례 검색"""
            return ""

        analyst = chat_model(profile.part("llm.analyst"), settings, max_tokens=300).bind_tools([search_law])
        t0 = time.perf_counter()
        message = analyst.invoke("영상에서 동의하지 않은 직원 얼굴을 공개할 때의 요건을 찾으려면 무엇을 검색해야 하나? 도구를 호출해라.")
        details["analyst_tools"] = {"model": profile.part("llm.analyst").candidate.ref, "tool_calls": [c["name"] for c in message.tool_calls], "usage": message.usage_metadata, "s": round(time.perf_counter() - t0, 1)}
        if not message.tool_calls:
            failures.append("analyst가 도구를 호출하지 않음")
    except Exception as error:  # noqa: BLE001
        failures.append(f"analyst tools: {error}")
    vlm = profile.part("vlm.scene").candidate
    clip = ""
    if vlm.adapter == "openrouter" and vlm.ref:
        try:
            clip = base64.b64encode(_synthetic_clip()).decode()
        except Exception as error:  # noqa: BLE001
            failures.append(f"합성 클립 생성 실패: {error}")
    if clip:
        body = {
            "model": vlm.ref,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "이 영상에 보이는 글자와 물체를 JSON으로 답해라: {\"texts\": [...], \"objects\": [...], \"place\": \"...\"}"},
                        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{clip}"}},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "provider": {"require_parameters": True, "data_collection": "deny", **({"zdr": True} if settings.openrouter_zdr else {})},
            # a reasoning VLM can spend a small budget on thinking and return no answer; keep effort low, room high
            "reasoning": {"effort": "low"},
            "max_tokens": 1500,
        }
        attempts = []
        for _ in range(3):  # U4 is about stability, so the same request goes three times
            try:
                t0 = time.perf_counter()
                response = httpx.post("https://openrouter.ai/api/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}", "X-Title": "PrivFrame"}, timeout=120)
                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                text = choice.get("message", {}).get("content") or ""
                attempts.append(
                    {
                        "http": response.status_code,
                        "provider": data.get("provider"),
                        "finish": choice.get("finish_reason"),
                        "read_texts": "3456" in text and "김민지" in text,
                        "answer": text[:300],
                        "cost": (data.get("usage") or {}).get("cost"),
                        "s": round(time.perf_counter() - t0, 1),
                        "error": data.get("error"),
                    }
                )
            except Exception as error:  # noqa: BLE001
                attempts.append({"error": f"{type(error).__name__}: {error}"})
        ok = sum(1 for a in attempts if a.get("read_texts"))
        details["vlm_video_u4"] = {"model": vlm.ref, "ok": f"{ok}/{len(attempts)}", "attempts": attempts}
        if ok < len(attempts):
            failures.append(f"U4 영상 입력 {ok}/{len(attempts)} 성공")
    status = "pass" if not failures else "fail"
    return Result(status, "구조화 출력 · 도구 호출 · 영상 입력 통과" if not failures else " / ".join(failures), details)


def smoke_langsmith(settings: CoreSettings) -> Result:
    key = secret(settings.langsmith_api_key)
    if not key:
        return Result("skip", "LANGSMITH_API_KEY가 없습니다")
    from langsmith import Client, traceable, tracing_context

    # an explicit client and tracing context: LangSmith caches LANGSMITH_* environment lookups at import time
    client = Client(api_key=key, api_url=settings.langsmith_endpoint) if settings.langsmith_endpoint else Client(api_key=key)
    marker = f"m3-smoke-{int(time.time())}"
    profile_id = service_profiles(settings.pipeline_dir)["default"]
    resolved = resolve(load_catalog(settings.pipeline_dir), load_profile(settings.pipeline_dir, profile_id))

    @traceable(name="privframe-m3-smoke", client=client, project_name=settings.langsmith_project, metadata={"profile": profile_id, "config_hash": resolved.config_hash, "marker": marker})
    def traced(x: int) -> int:
        return x * 2

    with tracing_context(enabled=True):
        traced(21)
    client.flush()
    last_error = ""
    for _ in range(20):
        try:
            runs = list(client.list_runs(project_name=settings.langsmith_project, filter=f'has(metadata, \'{{"marker": "{marker}"}}\')', limit=1))
            if runs:
                return Result("pass", f"프로젝트 {settings.langsmith_project}에 추적이 기록됨", {"run_id": str(runs[0].id), "config_hash": resolved.config_hash, "url": client.get_run_url(run=runs[0], project_name=settings.langsmith_project)})
        except Exception as error:  # noqa: BLE001 — the project is created by the first ingested run
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(2)
    return Result("fail", f"추적을 40초 안에 읽어 오지 못했습니다 ({last_error or '결과 없음'})", {"project": settings.langsmith_project, "endpoint": settings.langsmith_endpoint or "기본(US)"})


def smoke_law(settings: CoreSettings) -> Result:
    """One search per API target (statute, rule, precedent, constitutional decision, interpretation, PIPC decision).
    Documents themselves are fetched and parsed by the corpus smoke."""
    oc = secret(settings.law_oc)
    if not oc:
        return Result("skip", "LAW_OC가 없습니다 (open.law.go.kr에서 OC 신청 + 서버 IP 등록)")
    from privframe.core.lawgokr import LawApiError, LawGoKr

    api = LawGoKr(oc)
    probes = {"law": {"query": "개인정보 보호법"}, "admrul": {"query": "표준 개인정보 보호지침"}, "prec": {"nb": "2020다253423"}, "detc": {"query": "2015헌마994"}, "expc": {"query": "영상정보처리기기"}, "ppc": {"query": "영상정보처리기기"}}
    details: dict[str, Any] = {}
    failures = []
    for target, params in probes.items():
        try:
            result = api.search(target, **params)
            hits = LawGoKr.items(result)
            details[target] = {"params": params, "total": LawGoKr.count(result), "hit_fields": sorted(hits[0])[:12] if hits else []}
            if not hits:
                failures.append(f"{target}: 결과 없음")
        except LawApiError as error:
            failures.append(f"{target}: {error}")
    summary = " · ".join(f"{t} {d['total']}건" for t, d in details.items())
    return Result("fail" if failures else "pass", " / ".join(failures) if failures else summary, details)


TARGETS: dict[str, Callable[[CoreSettings], Result]] = {
    "env": smoke_env,
    "catalog": smoke_catalog,
    "models-public": smoke_models_public,
    "adapters": smoke_adapters,
    "stream": smoke_stream,
    "perception": smoke_perception,
    "openrouter": smoke_openrouter,
    "langsmith": smoke_langsmith,
    "law": smoke_law,
    "corpus": smoke_corpus,
}

_OUT: Path | None = None


def settings_out(settings: CoreSettings) -> Path:
    global _OUT
    if _OUT is None:
        _OUT = settings.data_dir / "smoke" / datetime.now().strftime("%Y%m%d-%H%M%S")
        _OUT.mkdir(parents=True, exist_ok=True)
    return _OUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help=f"기본: 전부 · {' '.join(TARGETS)}")
    args = parser.parse_args(argv)
    unknown = [t for t in args.targets if t not in TARGETS]
    if unknown:
        parser.error(f"알 수 없는 대상: {', '.join(unknown)}")
    settings = CoreSettings()
    out = settings_out(settings)
    results: dict[str, dict[str, Any]] = {}
    for name in args.targets or list(TARGETS):
        started = time.perf_counter()
        try:
            result = TARGETS[name](settings)
        except Exception as error:  # noqa: BLE001 — a crashed smoke is a failed smoke, keep going
            result = Result("fail", f"{type(error).__name__}: {error}")
        results[name] = {"status": result.status, "summary": result.summary, "seconds": round(time.perf_counter() - started, 1), "details": result.details}
        print(f"[{result.status:>4}] {name:<14} {result.summary}  ({results[name]['seconds']} s)", flush=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"결과: {_rel(out)}/results.json")
    return 1 if any(r["status"] == "fail" for r in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
