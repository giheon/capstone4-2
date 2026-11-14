from __future__ import annotations

import hashlib
import io
import json
import runpy
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from privframe.adapters.identity import (
    ARCFACE_TEMPLATE, ArcFaceEmbedder, ArcFaceIdentityGrouper, align_face, constrained_clusters,
)
from privframe.adapters.vision import VideoProcessingError
from privframe.domain.models import Box, Track, TrackPoint, VideoObservation
from privframe.infrastructure.storage import ArtifactStore, sha256_file


def vector(angle=0):
    result = np.zeros(512)
    result[:2] = [np.cos(angle), np.sin(angle)]
    return result


def observation(track_frames=None):
    track_frames = track_frames or {"a": [0, 1], "b": [3, 4], "c": [3, 4]}
    return VideoObservation(
        video_id="video", original_hash="original", working_artifact="video.mp4", working_hash="working",
        width=128, height=128, fps=30, frame_count=8, duration=8 / 30, has_audio=False,
        detector="yunet", tracker="iou", analysis_fingerprint="analysis",
        tracks=[Track(id=name, start=min(frames) / 30, end=max(frames) / 30, points=[
            TrackPoint(frame=frame, time=frame / 30, box=Box(x=20, y=20, width=80, height=80),
                       confidence=0.99, landmarks=ARCFACE_TEMPLATE.tolist()) for frame in frames
        ]) for name, frames in track_frames.items()],
    )


def cached_features(grouper, obs, values, config=None):
    fingerprint = grouper.feature_fingerprint(obs, config or {})
    data = {"fingerprint": fingerprint, "version": "test", "tracks": {}}
    for track in obs.tracks:
        samples = []
        for index, feature in enumerate(values.get(track.id, [])):
            path = f"identity/{track.id}-{index}.jpg"
            grouper.artifacts.put_bytes(path, b"test thumbnail")
            samples.append({"embedding": feature.tolist(), "frame": track.points[index].frame,
                            "quality": 0.8, "thumbnail": path})
        data["tracks"][track.id] = {"samples": samples, "issues": [] if samples else ["low_quality"]}
    path = f"identity/features/{fingerprint}/features.json"
    grouper.artifacts.put_bytes(path, json.dumps(data).encode())
    return path


def test_alignment_is_deterministic_similarity_and_rejects_degenerate_points():
    rng = np.random.default_rng(14)
    frame = rng.integers(0, 256, (112, 112, 3), dtype=np.uint8)
    np.testing.assert_array_equal(align_face(frame, ARCFACE_TEMPLATE), frame)
    np.testing.assert_array_equal(align_face(frame, ARCFACE_TEMPLATE), align_face(frame, ARCFACE_TEMPLATE))
    expanded = cv2.resize(frame, (224, 224))
    aligned = align_face(expanded, ARCFACE_TEMPLATE * 2)
    assert aligned.shape == frame.shape
    for landmarks in ([[0, 0]] * 5, [[float("nan"), 1]] * 5, [[1, 1]]):
        with pytest.raises(VideoProcessingError, match="landmarks"):
            align_face(frame, landmarks)


def test_complete_link_blocks_cooccurrence_and_transitive_false_merge():
    # AB and BC exceed .8, while AC does not. Connected components would join all three.
    features = {"a": vector(0), "b": vector(0.5), "c": vector(1)}
    result = constrained_clusters(["a", "b", "c"], features, {"a": {0}, "b": {1}, "c": {2}}, 0.8)
    assert len(result) == 2
    assert not any("a" in cluster and "c" in cluster for cluster in result)
    result = constrained_clusters(["a", "b"], {"a": vector(), "b": vector()},
                                  {"a": {0, 1}, "b": {1, 2}}, 0.6)
    assert result == [["a"], ["b"]]
    # Interval overlap alone is not co-detection: sparse tracks can legally reconnect.
    assert constrained_clusters(["a", "b"], {"a": vector(), "b": vector()},
                                {"a": {0, 2}, "b": {1}}, 0.6) == [["a", "b"]]


def test_grouping_cached_features_reuses_embeddings_but_reclusters_threshold(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path)
    grouper = ArcFaceIdentityGrouper(store, tmp_path / "missing.onnx")
    obs = observation()
    cached_features(grouper, obs, {"a": [vector(), vector()], "b": [vector(.6), vector(.6)],
                                  "c": [vector(2), vector(2)]})
    monkeypatch.setattr(grouper, "_extract", lambda *args: pytest.fail("Should reuse extracted features"))
    result = grouper.group(obs, {}, "one")
    assert sorted(len(group.track_ids) for group in result.groups) == [1, 2]
    assert result.metrics["feature_cache_reused"]
    assert grouper.embedder.session is None
    second = grouper.group(obs, {"identity_similarity_threshold": .95}, "two", cached=result)
    assert len(second.groups) == 3
    assert second.feature_fingerprint == result.feature_fingerprint
    assert second.grouping_fingerprint != result.grouping_fingerprint
    assert any(pair.first_track_id == "a" and pair.second_track_id == "b" for pair in second.candidates)
    # API snapshots contain summaries only, never numeric feature vectors.
    assert "embedding\"" not in result.model_dump_json()
    assert grouper.feature_fingerprint(obs, {"llm_model": "unrelated"}) == result.feature_fingerprint
    assert grouper.feature_fingerprint(obs, {"identity_max_samples": 4}) != result.feature_fingerprint


def test_mixed_and_low_quality_tracks_are_not_automatically_merged(tmp_path):
    grouper = ArcFaceIdentityGrouper(ArtifactStore(tmp_path), tmp_path / "missing.onnx")
    obs = observation()
    cached_features(grouper, obs, {"a": [vector(), vector(np.pi)], "b": [], "c": [vector(), vector()]})
    result = grouper.group(obs, {}, "one")
    assert len(result.groups) == 3
    assert "mixed_identity" in next(track for track in result.tracks if track.track_id == "a").issues
    assert "low_quality" in next(track for track in result.tracks if track.track_id == "b").issues
    assert result.metrics["excluded_track_count"] == 2
    assert not result.candidates


def test_corrupt_or_incomplete_feature_cache_is_recomputed(tmp_path, monkeypatch):
    grouper = ArcFaceIdentityGrouper(ArtifactStore(tmp_path), tmp_path / "missing.onnx")
    obs = observation()
    path = cached_features(grouper, obs, {"a": [vector(), vector()], "b": [], "c": []})
    data = json.loads(grouper.artifacts.resolve(path).read_text())
    grouper.artifacts.resolve(path).write_text("broken")
    calls = []
    monkeypatch.setattr(grouper, "_extract", lambda *args: calls.append(True) or data)
    assert not grouper.group(obs, {}, "one").metrics["feature_cache_reused"]
    assert len(calls) == 1
    grouper.artifacts.resolve("identity/a-0.jpg").unlink()
    assert not grouper.group(obs, {}, "two").metrics["feature_cache_reused"]
    assert len(calls) == 2


def test_extract_uses_diverse_frames_and_embeds_only_selected_samples(tmp_path):
    store = ArtifactStore(tmp_path)
    path = store.resolve("video.mp4")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (128, 128))
    if not writer.isOpened():
        pytest.skip("OpenCV mp4v writer unavailable")
    rng = np.random.default_rng(33)
    for _ in range(12):
        writer.write(rng.integers(0, 256, (128, 128, 3), dtype=np.uint8))
    writer.release()
    obs = observation({"a": list(range(12))})
    obs.working_hash = sha256_file(path)
    obs.frame_count = 12
    grouper = ArcFaceIdentityGrouper(store, tmp_path / "missing.onnx")
    calls = []
    grouper.embedder.embed_aligned = lambda frame: calls.append(frame.shape) or vector()
    result = grouper.group(obs, {"identity_max_samples": 3}, "one")
    assert len(calls) == 3
    frames = result.tracks[0].sample_frames
    assert len(frames) == 3
    assert 0 <= frames[0] < 4 <= frames[1] < 8 <= frames[2] < 12
    assert store.resolve(result.tracks[0].thumbnail).is_file()
    assert result.tracks[0].thumbnail.startswith("runs/identity-cache/")

    def slow_embedding(frame):
        time.sleep(.03)
        return vector()

    grouper.embedder.embed_aligned = slow_embedding
    with pytest.raises(VideoProcessingError, match="timed out"):
        grouper.group(obs, {"identity_max_samples": 2, "video_timeout_seconds": .01}, "timeout")


def test_non_face_tracks_bypass_identity_and_empty_faces_require_no_model(tmp_path):
    grouper = ArcFaceIdentityGrouper(ArtifactStore(tmp_path), tmp_path / "missing.onnx")
    obs = observation({"car": [0, 1]})
    obs.tracks[0].object_type = "vehicle"
    result = grouper.group(obs, {}, "objects-only")
    assert result.groups == result.tracks == []
    assert result.metrics["embedding_count"] == 0
    assert grouper.embedder.session is None
    assert obs.tracks[0].object_type == "vehicle"


def test_embedder_lazy_init_explicit_model_failures_and_pinned_cpu_smoke(tmp_path):
    embedder = ArcFaceEmbedder(tmp_path / "missing.onnx")
    assert embedder.session is None
    with pytest.raises(VideoProcessingError, match="missing"):
        embedder.embed_aligned(np.zeros((112, 112, 3), dtype=np.uint8))
    wrong = tmp_path / "wrong.onnx"
    wrong.write_bytes(b"wrong")
    with pytest.raises(VideoProcessingError, match="checksum"):
        ArcFaceEmbedder(wrong).embed_aligned(np.zeros((112, 112, 3), dtype=np.uint8))
    model = Path(__file__).resolve().parents[2] / "models/w600k_r50.onnx"
    if not model.is_file():
        pytest.skip("Run scripts/download_model.py for the pinned ArcFace integration check")
    result = ArcFaceEmbedder(model).embed_aligned(np.zeros((112, 112, 3), dtype=np.uint8))
    assert result.shape == (512,)
    assert np.linalg.norm(result) == pytest.approx(1)


def test_downloader_verifies_archive_and_model_and_preserves_existing_files(tmp_path, monkeypatch):
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/download_model.py"))
    payload = b"pinned recognition model"
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("w600k_r50.onnx", payload)
    data = archive_buffer.getvalue()
    model = {"url": "https://example.test/model.zip", "size": len(payload),
             "sha256": hashlib.sha256(payload).hexdigest(), "archive_member": "w600k_r50.onnx",
             "archive_size": len(data), "archive_sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(module["urllib"].request, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    path = tmp_path / "model.onnx"
    assert module["download"](path, model=model) == path
    assert path.read_bytes() == payload
    assert module["download"](path, model=model) == path
    path.write_bytes(b"local change")
    with pytest.raises(ValueError, match="Existing model checksum"):
        module["download"](path, model=model)
    assert path.read_bytes() == b"local change"
    with pytest.raises(ValueError, match="pinned SHA-256"):
        module["download"](tmp_path / "bad.onnx", model={**model, "archive_sha256": "wrong"})
    assert not list(tmp_path.glob("*.download"))
