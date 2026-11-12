"""The case graph (design D4): one thread per video, from ingest to the report.

ingest → perceive (subgraph) → [await_identity] → legal_analyst ⇄ ask_user → plan → review_guide → await_review
→ auditor → [await_audit_confirm] → render_original → verify_output → report. When the output check finds
something that was to be redacted still showing, verify_output sends the case back to render_original with
the places to cover (at most MAX_REPAIRS times).

State holds what later steps and the user need, in the web contract's shape where the web app reads it; heavy
artifacts (tracks, regions, the output) stay on disk under the case directory and state keeps their paths.
Models, the corpus index and other services come from the runtime context, never from state.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from .util import write_json
from . import artifacts, knowledge, report as reporting, stages, understanding as views
from .agents import planner
from .agents.analyst import AGENT, AnalystDeps, build_analyst
from .agents.memo import extract_memo_facts
from .domain import (
    METHOD_LABEL,
    REDACT_METHOD,
    SHAPE_LABEL,
    Analysis,
    Decision,
    Fact,
    Finding,
    Plate,
    Requirement,
    SegmentInfo,
    TextItem,
    Track,
    VideoAsset,
    VideoMeta,
    VideoUnderstanding,
    clip_ranges,
    merge_ranges,
)
from .geometry import SPATIAL_SCALE, STRENGTH, Region, build_regions, expected_frames, regions_from_json, regions_to_json, render_shape, scale_box
from .runtime import CaseContext, ModelCallError, activity, emit, note, patch
from .video import is_location_tag, load_times, make_proxy, probe
from .vlm import Vision

MAX_REPAIRS = 2  # times the output check may send a case back to the renderer


def _merge_dict(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class CaseState(TypedDict, total=False):
    upload: dict[str, Any]  # path, filename, memo, mode
    video: dict[str, Any]  # VideoMeta, then VideoAsset once the proxy exists
    perception: Annotated[dict[str, Any], _merge_dict]  # per-step summaries of the perceive subgraph
    understanding: dict[str, Any]  # path and counts of the VideoUnderstanding artifact
    segments: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    identity: dict[str, Any] | None
    facts: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    evidence: dict[str, dict[str, Any]]
    analyses: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    reeval: dict[str, Any] | None  # set when the user changed a fact during review
    plan: dict[str, Any]
    findings: list[dict[str, Any]]
    finding_fixes: dict[str, Any]
    audit_rounds: int
    edits: list[str]
    output: dict[str, Any]
    verification: list[dict[str, Any]]
    repairs: dict[str, list[list[Any]]]  # frame → extra regions the output check asked for
    repair_rounds: int
    report: dict[str, Any]


Ctx = Runtime[CaseContext]


def _dir(ctx: CaseContext, name: str) -> Path:
    path = ctx.workdir / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, value: Any) -> None:
    """Write a JSON file by replacing it, so a file that another case or the artifact store shares is never
    changed in place."""
    write_json(path, value)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _asset(state: CaseState) -> VideoAsset:
    return VideoAsset.model_validate(state["video"])


def _understanding(state: CaseState) -> VideoUnderstanding:
    """The case's video understanding with its coverage filled in (an artifact written before coverage was
    stored gets it here, once)."""
    path = Path(state["understanding"]["path"])
    understanding = views.load(path)
    if not understanding.visible and understanding.tracks:
        views.save(views.with_coverage(understanding, _asset(state)), path)
    return understanding


# ---------- steps whose results are kept ----------

STEP_VERSION = 3  # raise when a step's output format or meaning changes, so stored results are not reused


def _sig(ctx: CaseContext, *slots: str) -> list[str]:
    """What identifies the chosen candidate of each part, for a step key."""
    out = []
    for slot in slots:
        candidate = ctx.services.part(slot).candidate
        out.append(f"{slot}={candidate.id}|{candidate.adapter}|{candidate.ref}")
    return out


def _inputs(ctx: CaseContext, node: str) -> list[Any]:
    """The parts and parameters a step's result depends on (core/stages.py), as they are in this run."""
    step = stages.STEPS[node]
    return [_sig(ctx, *step.slots), [float(ctx.services.param(name)) for name in step.params]]


def _step(ctx: CaseContext, step: str, key: str, files: list[str], compute: Any, *, node: str | None = None, keep: Any = None) -> dict[str, Any]:
    """Run one step, or take its stored result when the same step was already computed from the same inputs
    (core/artifacts.py). `compute()` writes `files` under the case directory and returns the state update;
    `keep(update)` can refuse to store a result that should be computed again next time (a failed model call)."""
    store = ctx.artifacts
    if store is not None:
        hit = store.restore(step, key, ctx.workdir)
        if hit is not None:
            cost = float(hit.get("costUsd") or 0.0)
            spent = f" · ${cost:.4f}" if cost else ""
            emit("state", channel="reuse", writer=node or f"perceive/{step}", summary=f"같은 입력 · 같은 부품으로 계산한 결과를 재사용 (처음 계산 {hit['seconds']:.1f}초{spent})", step=step, key=key, savedSec=hit["seconds"], savedCostUsd=cost, source=hit.get("source"))
            return hit["update"]
        for name in files:
            (ctx.workdir / name).unlink(missing_ok=True)  # never write over a file another case may share
    started, spent_before = time.perf_counter(), ctx.services.cost.spent
    update = compute()
    if store is not None and (keep is None or keep(update)):
        # steps of one case run side by side, so a model call of another step can land in this figure; the
        # stored cost is what the case spent while this step ran
        store.store(step, key, ctx.workdir, files, update, time.perf_counter() - started, ctx.case_id, cost_usd=ctx.services.cost.spent - spent_before)
    return update


# ---------- ingest ----------


def ingest(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    upload = state["upload"]
    meta, pts = probe(Path(upload["path"]), upload.get("filename"))
    _write(ctx.workdir / "pts.json", pts)
    activity("원본을 수정하지 않고 보존했고, 메타데이터를 기록했습니다.")
    memo = (upload.get("memo") or "").strip()

    def read_memo() -> dict[str, Any]:
        try:
            return {"facts": [f.wire() for f in extract_memo_facts(ctx.services, memo)], "ok": True}
        except ModelCallError as error:  # the memo is optional; the analyst can still ask what it needs
            activity(f"메모에서 사실을 읽지 못했습니다. 필요한 사실은 질문으로 확인합니다. ({error})", "warn")
            return {"facts": [], "ok": False}

    wire = _step(ctx, "memo_facts", artifacts.key(STEP_VERSION, memo, _sig(ctx, "llm.planner")), [], read_memo, node="ingest", keep=lambda update: update["ok"])["facts"] if memo else []
    if wire:
        activity(f"메모에서 사실 {len(wire)}가지를 읽었습니다: {', '.join(f['label'] for f in wire)}")
    patch(facts=wire)
    rate = "가변 프레임레이트" if meta.vfr else f"{meta.avg_frame_rate} fps"
    note("video", "ingest", f"원본 보존 · {meta.display_width}×{meta.display_height} · {meta.frame_count:,}프레임 · {rate} · {meta.codec} {meta.profile} · 오디오 {meta.audio}")
    return {"video": {"meta": meta.model_dump()}, "facts": wire}


# ---------- perceive subgraph ----------


def ingest_probe(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    meta = VideoMeta.model_validate(state["video"]["meta"])
    fps = float(ctx.services.param("perception.analysis_fps", 12))
    samples = max(1, round(meta.duration_s * fps))
    return {"perception": {"probe": {"frames": meta.frame_count, "analysis_fps": fps, "samples": samples, "rotation": meta.rotation}}}


def proxy_step(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    meta = VideoMeta.model_validate(state["video"]["meta"])
    short_side = int(ctx.services.param("perception.proxy_short_side", 640))
    key = artifacts.key(STEP_VERSION, "make_proxy", meta.sha256, _inputs(ctx, "perceive/make_proxy"))

    def compute() -> dict[str, Any]:
        return {"proxy": make_proxy(meta, _read(ctx.workdir / "pts.json"), ctx.workdir / "proxy.mp4", short_side).model_dump()}

    from .domain import Proxy

    proxy = Proxy.model_validate(_step(ctx, "make_proxy", key, ["proxy.mp4"], compute)["proxy"])
    asset = VideoAsset(meta=meta, proxy=proxy, pts_path=str(ctx.workdir / "pts.json"))
    same = "원본과 동일" if proxy.pts_identical else "프레임 순번으로 대응"
    note("pts_map", "perceive/make_proxy", f"프록시 {proxy.width}×{proxy.height} · {proxy.frame_count:,}프레임 · PTS {same}")
    activity("분석용 축소 사본을 만들었습니다. 프레임 수가 원본과 같습니다.")
    return {"video": asset.model_dump(), "perception": {"proxy": {"width": proxy.width, "height": proxy.height, "pts_identical": proxy.pts_identical, "key": key}}}


def detect_track(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    services = ctx.services
    key = artifacts.key(STEP_VERSION, "detect_track", state["perception"]["proxy"]["key"], _inputs(ctx, "perceive/detect_track"))

    def compute() -> dict[str, Any]:
        times = load_times(asset)
        out = _dir(ctx, "perception")
        if services.is_fixture("perception.face_detector"):
            segments, tracks = services.fixture.tracks(asset, times)
        else:
            from . import perception

            segments = perception.find_segments(asset, times, services.profile)
            tracks = perception.detect_and_track(asset, times, segments, services.profile)
        _write(out / "segments.json", [s.model_dump() for s in segments])
        _write(out / "tracks.json", [t.model_dump() for t in tracks])
        return {"segments": len(segments), **{kind: sum(1 for t in tracks if t.kind == kind) for kind in ("face", "person", "plate")}}

    counts = _step(ctx, "detect_track", key, ["perception/segments.json", "perception/tracks.json"], compute)
    note("tracks", "perceive/detect_track", f"장면 {counts['segments']} · 얼굴 {counts['face']} · 전신 {counts['person']} · 번호판 {counts['plate']} 트랙")
    activity(f"장면 {counts['segments']}개에서 얼굴 {counts['face']}개, 사람 {counts['person']}명, 번호판 {counts['plate']}개의 움직임을 따라갔습니다.")
    return {"perception": {"detect": {**counts, "key": key}}}


def _tracks(ctx: CaseContext) -> list[Track]:
    return [Track.model_validate(t) for t in _read(ctx.workdir / "perception" / "tracks.json")]


def _segments(ctx: CaseContext) -> list[SegmentInfo]:
    return [SegmentInfo.model_validate(s) for s in _read(ctx.workdir / "perception" / "segments.json")]


def group_identities(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    services = ctx.services
    key = artifacts.key(STEP_VERSION, "group_identities", state["perception"]["detect"]["key"], _inputs(ctx, "perceive/group_identities"))

    def compute() -> dict[str, Any]:
        tracks = _tracks(ctx)
        if services.is_fixture("perception.face_embedding"):
            persons, ambiguities = services.fixture.persons(tracks)
        else:
            from . import perception

            persons, ambiguities = perception.group_identities(asset, tracks, services.profile)
        _write(ctx.workdir / "perception" / "persons.json", {"persons": [p.model_dump() for p in persons], "ambiguities": [a.model_dump() for a in ambiguities]})
        return {"count": len(persons), "uncertain": len(ambiguities)}

    found = _step(ctx, "group_identities", key, ["perception/persons.json"], compute)
    note("person_groups", "perceive/group_identities", f"인물 {found['count']} · 불확실한 묶음 {found['uncertain']}")
    return {"perception": {"persons": {**found, "key": key}}}


def describe_persons(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    key = artifacts.key(STEP_VERSION, "describe_persons", state["perception"]["persons"]["key"], _inputs(ctx, "perceive/describe_persons"))

    def compute() -> dict[str, Any]:
        from .domain import PersonGroup

        data = _read(ctx.workdir / "perception" / "persons.json")
        tracks = _tracks(ctx)
        vision = Vision(ctx.services, asset, tracks, _face_finder(ctx))
        persons = sorted((PersonGroup.model_validate(p) for p in data["persons"]), key=lambda p: -p.prominence)
        attributes: dict[str, Any] = {}
        for person in persons[:6]:  # the most prominent people; the rest keep observation-only attributes
            attributes[person.id] = vision.describe_person(person, tracks)
        _write(ctx.workdir / "perception" / "person_attributes.json", attributes)
        return {"described": len(attributes), "missing": sum(1 for value in attributes.values() if not value)}

    told = _step(ctx, "describe_persons", key, ["perception/person_attributes.json"], compute, keep=lambda update: not update["missing"])
    return {"perception": {"person_attributes": {"described": told["described"]}}}


def ocr(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    services = ctx.services
    key = artifacts.key(STEP_VERSION, "ocr", state["perception"]["detect"]["key"], _inputs(ctx, "perceive/ocr"))

    def compute() -> dict[str, Any]:
        times = load_times(asset)
        if services.is_fixture("perception.ocr"):
            text_tracks, texts = services.fixture.texts(asset, times)
        else:
            from . import perception

            text_tracks, texts = perception.recognize_text(asset, times, _segments(ctx), services.profile)
        _write(ctx.workdir / "perception" / "texts.json", {"tracks": [t.model_dump() for t in text_tracks], "texts": [t.model_dump() for t in texts]})
        return {"count": len(texts), "shown": " · ".join(f'"{t.text}" {t.confidence:.2f}' for t in texts[:4])}

    read = _step(ctx, "ocr", key, ["perception/texts.json"], compute)
    note("text_items", "perceive/ocr", f"글자 {read['count']}곳" + (f" · {read['shown']}" if read["shown"] else ""))
    return {"perception": {"texts": {"count": read["count"]}}}


def describe_segments(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    mode = ctx.services.part("vlm.input_mode").candidate.id
    key = artifacts.key(STEP_VERSION, "describe_segments", state["perception"]["detect"]["key"], _inputs(ctx, "perceive/describe_segments"))

    def compute() -> dict[str, Any]:
        vision = Vision(ctx.services, asset, _tracks(ctx), _face_finder(ctx))
        descriptions = {segment.id: vision.describe_segment(segment) for segment in _segments(ctx)}
        _write(ctx.workdir / "perception" / "segment_descriptions.json", descriptions)
        return {"described": len(descriptions), "missing": sum(1 for value in descriptions.values() if not value)}

    told = _step(ctx, "describe_segments", key, ["perception/segment_descriptions.json"], compute, keep=lambda update: not update["missing"])
    label = {"v1": "원본 프레임 전송", "v2": "얼굴을 흐리게 한 뒤 전송", "v3": "이 컴퓨터에서만 처리"}.get(mode, mode)
    note("segments", "perceive/describe_segments", f"장면 {told['described']} 설명 · {label}")
    return {"perception": {"segments": {"described": told["described"], "input_mode": mode}}}


def _face_finder(ctx: CaseContext) -> Any:
    """A low-threshold face detector for anonymising frames before they are sent (recall over precision)."""
    if ctx.services.is_fixture("perception.face_detector") or ctx.services.part("vlm.input_mode").candidate.id == "v1":
        return None
    from . import perception

    return perception.face_finder(ctx.services.profile)


def assemble(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    base = ctx.workdir / "perception"
    from .domain import IdentityAmbiguity, PersonGroup

    tracks = _tracks(ctx)
    grouped = _read(base / "persons.json")
    persons = [PersonGroup.model_validate(p) for p in grouped["persons"]]
    attributes = _read(base / "person_attributes.json")
    for person in persons:
        person.attributes = {**person.attributes, **(attributes.get(person.id) or {})}
    text_data = _read(base / "texts.json")
    text_tracks = [Track.model_validate(t) for t in text_data["tracks"]]
    texts = [TextItem.model_validate(t) for t in text_data["texts"]]
    descriptions = _read(base / "segment_descriptions.json")
    segments = _segments(ctx)
    for segment in segments:
        segment.description = descriptions.get(segment.id) or {}
        segment.label = str(segment.description.get("label_ko") or f"장면 {segment.id[1:]}")
    all_tracks = [*tracks, *text_tracks]
    if not ctx.services.is_fixture("perception.ocr"):
        from . import perception

        perception.attach_text(all_tracks, persons, texts)
    plates = [Plate(id=f"plate-{i}", tracks=[t.id]) for i, t in enumerate((t for t in tracks if t.kind == "plate"), start=1)]
    understanding = VideoUnderstanding(
        segments=segments,
        tracks=all_tracks,
        persons=persons,
        texts=texts,
        plates=plates,
        ambiguities=[IdentityAmbiguity.model_validate(a) for a in grouped["ambiguities"]],
        summary_ko=" ".join(str(s.description.get("summary_ko", "")) for s in segments).strip(),
        provenance={slot: ctx.services.part(slot).candidate.id for slot in ("perception.face_detector", "perception.object_detector", "perception.tracker", "perception.ocr", "vlm.scene", "vlm.input_mode")},
        sample_interval=1.0 / max(float(ctx.services.param("perception.analysis_fps", 12)), 0.1),
    )
    path = ctx.workdir / "understanding.json"
    views.save(views.with_coverage(understanding, asset), path)
    return _publish_understanding(understanding, path, asset, announce=True)


def _publish_understanding(understanding: VideoUnderstanding, path: Path, asset: VideoAsset, *, announce: bool) -> dict[str, Any]:
    targets = [t.wire() for t in views.build_targets(understanding, asset.meta)]
    segments = [{"id": s.id, "start": round(s.start, 2), "end": round(s.end, 2), "label": s.label} for s in understanding.segments]
    summary = views.perception_summary(understanding)
    patch(perception=summary, segments=segments, targets=targets)
    note("understanding", "perceive/assemble", f"장면 {summary['scenes']} · 인물 {summary['people']} · 번호판 {summary['plates']} · 글자 {summary['texts']} (원본 시간 기준)")
    if announce:
        activity(f"사람 {summary['people']}명, 번호판 {summary['plates']}개, 글자 {summary['texts']}곳, 장면 {summary['scenes']}개를 찾았습니다.", "done")
    return {"understanding": {"path": str(path), **summary, "ambiguities": len(understanding.ambiguities)}, "segments": segments, "targets": targets}


def build_perceive() -> Any:
    graph = StateGraph(CaseState, context_schema=CaseContext)
    graph.add_node("ingest_probe", ingest_probe)
    graph.add_node("make_proxy", proxy_step)
    graph.add_node("detect_track", detect_track)
    graph.add_node("group_identities", group_identities)
    graph.add_node("describe_persons", describe_persons)
    graph.add_node("ocr", ocr)
    graph.add_node("describe_segments", describe_segments)
    graph.add_node("assemble", assemble)
    graph.add_edge(START, "ingest_probe")
    graph.add_edge("ingest_probe", "make_proxy")
    graph.add_edge("make_proxy", "detect_track")
    graph.add_edge("detect_track", "group_identities")
    graph.add_edge("detect_track", "ocr")
    graph.add_edge("detect_track", "describe_segments")
    graph.add_edge("group_identities", "describe_persons")
    graph.add_edge(["describe_persons", "ocr", "describe_segments"], "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


# ---------- identity confirmation ----------


def route_identity(state: CaseState) -> Literal["await_identity", "legal_analyst"]:
    return "await_identity" if state["understanding"].get("ambiguities") else "legal_analyst"


def await_identity(state: CaseState, runtime: Ctx) -> Command[Literal["await_identity", "legal_analyst"]]:
    path = Path(state["understanding"]["path"])
    understanding = views.load(path)
    question = views.identity_question(understanding)
    if question is None:
        return Command(goto="legal_analyst")
    answer = str(interrupt({"payload": "identity", "summary": f"같은 사람인지 확인 1건 ({question.candidates[0].target} · {question.candidates[1].target})", "identity": question.wire()}))
    if runtime.context.services.is_fixture("perception.face_embedding"):
        updated = runtime.context.services.fixture.apply_identity(understanding, question.id, answer)
    else:
        from . import perception

        updated = perception.apply_identity_answer(understanding, question.id, answer)
    views.save(views.with_coverage(updated, _asset(state)), path)
    question.status, question.answer = "answered", answer
    patch(identity=question.wire())
    update = _publish_understanding(updated, path, _asset(state), announce=False)
    return Command(update={**update, "identity": question.wire()}, goto="await_identity" if updated.ambiguities else "legal_analyst")


# ---------- legal analyst ----------


def _merge_facts(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Later groups win, except that an inferred fact never replaces a declared or answered one."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for fact in group:
            current = merged.get(fact["key"])
            if current and fact["provenance"] == "inferred" and current["provenance"] != "inferred":
                continue
            merged[fact["key"]] = fact
    return list(merged.values())


def _brief(state: CaseState, understanding: VideoUnderstanding, facts: list[dict[str, Any]], seed: knowledge.Seed, services: Any) -> str:
    asset = _asset(state)
    limits = {name: int(services.param(f"analyst.{name}", default)) for name, default in (("max_tool_calls", 25), ("max_searches", 12), ("max_question_rounds", 2), ("max_questions", 6))}
    parts = [
        views.observation_text(understanding, state["targets"], asset.meta.duration_s),
        "[알려진 사실]\n" + ("\n".join(f"- {f['key']} = {f['value']} ({f['valueLabel']}) · {f['provenance']}" + (f" · 신뢰도 {f['confidence']:.2f}" if f.get("confidence") is not None else "") for f in facts) or "- 없음"),
        "[사실 키 참고 어휘] (질문을 만들 때 참고, 강제 아님)\n" + knowledge.fact_vocabulary(),
        f"[예산] 도구 호출 {limits['max_tool_calls']}회 · 검색 {limits['max_searches']}회 · 질문 {limits['max_question_rounds']}라운드(라운드당 {limits['max_questions']}개)",
    ]
    if seed.evidence:
        parts.append("[시드 문서] (항상 관련 있는 핵심 조문의 원문. 이 밖의 근거는 직접 검색)\n" + seed.as_prompt())
    else:
        parts.append("[시드 문서] 없음. 필요한 조문부터 검색하세요.")
    reeval = state.get("reeval")
    if reeval:
        previous = "\n".join(f"- {a['id']} [{a['applicability']}] {a['question']} (의존 사실: {', '.join(a['dependsOn']) or '없음'})" for a in state.get("analyses") or [])
        requirements = "\n".join(f"- {r['id']} {r['statement']} (대상 {', '.join(r['targets'])})" for r in state.get("requirements") or [])
        parts.append(f"[재평가] 사용자가 검토 중에 사실을 바꿨습니다: {reeval['reason']}\n바뀐 사실에 의존하는 분석만 다시 평가하고, 나머지는 그대로 두어 전체 결과를 finalize로 다시 제출하세요. 이번에는 사용자에게 질문할 수 없습니다.\n이전 분석:\n{previous or '- 없음'}\n이전 요건:\n{requirements or '- 없음'}")
    return "\n\n".join(parts)


def legal_analyst(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    services, asset = ctx.services, _asset(state)
    understanding = _understanding(state)
    facts = _merge_facts([f.wire() for f in [*views.inferred_facts(understanding), *views.file_facts(asset.meta)]], state.get("facts") or [])
    seeded = services.part("rag.seed").candidate.id != "disabled"
    seed = knowledge.load_seed(services.store) if seeded else knowledge.Seed([], {}, [])
    reeval = state.get("reeval")
    deps = AnalystDeps(
        services=services,
        understanding=understanding,
        asset=asset,
        vision=Vision(services, asset, understanding.tracks, _face_finder(ctx)),
        targets=state["targets"],
        times=load_times(asset),
        allow_questions=not reeval,
    )
    evidence = {**(state.get("evidence") or {}), **{e["id"]: e for e in seed.evidence}}
    limit = 4 * int(services.param("analyst.max_tool_calls", 25)) + 60
    result = build_analyst(deps).invoke(
        {"messages": [HumanMessage(_brief(state, understanding, facts, seed, services))], "evidence": evidence, "facts": facts, "questions": state.get("questions") or []},
        {"recursion_limit": limit},
    )
    evidence = result.get("evidence") or evidence
    analyses, requirements, unresolved = _to_contract(result.get("result") or {}, evidence, state["targets"], understanding, views.timeline(asset, understanding))
    facts, questions = result.get("facts") or facts, result.get("questions") or []
    patch(facts=facts, questions=questions, analyses=analyses, requirements=requirements)
    applies = sum(1 for a in analyses if a["applicability"] == "applies")
    uncertain = sum(1 for a in analyses if a["applicability"] == "uncertain")
    note("analyses", AGENT, f"분석 {len(analyses)}건 · 적용 {applies} · 불확실 {uncertain}")
    note("requirements", AGENT, " · ".join(r["id"] for r in requirements) or "요건 없음")
    activity(f"보호 요건 {len(requirements)}건을 정리했습니다." if not reeval else f"바뀐 사실을 반영해 다시 평가했습니다. 보호 요건 {len(requirements)}건.", "done")
    return {"facts": facts, "questions": questions, "evidence": evidence, "analyses": analyses, "requirements": requirements, "unresolved": unresolved, "reeval": None}


def _to_contract(result: dict[str, Any], evidence: dict[str, dict[str, Any]], targets: list[dict[str, Any]], understanding: VideoUnderstanding, timeline: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The agent's submission in the web contract's shape. A run that ended without a usable submission gets
    conservative requirements for everything personal, marked uncertain."""
    unresolved = list(result.get("unresolved") or [])
    analyses_in, requirements_in = result.get("analyses") or [], result.get("requirements") or []
    if result.get("forced") or not analyses_in:
        personal = [t for t in targets if t["personal"] and (t["ranges"] or t["kind"] == "metadata")]
        analyses_in = [{"id": "A-0", "question_of_law": "법적 분석을 끝내지 못했을 때의 보수적 처리", "applicability": "uncertain", "reasoning_ko": "분석이 예산 안에 완료되지 않아 근거를 확정하지 못했습니다.", "evidence_ids": [], "affected_targets": [t["id"] for t in personal], "depends_on_facts": [], "conservative_assumption": "개인을 알아볼 수 있는 모든 대상을 식별할 수 없게 처리"}]
        scope_of = {"person": "face", "text": "text", "plate": "plate", "metadata": "metadata"}
        requirements_in = [{"id": f"REQ-{i}", "statement_ko": f"분석이 완료되지 않아 {t['label']}을(를) 보수적으로 식별할 수 없게 처리 (불확실)", "targets": [t["id"]], "scope": scope_of[t["kind"]], "time_ranges": None, "derived_from_analysis_ids": ["A-0"], "evidence_ids": []} for i, t in enumerate(personal, start=1)]
        unresolved.append({"item": "법적 분석 미완료", "assumption": "개인을 알아볼 수 있는 모든 대상을 처리"})
    analyses = []
    for item in analyses_in:
        reasoning = item["reasoning_ko"] + (f" (보수적 가정: {item['conservative_assumption']})" if item.get("conservative_assumption") else "")
        analyses.append(Analysis(id=item["id"], question=item["question_of_law"], applicability=item["applicability"], reasoning=reasoning, citations=knowledge.citations_for(item.get("evidence_ids", []), evidence), depends_on=item.get("depends_on_facts", []), targets=item.get("affected_targets", [])).wire())
    requirements = []
    for item in requirements_in:
        visible = merge_ranges([r for target in item["targets"] for r in planner.visible_ranges(understanding, target, item["scope"])])
        stated = [(float(a), float(b)) for a, b in item.get("time_ranges") or []]
        # a stated range is the model's reading of the timeline ("0.0–9.9"): fit it to the frames it means
        ranges = clip_ranges(timeline.snap(stated, visible), visible) if stated and item["scope"] != "metadata" else visible
        cited = item.get("evidence_ids") or [e for a in analyses_in if a["id"] in item["derived_from_analysis_ids"] for e in a.get("evidence_ids", [])]
        requirements.append(Requirement(id=item["id"], statement=item["statement_ko"], targets=item["targets"], ranges=ranges, derived_from=item["derived_from_analysis_ids"], citations=knowledge.citations_for(cited, evidence), scope=item["scope"]).wire())
    return analyses, requirements, unresolved


# ---------- plan and review ----------


def plan_step(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    understanding = _understanding(state)
    requirements = state.get("requirements") or []
    activity(f"보호 요건 {len(requirements)}건을 처리 계획으로 바꾸고 있습니다.")
    version = (state.get("plan") or {}).get("version", 0) + 1
    plan = planner.make_plan(ctx.services, understanding, state["targets"], requirements, state.get("analyses") or [], state.get("facts") or [], version=version)
    wire = plan.wire()
    patch(plan=wire)
    note("plan", "plan", f"계획 v{plan.version} · 결정 {len(plan.decisions)}건 · 처리 {sum(1 for d in plan.decisions if d.method != 'keep')} · 미충족 {len(plan.unsatisfied)}")
    return {"plan": wire}


def review_guide(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    """What the user needs beside each decision. M4 assembles it from the requirements; real similar cases and
    model-written guidance arrive with FR-08 (M5)."""
    plan = state["plan"]
    statements = {r["id"]: r["statement"] for r in state.get("requirements") or []}
    rank = {"high": 0, "uncertain": 1, "medium": 2, "low": 3}
    decisions = []
    for decision in plan["decisions"]:
        linked = [statements[r] for r in decision["satisfies"] if r in statements]
        if decision["method"] == "keep":
            risk = "처리를 추가해도 요건에는 영향이 없지만, 가린 만큼 영상의 쓸모가 줄어듭니다."
        else:
            risk = "이 처리를 풀거나 구간을 줄이면 다음 요건을 충족하지 못합니다: " + " / ".join(linked) if linked else "연결된 요건이 없는 처리입니다."
        decisions.append({**decision, "riskIfChanged": risk})
    decisions.sort(key=lambda d: (d["method"] == "keep", rank.get(d["risk"], 2)))
    wire = {**plan, "decisions": decisions}
    patch(plan=wire)
    note("review_guide", "review_guide", "결정별 근거 · 수정 시 위험 (유사 사례와 모델 안내는 M5)")
    return {"plan": wire}


def await_review(state: CaseState, runtime: Ctx) -> Command[Literal["auditor", "legal_analyst"]]:
    plan = state["plan"]
    deferred = sum(1 for q in state.get("questions") or [] if q["status"] == "deferred")
    reply = interrupt({"payload": "review", "summary": f"결정 {len(plan['decisions'])}건 · 미답 질문 {deferred}건"}) or {}
    action = reply.get("action", "approve")
    if action == "facts":
        facts = {f["key"]: f for f in state.get("facts") or []}
        changed = []
        for item in reply.get("facts") or []:
            current = facts.get(item["key"])
            options = (current or {}).get("options") or []
            label = next((o["label"] for o in options if o["value"] == item["value"]), item["value"])
            facts[item["key"]] = Fact(key=item["key"], label=(current or {}).get("label", item["key"]), value=item["value"], value_label=label, provenance="answered", source="사용자 수정", editable=True, options=options or None).wire()
            changed.append(f"{item['key']}={item['value']}")
        wire = list(facts.values())
        patch(facts=wire)
        note("facts", "await_review", " · ".join(changed) + " (사용자 수정)")
        return Command(update={"facts": wire, "reeval": {"reason": "사실 수정 " + ", ".join(changed)}, "edits": [*(state.get("edits") or []), *[f"사실 수정 {c}" for c in changed]]}, goto="legal_analyst")
    if action == "answers":
        from .agents.analyst import apply_answers
        from .domain import Question

        answers = reply.get("answers") or {}
        questions = [Question.model_validate(q) for q in state.get("questions") or []]
        late, facts = apply_answers([q for q in questions if q.id in answers and q.status == "deferred"], answers)
        merged = _merge_facts(state.get("facts") or [], [f.wire() for f in facts])
        wire = [q.wire() for q in questions]
        patch(facts=merged, questions=wire)
        reason = "늦은 답변 " + ", ".join(f"{q.id}={q.answer}" for q in late)
        note("facts", "await_review", reason)
        return Command(update={"facts": merged, "questions": wire, "reeval": {"reason": reason}, "edits": [*(state.get("edits") or []), reason]}, goto="legal_analyst")
    incoming = {d["id"]: d for d in reply.get("decisions") or []}
    edits: list[str] = []
    decisions = []
    understanding = _understanding(state)
    timeline = views.timeline(_asset(state), understanding)
    kinds = {t["id"]: t["kind"] for t in state["targets"]}
    for decision in plan["decisions"]:
        new = incoming.get(decision["id"])
        if not new:
            decisions.append(decision)
            continue
        kind = kinds.get(decision["targetId"], "person")
        method = new.get("method", decision["method"])
        if method not in ("keep", REDACT_METHOD[kind]):
            method = decision["method"]  # the only choices are to keep the target or to redact it its one way
        ranges = decision["ranges"]
        if kind != "metadata" and "ranges" in new:
            # what the reviewer typed, fitted to frames; a boundary next to where the target appears or disappears means that moment
            ranges = timeline.snap([(float(a), float(b)) for a, b in new["ranges"]], planner.visible_ranges(understanding, decision["targetId"], decision["scope"]))
        shape = (new.get("shape") or decision.get("shape")) if kind != "metadata" else None
        checked = Decision.model_validate({**decision, "method": method, "shape": shape, "ranges": ranges}).wire()
        parts = [
            f"처리 {METHOD_LABEL[decision['method']]}→{METHOD_LABEL[checked['method']]}" if checked["method"] != decision["method"] else "",
            f"형태 {SHAPE_LABEL.get(decision.get('shape'), '없음')}→{SHAPE_LABEL.get(checked.get('shape'), '없음')}" if checked.get("shape") != decision.get("shape") else "",
            "구간 수정" if [list(r) for r in checked["ranges"]] != [list(r) for r in decision["ranges"]] else "",
        ]
        parts = [p for p in parts if p]
        if parts:
            edits.append(f"검토 변경 {decision['id']} {decision['targetLabel']}: {', '.join(parts)}")
            checked.update(source="user", edited=True)
        decisions.append(checked)
    wire = {**plan, "version": plan["version"] + (1 if edits else 0), "decisions": decisions}
    if edits:
        patch(plan=wire)
        note("plan", "await_review", f"계획 v{wire['version']} · 사용자 수정 {len(edits)}건")
    return Command(update={"plan": wire, "edits": [*(state.get("edits") or []), *edits]}, goto="auditor")


# ---------- audit ----------


def auditor(state: CaseState, runtime: Ctx) -> Command[Literal["await_audit_confirm", "render_original"]]:
    """M4: the deterministic part of the audit — does the reviewed plan still meet every requirement?
    The independent model with its own tools (FR-09) is added in M5."""
    ctx = runtime.context
    if ctx.services.part("llm.auditor").candidate.adapter == "none":
        activity("이 프로필은 감사를 생략합니다. 검토가 끝난 계획으로 바로 렌더합니다.", "warn")
        return Command(update={"findings": []}, goto="render_original")
    understanding = _understanding(state)
    plan, requirements = state["plan"], state.get("requirements") or []
    excused = {u["requirementId"] for u in plan.get("unsatisfied", [])}
    labels = {t["id"]: t["label"] for t in state["targets"]}
    findings: list[dict[str, Any]] = []
    fixes: dict[str, Any] = {}
    for requirement in requirements:
        if requirement["id"] in excused:
            continue
        for target in requirement["targets"]:
            if planner.coverage_gap(requirement, target, plan["decisions"], understanding) is None:
                continue
            needed = planner.required_ranges(requirement, understanding, target)
            identifier = f"F-{len(findings) + 1}"
            findings.append(
                Finding(
                    id=identifier,
                    type="requirement_unmet",
                    severity="high",
                    title=f"요건 {requirement['id']}을(를) 충족하지 못합니다",
                    detail=f"{requirement['statement']} — 검토에서 바뀐 계획이 " + (f"{labels.get(target, target)}을(를) 파일에서 삭제하지 않습니다." if requirement["scope"] == "metadata" else f"{labels.get(target, target)}의 필요한 구간을 처리하지 않습니다."),
                    target_id=target,
                    target_label=labels.get(target, target),
                    ranges=needed,
                    citations=requirement["citations"],
                    recommendation="파일에서 이 정보를 삭제합니다." if requirement["scope"] == "metadata" else "필요한 구간 전체를 식별할 수 없게 처리합니다.",
                ).wire()
            )
            fixes[identifier] = {"target": target, "scope": requirement["scope"], "requirement": requirement["id"], "ranges": needed}
    patch(findings=findings)
    note("findings", "auditor", " · ".join(f"{f['id']}({f['severity']})" for f in findings) or "발견 없음 (요건 대조)")
    if not findings:
        activity("요건 대조 감사를 통과했습니다.", "done")
        return Command(update={"findings": [], "finding_fixes": {}}, goto="render_original")
    activity(f"감사에서 확인할 사항 {len(findings)}건이 나왔습니다.", "wait")
    return Command(update={"findings": findings, "finding_fixes": fixes}, goto="await_audit_confirm")


def await_audit_confirm(state: CaseState, runtime: Ctx) -> Command[Literal["await_review", "await_audit_confirm", "render_original"]]:
    findings = state.get("findings") or []
    rounds = state.get("audit_rounds", 0)
    # going back to the review is offered only while rounds are left; the screen and the API read this flag
    can_return = rounds < int(runtime.context.services.param("auditor.max_rounds", 1))
    reply = interrupt({"payload": "audit", "summary": f"발견 {len(findings)}건", "canReturn": can_return}) or {}
    if reply.get("backToReview"):
        if can_return:
            return Command(update={"audit_rounds": rounds + 1}, goto="await_review")
        return Command(goto="await_audit_confirm")  # not offered: ask again rather than read it as "apply everything"
    resolutions = {r["findingId"]: r for r in reply.get("resolutions") or []}
    plan = state["plan"]
    decisions = [dict(d) for d in plan["decisions"]]
    resolved = []
    edits: list[str] = []
    for finding in findings:
        choice = resolutions.get(finding["id"], {"action": "apply"})
        if choice.get("action") == "reject":
            resolved.append({**finding, "status": "rejected", "rejectReason": choice.get("reason") or ""})
            edits.append(f"감사 {finding['id']} 위험 수용: {choice.get('reason') or '사유 없음'}")
            continue
        fix = (state.get("finding_fixes") or {}).get(finding["id"])
        if fix:
            _apply_fix(decisions, fix, finding)
        resolved.append({**finding, "status": "applied"})
    wire = {**plan, "version": plan["version"] + 1, "decisions": decisions}
    patch(plan=wire, findings=resolved)
    applied = sum(1 for f in resolved if f["status"] == "applied")
    note("plan", "await_audit_confirm", f"계획 v{wire['version']} · 감사 권고 적용 {applied} · 위험 수용 {len(resolved) - applied}")
    return Command(update={"plan": wire, "findings": resolved, "edits": [*(state.get("edits") or []), *edits]}, goto="render_original")


def _apply_fix(decisions: list[dict[str, Any]], fix: dict[str, Any], finding: dict[str, Any]) -> None:
    """Restore the redaction a requirement needs on one target."""
    scope = fix["scope"]
    method = "remove" if scope == "metadata" else "mosaic"
    for decision in decisions:
        if decision["targetId"] == fix["target"] and decision["scope"] == scope:
            if decision["method"] == "keep":
                decision.update(method=method, ranges=[list(r) for r in fix["ranges"]])
            else:
                decision["ranges"] = [list(r) for r in merge_ranges([*[tuple(r) for r in decision["ranges"]], *fix["ranges"]])]
            decision["satisfies"] = sorted({*decision["satisfies"], fix["requirement"]})
            decision.update(source="auditor", edited=True)
            return
    decisions.append(
        Decision(id=f"D-{len(decisions) + 1}", target_id=fix["target"], target_label=finding["targetLabel"], scope=scope, method=method, shape=planner.DEFAULT_SHAPE.get(scope), ranges=fix["ranges"], satisfies=[fix["requirement"]], rationale=finding["recommendation"], citations=finding["citations"], risk="high", source="auditor", edited=True).wire()
    )


# ---------- render, verify, report ----------

RENDER_STEPS = ["원본 디코드", "프레임 수정 · 재인코딩", "스트림 복사 · 마무리", "출력 검증"]


def _steps(active: int, detail: str | None = None) -> list[dict[str, Any]]:
    out = []
    for index, label in enumerate(RENDER_STEPS):
        step: dict[str, Any] = {"label": label, "state": "done" if index < active else "running" if index == active else "pending"}
        if detail and index == active - 1:
            step["detail"] = detail
        out.append(step)
    return out


def _decisions(state: CaseState) -> list[Decision]:
    return [Decision.model_validate(d) for d in state["plan"]["decisions"]]


def render_original(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    from .render import encoder_for, render

    ctx, asset = runtime.context, _asset(state)
    understanding = _understanding(state)
    decisions = _decisions(state)
    regions = build_regions(understanding, decisions, views.timeline(asset, understanding), width=asset.meta.display_width, height=asset.meta.display_height)
    for frame, extra in regions_from_json(state.get("repairs") or {}).items():
        regions.setdefault(frame, []).extend(extra)
    _write(ctx.workdir / "regions.json", regions_to_json(regions))
    # the one thing the plan can remove from the file itself: where it was shot
    dropped = asset.meta.location_tags if any(d.method == "remove" and d.target_id == views.LOCATION_TARGET for d in decisions) else []
    source = Path(asset.meta.path)
    stem = Path(asset.meta.filename).stem
    output = _dir(ctx, "output") / f"{stem}.privframe{source.suffix or '.mp4'}"
    encoder = encoder_for(ctx.services.part("render.encoder").candidate.id, asset.meta)
    round_number = state.get("repair_rounds", 0)
    activity("원본 스트림에 처리 결과를 입히고 있습니다." if not round_number else f"검증에서 드러난 자리를 더 가려 다시 렌더합니다 ({round_number}회차).")
    seen = {"step": -1}

    def progress(text: str, fraction: float) -> None:
        step = 0 if fraction < 0.05 else 1 if fraction < 0.95 else 2
        if step != seen["step"]:
            seen["step"] = step
            patch(renderSteps=_steps(step))
            note("render_progress", "render_original", text)

    result = render(asset.meta, output, regions, encoder, drop_tag=is_location_tag if dropped else None, progress=progress)
    patch(renderSteps=_steps(3))
    info = {"path": str(output), "filename": output.name, "sizeBytes": result.size_bytes, "codec": f"{asset.meta.codec.upper()} {asset.meta.profile} · {result.codec_label}".strip(), "frames": result.frames, "modifiedFrames": result.modified_frames, "seconds": round(result.seconds, 1), "droppedTags": dropped}
    removed = f" · 위치 태그 {len(dropped)}개 삭제" if dropped else ""
    note("output", "render_original", f"{result.frames:,}프레임 중 {result.modified_frames:,}프레임 수정 · {result.codec_label} · {result.seconds:.1f}초{removed}")
    return {"output": info}


def verify_output(state: CaseState, runtime: Ctx) -> Command[Literal["render_original", "report"]]:
    from .verify import passed, verify

    ctx, asset = runtime.context, _asset(state)
    understanding = _understanding(state)
    decisions = _decisions(state)
    timeline = views.timeline(asset, understanding)
    regions = regions_from_json(_read(ctx.workdir / "regions.json"))
    if ctx.services.is_fixture("perception.face_detector"):
        checker = ctx.services.fixture.checker(state.get("repair_rounds", 0), decisions, asset.meta.display_width, asset.meta.display_height)
    else:
        from . import perception

        checker = perception.output_checker(ctx.services.profile, asset, understanding, decisions, timeline)
    rows = verify(asset.meta, Path(state["output"]["path"]), regions, expected=expected_frames(understanding, decisions, timeline), checker=checker, dropped_tags=state["output"].get("droppedTags") or [])
    wire = [r.wire() for r in rows]
    ok = passed(rows)
    good = sum(1 for r in rows if r.verdict != "fail")
    note("verification", "verify_output", " · ".join(f"{r.check}: {r.verdict}" for r in rows))
    rounds = state.get("repair_rounds", 0)
    leaks = list(checker.leaks) if checker is not None else []
    if leaks and rounds < MAX_REPAIRS:
        repairs = _repairs(leaks, decisions, timeline, asset, state.get("repairs") or {})
        frames = len({leak.frame for leak in leaks})
        activity(f"출력의 {frames}개 프레임에서 가려야 할 대상이 보여, 그 자리를 더 가려 다시 렌더합니다.", "warn")
        note("repairs", "verify_output", f"노출 {len(leaks)}곳 · {frames}프레임 → 보정 {rounds + 1}회차")
        patch(renderSteps=_steps(1))
        return Command(update={"verification": wire, "repairs": repairs, "repair_rounds": rounds + 1}, goto="render_original")
    patch(renderSteps=_steps(4, f"{good} / {len(rows)} 통과"))
    activity(f"출력 검증 {len(rows)}항목을 모두 통과했습니다." if ok else "출력 검증에 실패한 항목이 있습니다: " + ", ".join(r.check for r in rows if r.verdict == "fail"), "done" if ok else "warn")
    return Command(update={"verification": wire}, goto="report")


def _repairs(leaks: list[Any], decisions: list[Decision], timeline: Any, asset: VideoAsset, earlier: dict[str, list[list[Any]]]) -> dict[str, list[list[Any]]]:
    """Regions that cover what the output check still recognised, added to the ones already asked for. Each is
    held for the timeline's padding around the frames it was seen on."""
    by_id = {d.id: d for d in decisions}
    out = regions_from_json(earlier)
    width, height = asset.meta.display_width, asset.meta.display_height
    for leak in leaks:
        decision = by_id.get(leak.ref)
        if decision is None:
            continue
        x, y, w, h = scale_box(leak.box, SPATIAL_SCALE.get(decision.scope, 1.1) * 1.15, width, height)  # a little wider than a tracked box: this one is a single sighting
        region = Region(x, y, w, h, render_shape(decision), decision.id, STRENGTH.get(decision.scope, 1.0))
        first, last = max(0, leak.span[0]), min(timeline.count - 1, leak.span[1])
        while first > 0 and timeline.times[leak.span[0]] - timeline.times[first - 1] <= timeline.pad:
            first -= 1
        while last < timeline.count - 1 and timeline.times[last + 1] - timeline.times[leak.span[1]] <= timeline.pad:
            last += 1
        for frame in range(first, last + 1):
            out.setdefault(frame, []).append(region)
    return regions_to_json(out)


def report_step(state: CaseState, runtime: Ctx) -> dict[str, Any]:
    ctx, asset = runtime.context, _asset(state)
    elapsed = ctx.services.elapsed()
    dropped = state["output"].get("droppedTags") or []
    built = reporting.assemble(state=dict(state), elapsed_sec=elapsed, cost_usd=ctx.services.cost.spent, output=state["output"], verification=state["verification"], has_audio=asset.meta.audio != "없음", location_tags=asset.meta.location_tags, removed_tags=dropped)
    wire = built.wire()
    _write(ctx.workdir / "report.json", wire)
    (ctx.workdir / "report.html").write_text(reporting.to_html(asset.meta.filename, wire, state["plan"], state.get("requirements") or []), encoding="utf-8")
    patch(report=wire)
    note("report", "report", f"초 단위 스크립트 {len(built.script)}행 · 인용 {len(built.citations)}건")
    return {"report": wire}


def build_case_graph(checkpointer: Any = None) -> Any:
    graph = StateGraph(CaseState, context_schema=CaseContext)
    graph.add_node("ingest", ingest)
    graph.add_node("perceive", build_perceive())
    graph.add_node("await_identity", await_identity)
    graph.add_node("legal_analyst", legal_analyst)
    graph.add_node("plan", plan_step)
    graph.add_node("review_guide", review_guide)
    graph.add_node("await_review", await_review)
    graph.add_node("auditor", auditor)
    graph.add_node("await_audit_confirm", await_audit_confirm)
    graph.add_node("render_original", render_original)
    graph.add_node("verify_output", verify_output)
    graph.add_node("report", report_step)
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "perceive")
    graph.add_conditional_edges("perceive", route_identity, ["await_identity", "legal_analyst"])
    graph.add_edge("legal_analyst", "plan")
    graph.add_edge("plan", "review_guide")
    graph.add_edge("review_guide", "await_review")
    graph.add_edge("render_original", "verify_output")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)
