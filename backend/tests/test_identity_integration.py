"""Real CPU A→B→A regression, through API review/decision/render and saved reports.

The local reference video and pinned assets are optional. Decisions are explicitly
fixture data; all detection, recognition and rendering are real local adapters.
"""

import json
import shutil

import pytest
from fastapi.testclient import TestClient

import test_application as shared_fixtures
from privframe.api import create_app
from privframe.bootstrap import create_pipeline, create_service
from privframe.config import ROOT, Settings
from privframe.domain.models import VideoRecord
from privframe.infrastructure.storage import sha256_file


def test_real_aba_identity_review_cache_and_render(tmp_path, monkeypatch):
    source = ROOT / "data/videos/PrivFrame_ABA/A_B_A.mp4"
    model = ROOT / "models/w600k_r50.onnx"
    if not source.is_file() or not model.is_file() or not shutil.which("ffmpeg"):
        pytest.skip("Local A_B_A reference video, pinned models and FFmpeg are required")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for name in ("baseline", "fixture", "fixture-yunet"):
        shutil.copyfile(ROOT / f"configs/{name}.yaml", profiles / f"{name}.yaml")
    (profiles / "strict.yaml").write_text("extends: fixture-yunet\nname: strict\nidentity_similarity_threshold: 0.65\n")
    dispatcher = shared_fixtures.InlineDispatcher()
    service = create_service(Settings(data_dir=tmp_path / "data", profiles_dir=profiles), dispatcher)
    pipeline = create_pipeline(service)
    dispatcher.pipeline = pipeline
    artifact = service.artifacts.resolve("videos/aba/original.mp4")
    artifact.parent.mkdir(parents=True)
    shutil.copyfile(source, artifact)
    service.repository.insert("video", VideoRecord(
        id="aba", filename=source.name, artifact=service.artifacts.relative(artifact), content_hash=sha256_file(artifact),
    ))
    try:
        with TestClient(create_app(service)) as client:
            response = client.post("/api/runs", json={"video_id": "aba", "profile": "fixture-yunet",
                                                      "scenario": {"context": "로컬 인물 그룹화 검증"}})
            assert response.status_code == 202, response.text
            run = response.json()
            assert run["status"] == "AWAITING_LABELS", run["error"]
            assert run["observation"]["normalization"]["scene_cut_frames"] == [300, 600]
            assert len(run["observation"]["tracks"]) == 8
            assert {frozenset(group["track_ids"]) for group in run["identity"]["groups"]} == {
                frozenset(["face-0001", "face-0006"]), frozenset(["face-0002", "face-0007"]),
                frozenset(["face-0003", "face-0008"]), frozenset(["face-0004"]), frozenset(["face-0005"]),
            }
            url = f"/api/runs/{run['id']}"
            first = run["identity"]["groups"][0]
            assert client.get("/api/artifacts/" + first["thumbnail"]).status_code == 200
            assert client.get("/api/artifacts/" + run["identity"]["features_artifact"]).status_code == 404
            assert client.post(url + "/decide").status_code == 400
            groups = [{"id": group["id"], "track_ids": group["track_ids"]} for group in run["identity"]["groups"]]
            confirmed = client.post(url + "/identity-review", json={
                "revision": 0, "groups": groups,
                "persons": {group["id"]: {"relationship": "third", "consent": "granted" if n == 0 else "denied"}
                            for n, group in enumerate(groups)},
                "confirmed_group_ids": [group["id"] for group in groups],
            })
            assert confirmed.status_code == 200, confirmed.text
            reviewed = confirmed.json()
            # These operations must reuse actual detector frames/recognition features.
            monkeypatch.setattr(pipeline.adapters.analyzer, "analyze", lambda *a: pytest.fail("Repeated detection"))
            monkeypatch.setattr(pipeline.adapters.identity_grouper, "_extract", lambda *a: pytest.fail("Repeated features"))
            same = client.post(url + "/fork", json={}).json()
            assert same["status"] == "AWAITING_LABELS", same["error"]
            assert same["identity"]["reviewed"] and same["scenario"]["persons"] == reviewed["scenario"]["persons"]
            strict = client.post(url + "/fork", json={"profile": "strict"}).json()
            assert strict["status"] == "AWAITING_LABELS", strict["error"]
            assert strict["identity"]["metrics"]["feature_cache_reused"]
            assert len(strict["identity"]["groups"]) == 5 and not strict["identity"]["reviewed"]
            assert all(person["consent"] == "unknown" for person in strict["scenario"]["persons"].values())
            decided = client.post(url + "/decide").json()
            assert decided["status"] == "REVIEW_REQUIRED", decided["error"]
            prompt = json.loads(decided["decision"]["prompt"][1]["content"])
            assert prompt["scenario"]["persons"][first["id"]]["track_ids"] == first["track_ids"]
            treatments = {track["id"]: {"action": "MASK", "strength": 2}
                          for track in run["observation"]["tracks"]}
            overridden = client.put(url + "/overrides", json={"treatments": treatments})
            assert overridden.status_code == 200, overridden.text
            completed = client.post(url + "/render").json()
            assert completed["status"] == "COMPLETED", completed["error"]
            assert completed["render"]["frame_count"] == 900 and not completed["render"]["has_audio"]
            assert completed["render"]["treatments"] == treatments
            report = client.get("/api/artifacts/" + completed["reports"]["json"]).json()
            assert report["identity"]["groups"] == reviewed["identity"]["groups"]
            html = client.get("/api/artifacts/" + completed["reports"]["html"]).text
            assert "인물 그룹" in html and "다른 사람" in html
            reloaded = client.get(url).json()
            assert reloaded == completed and sha256_file(source) == run["observation"]["original_hash"]
    finally:
        service.repository.engine.dispose()
