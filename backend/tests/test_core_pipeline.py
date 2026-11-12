from conftest import LAB_HASHES, PIPELINE
from pathlib import Path

import pytest

from privframe.core.pipeline import ProfileSpec, config_hash, list_profiles, load_catalog, load_profile, resolve, service_profiles
from privframe.core.settings import CoreSettings


# values the Lab shows for the same files (frontend/mock/pipeline.ts); the two sides must agree
CLAUDE_API = {"claude-opus-5-5-api": "claude-opus-5-5", "claude-sonnet-5-5-api": "claude-sonnet-5-5", "claude-haiku-5-5-api": "claude-haiku-5-5"}
LLM_SLOTS = ("llm.analyst", "llm.planner", "llm.review_guide", "llm.auditor", "llm.report")
VLM_SLOTS = ("vlm.scene", "vlm.person", "vlm.inspect")


@pytest.mark.parametrize("profile_id", sorted(LAB_HASHES))
def test_config_hash_matches_the_lab(profile_id: str):
    resolved = resolve(load_catalog(PIPELINE), load_profile(PIPELINE, profile_id))
    assert resolved.config_hash == LAB_HASHES[profile_id]


def test_every_profile_has_a_hash_the_lab_was_asked_for():
    assert sorted(list_profiles(PIPELINE)) == sorted(LAB_HASHES)


def test_integral_floats_hash_like_javascript_numbers():
    assert config_hash({"a": "x"}, {"p": 1.0}) == config_hash({"a": "x"}, {"p": 1})


def test_missing_parts_take_the_catalog_default():
    catalog = load_catalog(PIPELINE)
    resolved = resolve(catalog, ProfileSpec(id="draft", name="draft"))
    for slot in catalog.slots:
        assert resolved.part(slot.id).candidate.id == slot.default


def test_local_only_keeps_frames_on_the_machine():
    catalog = load_catalog(PIPELINE)
    assert resolve(catalog, load_profile(PIPELINE, "baseline-v2")).frames_leave_device
    assert not resolve(catalog, load_profile(PIPELINE, "local-only")).frames_leave_device


def test_every_profile_and_service_pointer_refers_to_known_entries():
    catalog = load_catalog(PIPELINE)
    profiles = list_profiles(PIPELINE)
    assert set(service_profiles(PIPELINE).values()) <= set(profiles)
    for profile_id in profiles:
        spec = load_profile(PIPELINE, profile_id)
        for slot_id, candidate_id in spec.selections.items():
            assert catalog.slot(slot_id).candidate(candidate_id) is not None, f"{profile_id}: {slot_id}={candidate_id}"


def test_override_swaps_one_part(tmp_path: Path):
    catalog = load_catalog(PIPELINE)
    base = load_profile(PIPELINE, "baseline-v2")
    forked = resolve(catalog, base, {"llm.analyst": "gemini-3.8-flash"})
    assert forked.part("llm.analyst").candidate.tier == "efficient"
    assert forked.config_hash != resolve(catalog, base).config_hash


def test_claude_models_can_be_called_on_the_anthropic_api_directly():
    catalog = load_catalog(PIPELINE)
    adapter = catalog.adapters["anthropic"]
    assert (adapter.kind, adapter.label, adapter.lookup) == ("api", "Anthropic API (직접)", None)
    for slot_id in (*LLM_SLOTS, *VLM_SLOTS):
        slot = catalog.slot(slot_id)
        assert "anthropic" in slot.adapters, slot_id
        direct = {c.id: c for c in slot.candidates if c.adapter == "anthropic"}
        assert {identifier: c.ref for identifier, c in direct.items()} == CLAUDE_API, slot_id
        for candidate in direct.values():  # priced, so that a run can be costed without a billed amount from the route
            assert candidate.price and candidate.price[0] > 0 and candidate.price[1] > candidate.price[0]
            assert set(slot.requires) <= set(candidate.caps), (slot_id, candidate.id)
        assert slot.candidate(slot.default).adapter != "anthropic"  # nothing changes for a profile that does not ask for it


def test_the_claude_api_profile_swaps_only_the_api_models_of_the_baseline():
    catalog = load_catalog(PIPELINE)
    baseline, claude = resolve(catalog, load_profile(PIPELINE, "baseline-v2")), resolve(catalog, load_profile(PIPELINE, "claude-api"))
    changed = {slot for slot in baseline.parts if baseline.part(slot).candidate.id != claude.part(slot).candidate.id}
    assert changed == {*LLM_SLOTS, *VLM_SLOTS}
    assert all(claude.part(slot).candidate.adapter == "anthropic" and claude.part(slot).kind == "api" for slot in changed)
    assert not any(part.candidate.adapter == "openrouter" for part in claude.parts.values())  # no OpenRouter balance is used
    assert claude.frames_leave_device and claude.part("vlm.input_mode").candidate.id == baseline.part("vlm.input_mode").candidate.id
    assert {key: value for key, value in claude.params.items() if key != "case.cost_cap_usd"} == {key: value for key, value in baseline.params.items() if key != "case.cost_cap_usd"}


def test_cost_caps_of_the_catalog_and_the_profiles():
    catalog = load_catalog(PIPELINE)
    cap = next(p for p in catalog.params if p.id == "case.cost_cap_usd")
    assert (cap.default, cap.min, cap.max, cap.step, cap.unit) == (1.0, 0, 20.0, 0.25, "USD")
    assert "0이면 상한 없음" in cap.label
    caps = {profile_id: resolve(catalog, load_profile(PIPELINE, profile_id)).params["case.cost_cap_usd"] for profile_id in list_profiles(PIPELINE)}
    assert caps == {"baseline-v2": 1, "budget-saver": 0.3, "claude-api": 3, "local-only": 1, "research-max": 3}
    assert all(cap.min <= value <= cap.max for value in caps.values())
    assert resolve(catalog, ProfileSpec(id="draft", name="draft")).params["case.cost_cap_usd"] == 1


def test_an_empty_entry_in_the_environment_counts_as_not_set(monkeypatch, tmp_path):
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LANGSMITH_API_KEY", "LAW_OC", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, "")  # as `NAME=` in a copied .env.example leaves it
    settings = CoreSettings(_env_file=None)
    assert settings.anthropic_api_key is None and settings.openrouter_api_key is None
    assert settings.present() == {"OPENROUTER_API_KEY": False, "ANTHROPIC_API_KEY": False, "LANGSMITH_API_KEY": False, "LAW_OC": False, "GEMINI_API_KEY": False}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    settings = CoreSettings(_env_file=None)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test" and settings.present()["ANTHROPIC_API_KEY"] is True
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=\nOPENROUTER_API_KEY=sk-or-file\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    from_file = CoreSettings(_env_file=env)
    assert from_file.anthropic_api_key is None and from_file.openrouter_api_key.get_secret_value() == "sk-or-file"
