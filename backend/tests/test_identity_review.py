"""Person review, consent scope and edited track geometry through public use cases."""

import json

import pytest
from fastapi.testclient import TestClient

import test_application as shared_fixtures
from privframe.api import create_app
from privframe.config import fingerprint
from privframe.domain.models import (
    IdentityGroup, IdentityGrouping, IdentityReviewInput, RunSnapshot, ScenarioInput,
    SplitTrackInput, Treatment,
)

blank_video_bytes = shared_fixtures.blank_video_bytes
system = shared_fixtures.system


def with_identity(system):
    run = system.run()
    def add(value):
        groups = [IdentityGroup(id=f"person-{n}", track_ids=[track.id], thumbnail=track.thumbnail)
                  for n, track in enumerate(value.observation.tracks, 1)]
        value.identity = IdentityGrouping(feature_fingerprint="features", grouping_fingerprint="groups",
                                          automatic_groups=groups, groups=[g.model_copy(deep=True) for g in groups])
    return system.service.repository.mutate("run", run.id, RunSnapshot, add)


def review_body(run, **updates):
    return {"revision": run.identity.revision,
            "groups": [{"id": group.id, "track_ids": group.track_ids} for group in run.identity.groups],
            "persons": {"person-1": {"relationship": "self", "consent": "not_applicable"},
                        "person-2": {"relationship": "other", "consent": "granted"}},
            "confirmed_group_ids": [group.id for group in run.identity.groups], **updates}


def test_review_requires_valid_partition_and_explicit_confirmation_and_rejects_stale_save(system):
    run = with_identity(system)
    with TestClient(create_app(system.service)) as client:
        url = f"/api/runs/{run.id}"
        assert client.post(url + "/decide").status_code == 400
        assert client.post(url + "/labels", json={"track_labels": {"track1": "self"}}).status_code == 400
        for groups in ([{"id": "only", "track_ids": ["track1"]}],
                       [{"id": "only", "track_ids": ["track1", "track1", "track2"]}],
                       [{"id": "only", "track_ids": ["track1", "unknown"]}]):
            response = client.post(url + "/identity-review", json=review_body(run, groups=groups, persons={}))
            assert response.status_code == 400
        saved = client.post(url + "/identity-review", json=review_body(run))
        assert saved.status_code == 200, saved.text
        data = saved.json()
        assert data["identity"]["revision"] == 1 and data["identity"]["reviewed"]
        assert data["scenario"]["persons"]["person-1"] == {
            "relationship": "self", "consent": "not_applicable", "track_ids": ["track1"],
            "access_requester": "unknown", "legacy_values": {},
        }
        assert client.post(url + "/identity-review", json=review_body(run)).status_code == 409
        assert client.get(url).json() == data
        decided = client.post(url + "/decide").json()
        assert decided["status"] == "REVIEW_REQUIRED"
        assert "person-1" not in decided["evidence"]["query"]
        payload = json.loads(decided["decision"]["prompt"][1]["content"])
        assert payload["scenario"]["persons"]["person-2"]["track_ids"] == ["track2"]
        assert payload["scenario"]["persons"]["person-1"]["consent"] == "not_applicable"


def test_changed_membership_resets_consent_without_confirmation_and_does_not_overwrite_automatic_groups(system):
    run = with_identity(system)
    saved = system.service.identity_review(run.id, IdentityReviewInput.model_validate(review_body(run)))
    merged = system.service.identity_review(run.id, IdentityReviewInput.model_validate(review_body(
        saved, groups=[{"id": "person-1", "track_ids": ["track1", "track2"]}],
        persons={"person-1": {"relationship": "self", "consent": "granted"}}, confirmed_group_ids=[],
    )))
    assert not merged.identity.reviewed
    assert merged.scenario.persons["person-1"].consent == "unknown"
    assert merged.scenario.persons["person-1"].relationship == "unknown"
    assert len(merged.identity.automatic_groups) == 2 and len(merged.identity.groups) == 1
    assert len(merged.identity.user_changes) == 2
    with pytest.raises(ValueError, match="저장"):
        system.service.request_stage(run.id, "decide")


def test_split_preserves_all_observed_pixels_resets_only_affected_person_and_survives_reload(system):
    run = with_identity(system)
    run = system.service.identity_review(run.id, IdentityReviewInput.model_validate(review_body(run)))
    source = run.observation.tracks[0]
    result = system.service.split_track(run.id, SplitTrackInput(
        revision=run.identity.revision, track_id="track1", frame=3,
    ))
    children = [track for track in result.observation.tracks if track.source_track_id == "track1"]
    assert len(children) == 2
    assert [point.model_dump() for track in sorted(children, key=lambda item: item.start) for point in track.points] == [
        point.model_dump() for point in source.points]
    assert children[0].end == pytest.approx(3 / 30)
    assert all(system.service.artifacts.resolve(track.thumbnail).is_file() for track in children)
    assert result.scenario.persons["person-2"].consent == "granted"
    assert all(person.consent == "unknown" for key, person in result.scenario.persons.items() if key != "person-2")
    assert not result.identity.reviewed and result.identity.feature_fingerprint == ""
    assert result.identity.user_changes[-1]["operation"] == "split_track"
    assert system.service.repository.get("run", run.id, RunSnapshot) == result
    with pytest.raises(ValueError, match="both sides"):
        system.service.split_track(run.id, SplitTrackInput(
            revision=result.identity.revision, track_id=children[0].id, frame=0))


class CachedGrouper:
    calls = 0

    def group(self, observation, config, run_id, cached=None):
        self.calls += 1
        groups = [IdentityGroup(id=f"person-{n}", track_ids=[track.id])
                  for n, track in enumerate(observation.tracks, 1)]
        return IdentityGrouping(feature_fingerprint="features",
                                grouping_fingerprint=fingerprint({"tracks": [track.id for track in observation.tracks],
                                                                  "merge": config.get("identity_similarity_threshold")}),
                                automatic_groups=groups, groups=groups)


def enable_grouping(system):
    profile = system.service.settings.profiles_dir / "fixture.yaml"
    with profile.open("a") as stream:
        stream.write("\nidentity_grouper: arcface\n")
    system.pipeline.adapters.identity_grouper = CachedGrouper()


def test_fork_keeps_review_only_for_same_analysis_membership_and_purpose(system):
    enable_grouping(system)
    parent = system.run()
    parent = system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(parent)))
    same = system.service.fork(parent.id)
    assert same.identity.reviewed and same.scenario.persons == parent.scenario.persons
    assert system.analyzer.calls == 1
    changed = system.service.fork(parent.id, ScenarioInput(context="완전히 새로운 공개 목적"))
    assert not changed.identity.reviewed
    assert all(person.consent == "unknown" for person in changed.scenario.persons.values())
    assert changed.identity.groups == parent.identity.groups and system.analyzer.calls == 1
    modified = system.service.fork(parent.id, profile="fixture-other")
    assert not modified.identity.reviewed
    assert all(person.consent == "unknown" and person.relationship == "unknown"
               for person in modified.scenario.persons.values())
    assert system.analyzer.calls == 2
    assert system.service.repository.get("run", parent.id, RunSnapshot) == parent


def test_identity_failure_checkpoints_analysis_then_retry_avoids_detection(system):
    enable_grouping(system)
    original = system.pipeline.adapters.identity_grouper
    class Unavailable:
        def group(self, *args, **kwargs):
            raise RuntimeError("ArcFace model missing")
    system.pipeline.adapters.identity_grouper = Unavailable()
    run = system.run()
    assert run.status == "FAILED" and run.observation is not None
    assert system.analyzer.calls == 1 and "ArcFace" in run.error
    system.pipeline.adapters.identity_grouper = original
    retried = system.service.request_stage(run.id, "analyze", retry=True)
    assert retried.status == "AWAITING_LABELS" and retried.identity is not None
    assert system.analyzer.calls == 1


def test_grouped_revision_preserves_review_and_only_renders(system):
    enable_grouping(system)
    parent = system.run()
    parent = system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(parent)))
    system.service.request_stage(parent.id, "decide")
    system.service.overrides(parent.id, {key: Treatment(action="MASK", strength=2) for key in ("track1", "track2")}, False)
    parent = system.service.request_stage(parent.id, "render")
    assert parent.status == "COMPLETED", parent.error
    revised = system.service.revise(parent.id)
    assert revised.identity == parent.identity and revised.scenario.persons == parent.scenario.persons
    assert revised.status == "REVIEW_REQUIRED"
    renders_before = system.pipeline.adapters.identity_grouper.calls
    rendered = system.service.request_stage(revised.id, "render")
    assert rendered.status == "COMPLETED", rendered.error
    assert system.pipeline.adapters.identity_grouper.calls == renders_before


def test_legacy_snapshots_default_to_face_and_remain_readable(system):
    run = system.run()
    data = run.model_dump(mode="json")
    data.pop("identity")
    data["scenario"].pop("persons")
    for track in data["observation"]["tracks"]:
        track.pop("object_type")
        track.pop("source_track_id")
        for point in track["points"]:
            point.pop("landmarks")
    loaded = RunSnapshot.model_validate(data)
    assert loaded.identity is None and loaded.scenario.persons == {}
    assert all(track.object_type == "face" for track in loaded.observation.tracks)


def test_retry_keeps_child_consent_snapshot_when_unfinished_parent_is_edited(system, monkeypatch):
    enable_grouping(system)
    parent = system.run()
    parent = system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(parent)))
    system.dispatcher.paused = True
    child = system.service.fork(parent.id)
    original_write = system.service.artifacts.put_bytes
    def fail_snapshot(path, data):
        if path.endswith("/snapshot.json"):
            raise OSError("Snapshot temporarily unavailable")
        return original_write(path, data)
    monkeypatch.setattr(system.service.artifacts, "put_bytes", fail_snapshot)
    try:
        system.pipeline.run_stage(child.id, "analyze", child.stages[-1].id)
    except OSError:
        pass
    failed = system.service.repository.get("run", child.id, RunSnapshot)
    assert failed.status == "FAILED" and failed.identity.reviewed
    assert failed.scenario.persons["person-1"].consent == "not_applicable"
    monkeypatch.setattr(system.service.artifacts, "put_bytes", original_write)
    system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(
        parent, groups=[{"id": "person-1", "track_ids": ["track1", "track2"]}],
        persons={"person-1": {"relationship": "other", "consent": "granted"}},
        confirmed_group_ids=["person-1"],
    )))
    system.dispatcher.paused = False
    retried = system.service.request_stage(child.id, "analyze", retry=True)
    assert retried.status == "AWAITING_LABELS", retried.error
    assert retried.identity.reviewed
    assert retried.scenario.persons == failed.scenario.persons
    assert retried.identity.groups == failed.identity.groups
    assert all(set(retried.scenario.persons[group.id].track_ids) == set(group.track_ids)
               for group in retried.identity.groups)


def test_fork_grouping_failure_keeps_parent_corrections_and_purpose_scope_on_retry(system):
    enable_grouping(system)
    parent = system.run()
    parent = system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(
        parent, groups=[{"id": "person-1", "track_ids": ["track1", "track2"]}],
        persons={"person-1": {"relationship": "other", "consent": "denied"}},
        confirmed_group_ids=["person-1"],
    )))
    original = system.pipeline.adapters.identity_grouper
    class Unavailable:
        def group(self, *args, **kwargs):
            raise RuntimeError("Recognition temporarily unavailable")
    system.pipeline.adapters.identity_grouper = Unavailable()
    same = system.service.fork(parent.id)
    changed = system.service.fork(parent.id, ScenarioInput(context="새로운 이용 목적"))
    assert same.status == changed.status == "FAILED"
    assert same.identity.groups == changed.identity.groups == parent.identity.groups
    assert same.scenario.persons == parent.scenario.persons
    assert changed.scenario.persons["person-1"].consent == "unknown" and not changed.identity.reviewed
    system.service.identity_review(parent.id, IdentityReviewInput.model_validate(review_body(
        parent, groups=[{"id": "person-1", "track_ids": ["track1"]},
                        {"id": "person-2", "track_ids": ["track2"]}],
        confirmed_group_ids=["person-1", "person-2"],
    )))
    system.pipeline.adapters.identity_grouper = original
    retried = system.service.request_stage(same.id, "analyze", retry=True)
    assert retried.status == "AWAITING_LABELS" and retried.identity.reviewed
    assert retried.identity.groups == same.identity.groups
    assert retried.scenario.persons == same.scenario.persons
    changed_retry = system.service.request_stage(changed.id, "analyze", retry=True)
    assert changed_retry.status == "AWAITING_LABELS" and not changed_retry.identity.reviewed
    assert changed_retry.identity.groups == changed.identity.groups
    assert changed_retry.scenario.persons["person-1"].consent == "unknown"
    assert system.analyzer.calls == 1
