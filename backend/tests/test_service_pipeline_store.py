"""PipelineStore: the Lab's view of configs/pipeline (catalog, profiles, service pointer).

Every test works on a copy under tmp_path; the repository files are only read. Expected hashes, messages and
file texts are the ones frontend/mock/pipeline.ts produces for the same input.
"""
from conftest import LAB_HASHES, PIPELINE
import difflib
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from privframe.core import pipeline as core
from privframe.service import openrouter
from privframe.service.pipeline_store import PipelineError, PipelineStore, _scalar

PROFILE_IDS = sorted(LAB_HASHES)
MISTRAL = {
    "slot": "llm.analyst", "adapter": "openrouter", "ref": "mistralai/mistral-large-3",
    "label": "Mistral Large 3", "price": {"in": 2, "out": 6}, "caps": ["tools", "json", "image"],
    "tier": "frontier", "note": "단가: 2026-11-11 OpenRouter 목록 (R4)",
}
MINI_CATALOG = """# a hand-written catalog
adapters:
  local-model: {label: Local, kind: local, ref: "name"}
stages:
  - id: only
    label: Only
    slots:
      - id: a.part
        label: Part
        adapters: [local-model]
        default: first
        candidates: []   # none yet

params: []
"""


@pytest.fixture
def store(tmp_path: Path) -> PipelineStore:
    shutil.copytree(PIPELINE, tmp_path / "pipeline")
    return PipelineStore(tmp_path / "pipeline", "configs/pipeline")


def refusal(call, *args) -> tuple[int, str]:
    with pytest.raises(PipelineError) as caught:
        call(*args)
    return caught.value.status, caught.value.message


def line_changes(before: bytes, after: bytes) -> tuple[list[str], list[str]]:
    """Lines that disappeared and lines that appeared."""
    diff = list(difflib.ndiff(before.decode().split("\n"), after.decode().split("\n")))
    return [d[2:] for d in diff if d.startswith("- ")], [d[2:] for d in diff if d.startswith("+ ")]


def spec_of(store: PipelineStore, profile_id: str = "baseline-v2", **changes) -> dict:
    spec = json.loads(json.dumps(store.get_profile(profile_id)["spec"]))
    spec["selections"].update(changes.pop("selections", {}))
    spec["params"].update(changes.pop("params", {}))
    return {**spec, **changes}


def hash_the_pipeline_reads(store: PipelineStore, profile_id: str) -> str:
    return core.resolve(core.load_catalog(store.dir), core.load_profile(store.dir, profile_id)).config_hash


# ---------- reading ----------

def test_catalog_has_the_contract_shape(store: PipelineStore):
    catalog = store.catalog()
    assert json.loads(json.dumps(catalog)) == catalog
    assert list(catalog) == ["path", "modifiedAt", "adapters", "stages", "params"]
    assert catalog["path"] == "configs/pipeline/catalog.yaml" and catalog["modifiedAt"].endswith("Z")
    assert catalog["adapters"][0] == {
        "id": "openrouter", "label": "OpenRouter", "kind": "api", "ref": "OpenRouter 모델 slug",
        "example": "anthropic/claude-sonnet-5.5", "lookup": "openrouter", "addable": True,
    }
    assert catalog["adapters"][1] == {  # Claude on the Anthropic API itself: no public list to look a model up in
        "id": "anthropic", "label": "Anthropic API (직접)", "kind": "api", "ref": "Claude 모델 ID",
        "example": "claude-sonnet-5-5", "addable": True,
    }
    assert catalog["adapters"][-1] == {
        "id": "none", "label": "사용 안 함", "kind": "option", "ref": "", "addable": False,
    }
    assert [s["id"] for st in catalog["stages"] for s in st["slots"]] == [s["id"] for s in store.slots()]
    slot_keys = ["id", "stage", "label", "usedBy", "adapters", "requires", "default", "candidates"]
    for stage in catalog["stages"]:
        assert list(stage) == ["id", "label", "description", "slots"]
        for slot in stage["slots"]:
            assert list(slot) == slot_keys and slot["stage"] == stage["id"]
            assert slot["default"] in [c["id"] for c in slot["candidates"]]
            for candidate in slot["candidates"]:
                assert {"id", "label", "adapter", "caps", "source"} <= set(candidate)
                assert None not in candidate.values()  # left out, not null: JSON.stringify drops undefined
    scene = store.slot("vlm.scene")
    assert scene["requires"] == ["json"] and scene["usedBy"] == ["perceive/describe_segments"]
    assert scene["candidates"][0] == {
        "id": "qwen3.6-35b-a3b", "label": "Qwen3.6-35B-A3B", "adapter": "openrouter",
        "ref": "qwen/qwen3.6-35b-a3b", "license": "Apache-2.0", "price": {"in": 0.1, "out": 0.9},
        "caps": ["tools", "json", "image", "video"], "note": "Video-MME 86.6 · OpenRouter video_url 입력 (R1)",
        "source": "file",
    }
    planned = next(c for c in store.slot("agent.strategy")["candidates"] if c["id"] == "naive-rag")
    assert planned["status"] == "planned"
    cost_cap = catalog["params"][-1]
    assert list(cost_cap) == ["id", "stage", "label", "unit", "default", "min", "max", "step"]
    assert cost_cap == {
        "id": "case.cost_cap_usd", "stage": "agents", "label": "케이스 비용 상한 (넘으면 멈춤 · 0이면 상한 없음)",
        "unit": "USD", "default": 1, "min": 0, "max": 20, "step": 0.25,
    }
    # 1.00 and 20.00 in the file; JavaScript sends 1 and 20, not 1.0 and 20.0
    assert (json.dumps(cost_cap["default"]), json.dumps(cost_cap["max"])) == ("1", "20")
    assert refusal(store.slot, "nope") == (404, "카탈로그에 'nope' 부품이 없습니다.")
    # without a display name the repository's own folder is shown relative to the repository
    assert PipelineStore(PIPELINE).display(PIPELINE / "catalog.yaml") == "configs/pipeline/catalog.yaml"


def test_profiles_resolve_to_the_hashes_the_lab_shows(store: PipelineStore):
    profiles = store.list_profiles()
    assert [p["spec"]["id"] for p in profiles] == PROFILE_IDS
    for profile in profiles:
        spec = profile["spec"]
        assert profile["path"] == f"configs/pipeline/profiles/{spec['id']}.yaml"
        assert profile["source"] == "file" and profile["updatedAt"].endswith("Z")
        resolved = store.resolve(spec)
        assert resolved["configHash"] == LAB_HASHES[spec["id"]]
        assert [c["slot"] for c in resolved["components"]] == [s["id"] for s in store.slots()]
        assert resolved["params"] == spec["params"]
    assert "base" not in profiles[0]["spec"] and profiles[1]["spec"]["base"] == "baseline-v2"
    caps = {p["spec"]["id"]: p["spec"]["params"]["case.cost_cap_usd"] for p in profiles}
    assert caps == {"baseline-v2": 1, "budget-saver": 0.3, "claude-api": 3, "local-only": 1, "research-max": 3}
    assert store.service() == {"default": "baseline-v2", "local_only": "local-only"}
    assert refusal(store.get_profile, "nope") == (404, "프로필 'nope'을(를) 찾을 수 없습니다.")


def test_resolved_parts_carry_what_a_run_needs(store: PipelineStore):
    spec = spec_of(store)
    resolved = store.resolve(spec)
    assert resolved["components"][0] == {
        "slot": "perception.scene_cut", "stage": "perception", "slotLabel": "장면 경계",
        "candidate": "adaptive", "label": "PySceneDetect AdaptiveDetector", "short": "Adaptive 컷",
        "kind": "local", "usedBy": ["perceive/detect_track", "perceive/describe_segments"],
    }
    assert resolved["part"]["llm.analyst"] == {
        "slot": "llm.analyst", "stage": "agents", "slotLabel": "Legal Analyst",
        "candidate": "claude-sonnet-5.5", "label": "Claude Sonnet 5.5", "short": "Claude Sonnet 5.5",
        "kind": "api", "tier": "frontier", "usedBy": ["legal_analyst/model"], "adapter": "openrouter",
        "ref": "anthropic/claude-sonnet-5.5", "price": {"in": 2, "out": 10},
        "caps": ["tools", "json", "image"], "planned": False, "vendor": "anthropic",
    }
    assert resolved["part"]["rag.embedding"]["lawirko"] == 0.743
    swapped = store.resolve(spec, {"llm.analyst": "gemini-3.8-flash"})
    assert swapped["part"]["llm.analyst"]["tier"] == "efficient"
    assert swapped["configHash"] != resolved["configHash"]
    draft = store.resolve({"id": "draft", "name": "draft", "selections": {"vlm.scene": "nope"}, "params": {}})
    assert [c["candidate"] for c in draft["components"]] == [s["default"] for s in store.slots()]

    assert store.frames_leave_device(spec) is True
    assert store.api_local_counts(spec) == {"api": 8, "local": 13}
    local = spec_of(store, "local-only")
    assert store.frames_leave_device(local) is False
    assert store.api_local_counts(local) == {"api": 5, "local": 16}


def test_issues_use_the_levels_and_wording_of_the_lab(store: PipelineStore):
    assert store.issues(spec_of(store)) == []
    broken = spec_of(
        store,
        selections={
            "vlm.input_mode": "v3", "llm.auditor": "claude-opus-5.5", "agent.strategy": "naive-rag",
            "vlm.scene": "glm-4.6v", "perception.object_detector": "yolo26n", "rag.retrieval": "dense",
            "agent.questions": "disabled", "rag.seed": "disabled", "perception.face_detector": "nope",
        },
        params={"rag.top_k": 99},
    )
    assert [(i["level"], i.get("slot"), i["text"]) for i in store.issues(broken)] == [
        ("error", "perception.face_detector", "'얼굴 검출'의 후보 'nope'이(가) 카탈로그에 없습니다."),
        ("warn", "perception.object_detector",
         "'YOLO26n' 라이선스(AGPL-3.0)는 서비스 전환 전에 검토해야 합니다."),
        ("error", "vlm.scene",
         "'장면 설명'에는 엄격한 JSON 출력 기능이 필요한데 'GLM-4.6V'은(는) 지원하지 않습니다."),
        ("error", "agent.strategy",
         "'계획 전략'의 '단순 RAG (1회 검색 · 고정 질문)'은(는) 아직 구현되지 않았습니다."),
        ("error", "vlm.input_mode",
         "V-3(로컬만)인데 장면 설명, 인물 속성, 시각 재점검 (inspect_visual)이(가) 외부 API입니다."),
        ("warn", "llm.auditor", "감사자가 Legal Analyst와 같은 회사 모델이라 독립성이 약해집니다."),
        ("warn", "agent.questions",
         "동적 질문을 끄면 모르는 사실을 보수적으로 가정해 과잉 처리가 늘 수 있습니다."),
        ("info", "rag.bm25_tokenizer", "dense만 쓰므로 BM25 토크나이저는 쓰이지 않습니다."),
        ("info", "rag.seed", "시드 묶음 없이 핵심 조문부터 검색하므로 검색 호출이 늘어납니다."),
        ("error", None, "검색 결과 수 (k)은(는) 3~20 사이여야 합니다."),
    ]
    partial = spec_of(store, selections={"vlm.input_mode": "v1", "llm.auditor": "none"})
    del partial["selections"]["rag.seed"]
    partial["params"] = {}
    assert store.issues(partial) == [
        {"level": "info", "slot": "rag.seed",
         "text": "카탈로그에 새로 생긴 부품 '시드 묶음' — 기본 후보를 씁니다."},
        {"level": "warn", "slot": "vlm.input_mode",
         "text": "V-1은 원본 프레임을 외부로 보냅니다. 동의받은 영상이나 합성 영상에만 쓰세요."},
        {"level": "warn", "slot": "llm.auditor",
         "text": "감사를 생략하면 문신 같은 잔여 식별자를 놓칠 수 있습니다."},
    ]


def test_hand_edits_show_up_without_a_restart(store: PipelineStore):
    assert "by-hand" not in [c["id"] for c in store.slot("rag.store")["candidates"]]
    text = store.catalog_file.read_text(encoding="utf-8")
    anchor = "            note: dense + sparse를 서버에서 RRF 결합. 한국어 BM25 토큰은 직접 넣어야 한다 (R3)\n"
    entry = "          - id: by-hand\n            label: By hand\n            adapter: vector-store\n"
    assert anchor in text
    store.catalog_file.write_text(text.replace(anchor, anchor + entry), encoding="utf-8")
    assert store.slot("rag.store")["candidates"][-1] == {
        "id": "by-hand", "label": "By hand", "adapter": "vector-store", "caps": [], "source": "file",
    }

    assert store.service()["default"] == "baseline-v2"
    shutil.copy(store.profiles_dir / "budget-saver.yaml", store.profiles_dir / "dropped-in.yaml")
    store.service_file.write_text("default: budget-saver\n", encoding="utf-8")
    assert store.service() == {"default": "budget-saver", "local_only": "budget-saver"}
    assert len(store.list_profiles()) == len(PROFILE_IDS) + 1

    (store.profiles_dir / "broken.yaml").write_text("selections: [unclosed\n", encoding="utf-8")
    assert len(store.list_profiles()) == len(PROFILE_IDS) + 1  # an unreadable profile is skipped, the rest still load
    store.catalog_file.write_text("stages: [unclosed\n", encoding="utf-8")
    status, message = refusal(store.catalog)
    assert status == 500
    assert message.startswith("카탈로그 파일을 읽을 수 없습니다 (configs/pipeline/catalog.yaml)")


# ---------- candidates ----------

def test_adding_a_candidate_appends_lines_and_removing_it_restores_the_file(store: PipelineStore):
    before = store.catalog_file.read_bytes()
    today = datetime.now(UTC).date().isoformat()
    added = store.add_candidate(MISTRAL)
    assert added == {
        "id": "mistral-large-3", "label": "Mistral Large 3", "adapter": "openrouter",
        "ref": "mistralai/mistral-large-3", "price": {"in": 2, "out": 6}, "caps": ["tools", "json", "image"],
        "tier": "frontier", "note": "단가: 2026-11-11 OpenRouter 목록 (R4)", "source": "lab", "addedAt": today,
    }
    assert store.slot("llm.analyst")["candidates"][-1] == added
    after = store.catalog_file.read_bytes()
    assert line_changes(before, after) == ([], [
        "          - id: mistral-large-3",
        "            label: Mistral Large 3",
        "            adapter: openrouter",
        "            ref: mistralai/mistral-large-3",
        "            price: {in: 2, out: 6}",
        "            caps: [tools, json, image]",
        "            tier: frontier",
        '            note: "단가: 2026-11-11 OpenRouter 목록 (R4)"',
        "            source: lab",
        f"            added: {today}",
    ])
    # the entry closes the part's list: right after the last hand-written candidate, before the blank line
    above, below = after.decode().split("          - id: mistral-large-3\n")
    assert above.endswith("note: Anthropic API 직접 호출 (ANTHROPIC_API_KEY). 프롬프트 10만 토큰 이하 단가.\n")
    assert below.split(f"added: {today}\n")[1].startswith("\n      - id: llm.planner\n")

    store.remove_candidate("llm.analyst", "mistral-large-3")
    assert store.catalog_file.read_bytes() == before


@pytest.mark.parametrize(
    "slot_id", ["perception.scene_cut", "perception.ocr", "vlm.scene", "rag.embedding", "render.encoder"]
)
def test_add_then_remove_is_byte_identical_wherever_the_part_sits(store: PipelineStore, slot_id: str):
    before = store.catalog_file.read_bytes()
    adapter = next(a for a in store.catalog()["adapters"] if a["id"] == store.slot(slot_id)["adapters"][0])
    request = {
        "slot": slot_id, "adapter": adapter["id"], "ref": "Some-Org/New_Model.v2", "label": "New: model #2",
        "caps": ["tools", "json", "image", "video"], "license": "MIT", "note": '"quoted" start',
        **({"price": {"in": 0.1, "out": 0.4}} if adapter["kind"] == "api" else {}),
    }
    added = store.add_candidate(request)
    assert (added["id"], added["label"], added["note"]) == ("new_model.v2", "New: model #2", '"quoted" start')
    removed, new = line_changes(before, store.catalog_file.read_bytes())
    assert removed == []
    assert '            label: "New: model #2"' in new and "            note: '\"quoted\" start'" in new
    # a second lab candidate lands after the first; taking the first out leaves the second in place
    second = store.add_candidate({**request, "ref": "some-org/another"})
    assert [c["id"] for c in store.slot(slot_id)["candidates"]][-2:] == ["new_model.v2", "another"]
    store.remove_candidate(slot_id, "new_model.v2")
    assert store.slot(slot_id)["candidates"][-1] == second
    store.remove_candidate(slot_id, "another")
    assert store.catalog_file.read_bytes() == before


@pytest.mark.parametrize(("change", "status", "message"), [
    ({"slot": "nope"}, 404, "카탈로그에 'nope' 부품이 없습니다."),
    ({"adapter": "gemini-api"}, 400, "'Legal Analyst'에는 'gemini-api' 계열을 쓸 수 없습니다."),
    ({"slot": "rag.chunking", "adapter": "builtin"}, 400,
     "'코드에 구현된 방식' 후보는 코드에서만 추가할 수 있습니다."),
    ({"ref": "  "}, 400, "모델 ID(ref)를 입력하세요."),
    ({"ref": "anthropic/claude-opus-5.5"}, 409,
     "'anthropic/claude-opus-5.5'는 이미 'Legal Analyst' 후보에 있습니다."),
    ({"ref": "회사/모델"}, 400, "모델 ID로 후보 키를 만들 수 없습니다. 영문·숫자로 된 ID를 쓰세요."),
    ({"ref": "elsewhere/GPT-5.4"}, 409, "'gpt-5.4' 키를 가진 후보가 이미 있습니다."),
    ({"caps": ["json", "image", "smell"]}, 400, "'Legal Analyst'에는 도구 호출 기능이 필요합니다."),
    ({"price": None}, 400, "외부 API 모델은 단가가 필요합니다 (USD / 1M 토큰)."),
    ({"price": {"in": "2", "out": 6}}, 400, "외부 API 모델은 단가가 필요합니다 (USD / 1M 토큰)."),
])
def test_candidates_the_catalog_cannot_take_are_refused(store: PipelineStore, change, status, message):
    before = store.catalog_file.read_bytes()
    assert refusal(store.add_candidate, {**MISTRAL, **change}) == (status, message)
    assert store.catalog_file.read_bytes() == before


def test_only_unused_lab_candidates_can_be_removed(store: PipelineStore):
    store.add_candidate(MISTRAL)
    for profile_id in ("uses-it", "also-uses-it"):
        using = spec_of(store, id=profile_id, selections={"llm.analyst": "mistral-large-3"})
        store.save_profile(using, "create")
    with_candidate = store.catalog_file.read_bytes()
    for args, expected in [
        (("nope", "x"), (404, "카탈로그에 'nope' 부품이 없습니다.")),
        (("llm.analyst", "nope"), (404, "후보를 찾을 수 없습니다.")),
        (("llm.analyst", "gpt-5.4"), (409, "파일에 직접 적은 후보는 catalog.yaml에서 지우세요.")),
        (("llm.analyst", "mistral-large-3"),
         (409, "프로필 also-uses-it, uses-it이(가) 이 후보를 쓰고 있습니다.")),
    ]:
        assert refusal(store.remove_candidate, *args) == expected
    assert store.catalog_file.read_bytes() == with_candidate
    store.delete_profile("uses-it")
    store.delete_profile("also-uses-it")
    store.remove_candidate("llm.analyst", "mistral-large-3")
    assert store.catalog_file.read_bytes() == (PIPELINE / "catalog.yaml").read_bytes()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_an_empty_candidate_list_and_the_line_endings_of_the_file_are_kept(tmp_path: Path, newline: str):
    (tmp_path / "catalog.yaml").write_bytes(MINI_CATALOG.replace("\n", newline).encode())
    store = PipelineStore(tmp_path)
    assert store.catalog()["path"] == f"{tmp_path.as_posix()}/catalog.yaml"
    store.add_candidate({"slot": "a.part", "adapter": "local-model", "ref": "org/model-b", "label": "yes"})
    today = datetime.now(UTC).date().isoformat()
    expected = MINI_CATALOG.replace("        candidates: []   # none yet\n", "".join([
        "        candidates:   # none yet\n",
        "          - id: model-b\n",
        '            label: "yes"\n',  # quoted: PyYAML would read a bare yes as a boolean
        "            adapter: local-model\n",
        "            ref: org/model-b\n",
        "            source: lab\n",
        f"            added: {today}\n",
    ]))
    assert (tmp_path / "catalog.yaml").read_bytes() == expected.replace("\n", newline).encode()
    assert store.slot("a.part")["candidates"][0]["label"] == "yes"
    store.remove_candidate("a.part", "model-b")
    assert (tmp_path / "catalog.yaml").read_bytes() == MINI_CATALOG.replace("\n", newline).encode()


def test_a_catalog_the_edit_cannot_place_an_entry_in_is_left_alone(tmp_path: Path):
    one_line_list = "candidates: [{id: first, label: First, adapter: local-model}]"
    flow = MINI_CATALOG.replace("candidates: []   # none yet", one_line_list)
    (tmp_path / "catalog.yaml").write_text(flow, encoding="utf-8")
    store = PipelineStore(tmp_path)
    request = {"slot": "a.part", "adapter": "local-model", "ref": "org/model-b", "label": "B"}
    assert refusal(store.add_candidate, request) == (500, "카탈로그 구조를 읽을 수 없습니다.")
    assert (tmp_path / "catalog.yaml").read_text(encoding="utf-8") == flow
    assert list(tmp_path.glob("*.tmp")) == []


def test_readers_in_other_threads_never_see_a_half_written_file(store: PipelineStore):
    stop, failures = threading.Event(), []

    def read() -> None:
        reader = PipelineStore(store.dir, "configs/pipeline")  # another worker with its own cache
        while not stop.is_set():
            try:
                for one in (reader, store):
                    assert len(one.catalog()["stages"]) == 5 and len(one.list_profiles()) == len(PROFILE_IDS)
                    assert one.service()["local_only"] == "local-only"
            except Exception as error:
                failures.append(error)

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        for _ in range(3):
            store.add_candidate(MISTRAL)
            store.promote("budget-saver", "default")
            store.remove_candidate("llm.analyst", "mistral-large-3")
            store.promote("baseline-v2", "default")
    finally:
        stop.set()
        for thread in threads:
            thread.join()
    assert failures == []
    assert store.catalog_file.read_bytes() == (PIPELINE / "catalog.yaml").read_bytes()
    assert store.service_file.read_bytes() == (PIPELINE / "service.yaml").read_bytes()


# ---------- profiles ----------

@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_saving_an_unchanged_profile_keeps_every_byte(store: PipelineStore, profile_id: str):
    file = store.profiles_dir / f"{profile_id}.yaml"
    before = file.read_bytes()
    stored = store.get_profile(profile_id)
    saved = store.save_profile(stored["spec"], "update")
    assert file.read_bytes() == before  # the hand-written 0.30 and 1.00 too; JavaScript would print 0.3 and 1
    assert saved["spec"] == stored["spec"] and saved["source"] == "file"
    assert hash_the_pipeline_reads(store, profile_id) == LAB_HASHES[profile_id]


def test_editing_a_profile_changes_only_the_edited_lines(store: PipelineStore):
    file = store.profiles_dir / "research-max.yaml"
    by_hand = file.read_text(encoding="utf-8")
    for old, new in [
        ("  rag.seed: enabled\n", "  rag.seed: enabled  # 시드 유지\n"),
        ("  # 렌더\n", "\n  # 렌더: 품질 우선 (팀 메모)\n"),
        ("  rag.top_k: 8\n  analyst.max_tool_calls: 40\n", "  analyst.max_tool_calls: 40\n  rag.top_k: 8\n"),
        ("source: file\n", "source: file\nowner: giheon\n"),
    ]:
        assert old in by_hand
        by_hand = by_hand.replace(old, new)
    file.write_text(by_hand, encoding="utf-8")
    before = file.read_bytes()
    edited = spec_of(
        store, "research-max", name="research: max",
        selections={"llm.auditor": "grok-4.7"}, params={"rag.top_k": 10},
    )
    saved = store.save_profile(edited, "update")
    assert line_changes(before, file.read_bytes()) == (
        ["name: research-max", "  llm.auditor: gemini-3.1-pro", "  rag.top_k: 8"],
        ['name: "research: max"', "  llm.auditor: grok-4.7", "  rag.top_k: 10"],
    )
    assert saved["spec"] == edited
    assert hash_the_pipeline_reads(store, "research-max") == store.resolve(edited)["configHash"]
    assert store.resolve(edited)["configHash"] != LAB_HASHES["research-max"]


def test_a_profile_that_lacks_a_part_of_the_catalog_is_laid_out_again(store: PipelineStore):
    """The one case where a save rewrites the whole file: its keys have to change (pipeline.ts always does)."""
    file = store.profiles_dir / "baseline-v2.yaml"
    original = file.read_text(encoding="utf-8")
    by_hand = original.replace("  rag.seed: enabled\n", "").replace("  rag.top_k: 8\n", "# 메모\n")
    file.write_text(by_hand, encoding="utf-8")
    assert store.issues(store.get_profile("baseline-v2")["spec"]) == [
        {"level": "info", "slot": "rag.seed",
         "text": "카탈로그에 새로 생긴 부품 '시드 묶음' — 기본 후보를 씁니다."},
    ]
    saved = store.save_profile(store.get_profile("baseline-v2")["spec"], "update")
    assert file.read_text(encoding="utf-8") == original.replace("cost_cap_usd: 1.00", "cost_cap_usd: 1")
    assert store.resolve(saved["spec"])["configHash"] == LAB_HASHES["baseline-v2"]


def test_a_new_profile_is_written_like_the_hand_made_ones(store: PipelineStore):
    request = spec_of(
        store, id=" lab-made ", name=" 실험: 경량 조합 ", description="", base="baseline-v2",
        selections={"llm.analyst": "gpt-5.4", "rag.reranker": "none"},
        params={"case.cost_cap_usd": 0.25, "rag.top_k": 10.0},
    )
    del request["selections"]["rag.seed"], request["params"]["auditor.max_rounds"]  # catalog defaults fill in
    saved = store.save_profile(request, "create")
    assert saved["source"] == "lab" and saved["path"] == "configs/pipeline/profiles/lab-made.yaml"
    assert saved["spec"]["name"] == "실험: 경량 조합" and saved["spec"]["params"]["rag.top_k"] == 10

    expected = (PIPELINE / "profiles" / "baseline-v2.yaml").read_text(encoding="utf-8").split("\n")
    for old, new in {
        "id: baseline-v2": "id: lab-made",
        "name: baseline-v2": 'name: "실험: 경량 조합"',
        "source: file": "base: baseline-v2\nsource: lab",
        "  llm.analyst: claude-sonnet-5.5": "  llm.analyst: gpt-5.4",
        "  rag.reranker: kure-reranker-nano": "  rag.reranker: none",
        "  rag.top_k: 8": "  rag.top_k: 10",
        "  case.cost_cap_usd: 1.00": "  case.cost_cap_usd: 0.25",
    }.items():
        expected[expected.index(old)] = new
    expected[3] = 'description: ""'
    assert (store.profiles_dir / "lab-made.yaml").read_text(encoding="utf-8") == "\n".join(expected)

    # the pipeline reads the file to the hash the Lab computes for these choices (pipeline.ts: 16006fda)
    assert hash_the_pipeline_reads(store, "lab-made") == "16006fda"
    assert store.resolve(saved["spec"])["configHash"] == "16006fda"
    assert [p["spec"]["id"] for p in store.list_profiles()] == sorted([*PROFILE_IDS, "lab-made"])


def test_profiles_that_break_the_rules_are_refused(store: PipelineStore):
    id_rule = (400, "프로필 ID는 영문 소문자·숫자·'-'로 2~48자여야 합니다.")
    assert refusal(store.save_profile, spec_of(store, id="Bad ID"), "create") == id_rule
    assert refusal(store.save_profile, spec_of(store, id="x"), "create") == id_rule
    exists = (409, "'baseline-v2' 프로필이 이미 있습니다.")
    assert refusal(store.save_profile, spec_of(store), "create") == exists
    missing = (404, "프로필 'missing'을(를) 찾을 수 없습니다.")
    assert refusal(store.save_profile, spec_of(store, id="missing"), "update") == missing
    wrong = spec_of(
        store, id="wrong", selections={"vlm.scene": "nope", "agent.strategy": "static"},
        params={"rag.top_k": 99},
    )
    assert refusal(store.save_profile, wrong, "create") == (400, " / ".join([
        "'장면 설명'의 후보 'nope'이(가) 카탈로그에 없습니다.",
        "'계획 전략'의 '전부 가림'은(는) 아직 구현되지 않았습니다.",
        "검색 결과 수 (k)은(는) 3~20 사이여야 합니다.",
    ]))
    assert sorted(p.name for p in store.profiles_dir.iterdir()) == [f"{i}.yaml" for i in PROFILE_IDS]


def test_serviced_profiles_stay_and_promotion_changes_one_value(store: PipelineStore):
    in_use = (409, "서비스가 쓰고 있는 프로필은 지울 수 없습니다. 다른 프로필을 먼저 승격하세요.")
    assert refusal(store.delete_profile, "baseline-v2") == in_use
    assert refusal(store.delete_profile, "local-only") == in_use
    assert refusal(store.delete_profile, "nope") == (404, "프로필 'nope'을(를) 찾을 수 없습니다.")
    assert refusal(store.promote, "nope", "default") == (404, "프로필 'nope'을(를) 찾을 수 없습니다.")
    assert refusal(store.promote, "budget-saver", "elsewhere")[0] == 400

    before = store.service_file.read_bytes()
    store.promote("budget-saver", "default")
    changed = line_changes(before, store.service_file.read_bytes())
    assert changed == (["default: baseline-v2"], ["default: budget-saver"])  # both comment lines stay
    assert store.service() == {"default": "budget-saver", "local_only": "local-only"}
    store.promote("budget-saver", "local_only")
    both = before.replace(b"default: baseline-v2", b"default: budget-saver")
    both = both.replace(b"local_only: local-only", b"local_only: budget-saver")
    assert store.service_file.read_bytes() == both
    assert refusal(store.delete_profile, "budget-saver") == in_use
    store.delete_profile("baseline-v2")
    assert [p["spec"]["id"] for p in store.list_profiles()] == [i for i in PROFILE_IDS if i != "baseline-v2"]

    store.service_file.write_text("# 포인터\nlocal_only:   # 아직 없음\n", encoding="utf-8")
    store.promote("research-max", "local_only")
    store.promote("budget-saver", "default")
    assert store.service_file.read_text(encoding="utf-8") == (
        "# 포인터\nlocal_only: research-max   # 아직 없음\ndefault: budget-saver\n"
    )


# ---------- YAML scalars ----------

@pytest.mark.parametrize(("value", "written"), [
    ("Mistral Large 3", "Mistral Large 3"), ("값 · 가운뎃점 (R4)", "값 · 가운뎃점 (R4)"), ("a:b", "a:b"),
    ("a#b", "a#b"), ("$0.30 / $1.20", "$0.30 / $1.20"), ("tab\tinside", "tab\tinside"), ("--- x", "--- x"),
    ("a: b", '"a: b"'), ("ends:", '"ends:"'), ("#hash", '"#hash"'), ("a #b", '"a #b"'), ("- dash", '"- dash"'),
    ("[x]", '"[x]"'), ("{x}", '"{x}"'), ("*a", '"*a"'), ("&a", '"&a"'), ("!a", '"!a"'), ("%a", '"%a"'),
    ("@a", '"@a"'), (" lead", '" lead"'), ("trail ", '"trail "'), ("it's: so", '"it\'s: so"'),
    ("back\\slash: x", '"back\\\\slash: x"'), ('"quoted" start', "'\"quoted\" start'"),
    ("'single' start", "\"'single' start\""), ("", '""'), ("null", '"null"'), ("~", '"~"'),
    ("true", '"true"'), ("4.5", '"4.5"'), ("12", '"12"'), ("1e5", '"1e5"'), ("0x1f", '"0x1f"'),
    ("0o17", '"0o17"'), (".inf", '".inf"'),
    (12, "12"), (2.0, "2"), (0.30, "0.3"), (0.825, "0.825"), (0.00001, "0.00001"), (0.0000001, "1e-7"),
    (1e21, "1e+21"), (123456789012345680000, "123456789012345680000"), (-0.5, "-0.5"),
])
def test_scalars_are_written_as_the_yaml_package_of_the_lab_writes_them(value, written: str):
    assert _scalar(value) == written


@pytest.mark.parametrize(("value", "written"), [
    ("yes", '"yes"'), ("No", '"No"'), ("on", '"on"'), ("1:30", '"1:30"'), ("1_000", '"1_000"'),
    ("2026-11-11", '"2026-11-11"'), ("첫 줄\n둘째 줄", '"첫 줄\\n둘째 줄"'), ("bell\x07", '"bell\\u0007"'),
])
def test_text_pyyaml_would_misread_or_spread_over_lines_is_quoted(value: str, written: str):
    """Where this port departs from the `yaml` package: it would write these plain or as a block scalar."""
    assert _scalar(value) == written
    assert yaml.safe_load(f"key: {written}\n") == {"key": value}


# ---------- OpenRouter lookup ----------

OPENROUTER_LIST = {"data": [
    {"id": "anthropic/claude-sonnet-5.5", "canonical_slug": "anthropic/claude-5.5-sonnet-20261001",
     "name": "Anthropic: Claude Sonnet 5.5", "context_length": 1000000,
     "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "architecture": {"input_modalities": ["text", "image"]},
     "supported_parameters": ["tools", "structured_outputs", "temperature"]},
    {"id": "tiny/tiny-1", "pricing": {"prompt": "0.0000008255", "completion": "0.00000149999"},
     "architecture": {"input_modalities": ["text", "video"]}, "supported_parameters": []},
    {"id": "bare/model"},
]}


@pytest.fixture
def openrouter_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def get(url: str, **options):
        calls.append({"url": url, **options})
        return httpx.Response(200, json=OPENROUTER_LIST, request=httpx.Request("GET", url))

    monkeypatch.setattr(openrouter, "_cache", None)
    monkeypatch.setattr(openrouter.httpx, "get", get)
    return calls


def test_openrouter_lookup_fills_a_candidate_from_the_public_list(openrouter_calls: list[dict]):
    assert openrouter.lookup_openrouter(" anthropic/claude-sonnet-5.5 ") == {
        "ref": "anthropic/claude-sonnet-5.5", "label": "Claude Sonnet 5.5", "price": {"in": 3, "out": 15},
        "caps": ["tools", "json", "image"], "tier": "frontier", "contextLength": 1000000,
    }
    by_canonical_slug = openrouter.lookup_openrouter("anthropic/claude-5.5-sonnet-20261001")
    assert by_canonical_slug["ref"] == "anthropic/claude-sonnet-5.5"
    assert openrouter.lookup_openrouter("tiny/tiny-1") == {
        "ref": "tiny/tiny-1", "label": "tiny/tiny-1", "price": {"in": 0.826, "out": 1.5},
        "caps": ["video"], "tier": "efficient",
    }
    assert openrouter.lookup_openrouter("bare/model")["price"] == {"in": 0, "out": 0}
    no_slash = (400, "OpenRouter slug는 '회사/모델' 형식입니다 (예: anthropic/claude-sonnet-5.5).")
    assert refusal(openrouter.lookup_openrouter, "claude") == no_slash
    unknown = (404, "OpenRouter 공개 목록에 'nope/none'이(가) 없습니다.")
    assert refusal(openrouter.lookup_openrouter, "nope/none") == unknown
    # one download serves every lookup for ten minutes, and it carries no key or other identifying header
    assert len(openrouter_calls) == 1 and openrouter_calls[0]["url"] == "https://openrouter.ai/api/v1/models"
    assert openrouter_calls[0]["headers"] == {"Accept": "application/json", "User-Agent": "PrivFrame"}


def test_openrouter_failures_are_reported_as_bad_gateway(monkeypatch: pytest.MonkeyPatch):
    def unreachable(url: str, **options):
        raise httpx.ConnectError("no route", request=httpx.Request("GET", url))

    def unavailable(url: str, **options):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(openrouter, "_cache", None)
    monkeypatch.setattr(openrouter.httpx, "get", unreachable)
    offline = (502, "OpenRouter에 연결할 수 없습니다. 네트워크를 확인하거나 값을 직접 입력하세요.")
    assert refusal(openrouter.lookup_openrouter, "a/b") == offline
    monkeypatch.setattr(openrouter.httpx, "get", unavailable)
    assert refusal(openrouter.lookup_openrouter, "a/b") == (502, "OpenRouter 모델 목록을 가져오지 못했습니다 (503).")
