"""Offline face identity grouping; frame tracks remain the rendering authority.

Embeddings are private, content-addressed artifacts. They never enter API
snapshots, prompts, or a cross-video identity database.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from privframe.adapters.model_manifest import ARCFACE_MODEL
from privframe.adapters.vision import VideoProcessingError
from privframe.domain.models import (
    IdentityCandidate, IdentityGroup, IdentityGrouping, IdentityTrackSummary, VideoObservation,
)
from privframe.infrastructure.storage import ArtifactStore, sha256_file

FEATURE_VERSION = "arcface-track-features-v2"
GROUPING_VERSION = "constrained-complete-link-v1"
ARCFACE_TEMPLATE = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float64)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def normalize_feature(value: Any) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64).reshape(-1)
    norm = np.linalg.norm(vector)
    if vector.size == 0 or not np.isfinite(vector).all() or not math.isfinite(norm) or norm <= 1e-12:
        raise VideoProcessingError("Face embedding is empty, non-finite, or has zero length")
    return vector / norm


def align_face(frame: np.ndarray, landmarks: Any) -> np.ndarray:
    """Least-squares similarity transform, without RANSAC/randomness or reflection."""
    source = np.asarray(landmarks, dtype=np.float64)
    if source.shape != (5, 2) or not np.isfinite(source).all():
        raise VideoProcessingError("ArcFace requires five finite face landmarks")
    source_mean, target_mean = source.mean(axis=0), ARCFACE_TEMPLATE.mean(axis=0)
    centered, target = source - source_mean, ARCFACE_TEMPLATE - target_mean
    variance = np.sum(centered ** 2)
    if variance <= 1e-8 or np.linalg.norm(source[0] - source[1]) < 1:
        raise VideoProcessingError("Face landmarks are degenerate")
    u, singular, vt = np.linalg.svd(target.T @ centered)
    reflection = np.ones(2)
    reflection[-1] = 1 if np.linalg.det(u @ vt) >= 0 else -1
    rotation = u @ np.diag(reflection) @ vt
    scale = float(singular @ reflection / variance)
    if scale <= 0:
        raise VideoProcessingError("Face alignment has an invalid scale")
    matrix = np.column_stack((scale * rotation, target_mean - scale * rotation @ source_mean))
    return cv2.warpAffine(frame, matrix, (112, 112), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


class ArcFaceEmbedder:
    """Lazy CPU inference with the exact pinned WebFace600K recognition model."""

    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.session = None

    def _load(self):
        if self.session is not None:
            return self.session
        if not self.model_path.is_file():
            raise VideoProcessingError("ArcFace model is missing; run python scripts/download_model.py")
        if sha256_file(self.model_path) != ARCFACE_MODEL["sha256"]:
            raise VideoProcessingError("ArcFace model checksum mismatch; download the pinned w600k_r50 model")
        try:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                                providers=["CPUExecutionProvider"])
        except (ImportError, RuntimeError, ValueError) as exc:
            raise VideoProcessingError(f"Cannot load ArcFace CPU inference: {exc}") from exc
        return self.session

    def embed(self, frame: np.ndarray, landmarks: Any) -> np.ndarray:
        return self.embed_aligned(align_face(frame, landmarks))

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        session = self._load()
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112),
                                     (127.5, 127.5, 127.5), swapRB=True)
        try:
            result = session.run(None, {session.get_inputs()[0].name: blob})[0]
        except Exception as exc:
            raise VideoProcessingError(f"ArcFace inference failed: {exc}") from exc
        feature = normalize_feature(result)
        if feature.shape != (512,):
            raise VideoProcessingError("ArcFace returned an unexpected embedding dimension")
        return feature


@dataclass(frozen=True)
class IdentitySettings:
    identity_similarity_threshold: float = 0.60
    identity_candidate_threshold: float = 0.40
    identity_consistency_threshold: float = 0.35
    identity_min_face_size: int = 48
    identity_max_samples: int = 5
    video_timeout_seconds: float = 300

    @classmethod
    def from_config(cls, config: dict) -> IdentitySettings:
        value = cls(**{key: config[key] for key in cls.__dataclass_fields__ if key in config})
        if not (-1 <= value.identity_candidate_threshold <= value.identity_similarity_threshold <= 1):
            raise VideoProcessingError("Identity candidate threshold must not exceed the merge threshold")
        if not -1 <= value.identity_consistency_threshold <= 1:
            raise VideoProcessingError("Identity consistency threshold must be between -1 and 1")
        if not 1 <= value.identity_max_samples <= 20 or value.identity_min_face_size < 1:
            raise VideoProcessingError("Invalid identity sample count or minimum face size")
        if not math.isfinite(value.video_timeout_seconds) or value.video_timeout_seconds <= 0:
            raise VideoProcessingError("Identity timeout must be positive and finite")
        return value


def constrained_clusters(track_ids: list[str], features: dict[str, np.ndarray],
                         observed_frames: dict[str, set[int]], threshold: float) -> list[list[str]]:
    """Complete-link HAC with actual co-detections as cannot-link constraints.

    At most one face can occupy an identity in a frame during automatic grouping.
    Manual corrections can account for mirrors or screens elsewhere in the app.
    """
    clusters = [[track_id] for track_id in track_ids]
    similarities = {}
    for index, first in enumerate(track_ids):
        for second in track_ids[index + 1:]:
            if (first not in features or second not in features or
                    observed_frames[first] & observed_frames[second]):
                score = -math.inf
            else:
                score = float(np.clip(features[first] @ features[second], -1, 1))
            similarities[frozenset((first, second))] = score
    while True:
        best = None
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                score = min(similarities[frozenset((a, b))] for a in clusters[left] for b in clusters[right])
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, left, right)
        if best is None:
            break
        _, left, right = best
        clusters[left] = sorted(clusters[left] + clusters.pop(right), key=track_ids.index)
    return clusters


class ArcFaceIdentityGrouper:
    def __init__(self, artifacts: ArtifactStore, model_path: Path):
        self.artifacts = artifacts
        self.model_path = model_path
        self.embedder = ArcFaceEmbedder(model_path)  # No file/network/inference access here.

    def feature_fingerprint(self, observation: VideoObservation, config: dict) -> str:
        settings = IdentitySettings.from_config(config)
        return _fingerprint({
            "version": FEATURE_VERSION, "working_hash": observation.working_hash,
            "analysis_fingerprint": observation.analysis_fingerprint,
            "tracks": [track.model_dump(mode="json", exclude={"thumbnail"}) for track in observation.tracks
                       if track.object_type == "face"],
            "model_sha256": ARCFACE_MODEL["sha256"],
            "min_face_size": settings.identity_min_face_size, "max_samples": settings.identity_max_samples,
        })

    def fingerprint(self, observation: VideoObservation, config: dict) -> str:
        settings = IdentitySettings.from_config(config)
        return _fingerprint({
            "version": GROUPING_VERSION, "features": self.feature_fingerprint(observation, config),
            "merge": settings.identity_similarity_threshold, "candidate": settings.identity_candidate_threshold,
            "consistency": settings.identity_consistency_threshold,
        })

    def _read_features(self, path: str, fingerprint: str, track_ids: set[str]) -> dict | None:
        try:
            value = json.loads(self.artifacts.resolve(path).read_text())
            if value["fingerprint"] != fingerprint or set(value["tracks"]) != track_ids:
                return None
            for entry in value["tracks"].values():
                for sample in entry["samples"]:
                    if len(normalize_feature(sample["embedding"])) != 512:
                        return None
                    if not self.artifacts.resolve(sample["thumbnail"]).is_file():
                        return None
            return value
        except (KeyError, TypeError, ValueError, OSError, VideoProcessingError):
            return None

    def _extract(self, observation: VideoObservation, settings: IdentitySettings,
                 fingerprint: str) -> dict:
        deadline = time.monotonic() + settings.video_timeout_seconds

        def check_deadline():
            if time.monotonic() > deadline:
                raise VideoProcessingError("Identity feature extraction timed out")

        if not observation.tracks:
            return {"fingerprint": fingerprint, "version": FEATURE_VERSION, "tracks": {}}
        source = self.artifacts.resolve(observation.working_artifact)
        if not source.is_file() or sha256_file(source) != observation.working_hash:
            raise VideoProcessingError("Working video is missing or its checksum has changed")
        entries = {track.id: {"samples": [], "issues": []} for track in observation.tracks}
        schedule: dict[int, list[tuple[str, int, Any]]] = {}
        # Evaluate up to ten candidates in each temporal bin, then embed just the winner.
        for track in observation.tracks:
            check_deadline()
            eligible = [point for point in track.points
                        if point.landmarks is not None and min(point.box.width, point.box.height) >=
                        settings.identity_min_face_size and point.confidence >= 0.5]
            if not eligible:
                entries[track.id]["issues"] = ["missing_landmarks" if not any(
                    point.landmarks is not None for point in track.points) else "low_quality"]
                continue
            bins = np.array_split(np.arange(len(eligible)), min(settings.identity_max_samples, len(eligible)))
            for bin_index, indices in enumerate(bins):
                chosen = indices[np.unique(np.linspace(0, len(indices) - 1, min(10, len(indices)), dtype=int))]
                for index in chosen:
                    point = eligible[int(index)]
                    schedule.setdefault(point.frame, []).append((track.id, bin_index, point))
        candidates: dict[tuple[str, int], list[tuple[float, Any, np.ndarray]]] = {}
        capture = cv2.VideoCapture(str(source))
        try:
            if not capture.isOpened():
                raise VideoProcessingError("Cannot open working video for identity grouping")
            # Sequential decoding keeps frame indices exact and avoids per-face random seeks.
            for frame_index in range(max(schedule, default=-1) + 1):
                check_deadline()
                success, frame = capture.read()
                if not success:
                    raise VideoProcessingError("Working video ended before identity sample frames")
                for track_id, bin_index, point in schedule.get(frame_index, []):
                    try:
                        aligned = align_face(frame, point.landmarks)
                    except (VideoProcessingError, np.linalg.LinAlgError):
                        continue
                    sharpness = float(cv2.Laplacian(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
                    if sharpness < 12:
                        continue
                    size_score = min(1, math.sqrt(point.box.width * point.box.height) / 160)
                    detail_score = min(1, math.log1p(sharpness) / math.log1p(1000))
                    quality = float(point.confidence * math.sqrt(size_score * detail_score))
                    key = (track_id, bin_index)
                    candidates.setdefault(key, []).append((quality, point, aligned))
        finally:
            capture.release()
        winners = {}
        for track in observation.tracks:
            last_frame = None
            min_separation = max(1, (track.points[-1].frame - track.points[0].frame) //
                                 (settings.identity_max_samples * 2)) if track.points else 1
            for bin_index in range(settings.identity_max_samples):
                choices = [item for item in candidates.get((track.id, bin_index), [])
                           if last_frame is None or item[1].frame - last_frame >= min_separation]
                if choices:
                    winner = max(choices, key=lambda item: item[0])
                    winners[(track.id, bin_index)] = winner
                    last_frame = winner[1].frame
        for (track_id, bin_index), (quality, point, aligned) in sorted(winners.items()):
            check_deadline()
            feature = self.embedder.embed_aligned(aligned)
            check_deadline()
            success, thumbnail = cv2.imencode(".jpg", aligned)
            if not success:
                raise VideoProcessingError("Cannot encode identity representative thumbnail")
            relative = f"runs/identity-cache/{fingerprint}/{track_id}-{bin_index}.jpg"
            self.artifacts.put_bytes(relative, thumbnail.tobytes())
            entries[track_id]["samples"].append({
                "frame": point.frame, "quality": quality, "embedding": feature.tolist(), "thumbnail": relative,
            })
        for entry in entries.values():
            if not entry["samples"] and not entry["issues"]:
                entry["issues"] = ["low_quality"]
        return {"fingerprint": fingerprint, "version": FEATURE_VERSION, "tracks": entries}

    def group(self, observation: VideoObservation, config: dict, run_id: str,
              cached: IdentityGrouping | None = None) -> IdentityGrouping:
        start = time.perf_counter()
        observation = observation.model_copy(update={
            "tracks": [track for track in observation.tracks if track.object_type == "face"],
        })
        settings = IdentitySettings.from_config(config)
        feature_fp = self.feature_fingerprint(observation, config)
        grouping_fp = self.fingerprint(observation, config)
        artifact = f"identity/features/{feature_fp}/features.json"
        cache_source = cached.features_artifact if cached and cached.feature_fingerprint == feature_fp else artifact
        data = self._read_features(cache_source, feature_fp, {track.id for track in observation.tracks}) if (
            cache_source is not None) else None
        reused = data is not None
        if data is None:
            data = self._extract(observation, settings, feature_fp)
            self.artifacts.put_bytes(artifact, json.dumps(data, allow_nan=False).encode())
        else:
            artifact = cache_source
        summaries, prototypes = [], {}
        for track in observation.tracks:
            entry = data["tracks"][track.id]
            samples = entry["samples"]
            issues = list(entry["issues"])
            consistency = None
            quality = 0.0
            thumbnail = track.thumbnail
            if samples:
                vectors = np.stack([normalize_feature(sample["embedding"]) for sample in samples])
                quality = float(np.mean([sample["quality"] for sample in samples]))
                best = max(samples, key=lambda sample: sample["quality"])
                thumbnail = best["thumbnail"]
                track.thumbnail = thumbnail
                similarities = vectors @ vectors.T
                consistency = float(np.clip(np.min(similarities), -1, 1)) if len(samples) > 1 else None
                if consistency is not None and consistency < settings.identity_consistency_threshold:
                    issues.append("mixed_identity")
                elif len(samples) < 2:
                    issues.append("insufficient_samples")
                else:
                    prototypes[track.id] = normalize_feature(np.average(
                        vectors, axis=0, weights=[sample["quality"] for sample in samples]))
            summaries.append(IdentityTrackSummary(
                track_id=track.id, sample_frames=[sample["frame"] for sample in samples],
                thumbnail=thumbnail, quality=quality, consistency=consistency, issues=issues,
            ))
        ordered = [track.id for track in sorted(observation.tracks, key=lambda track: (track.start, track.id))]
        frames = {track.id: {point.frame for point in track.points} for track in observation.tracks}
        clusters = constrained_clusters(ordered, prototypes, frames, settings.identity_similarity_threshold)
        summary_map = {summary.track_id: summary for summary in summaries}
        groups = []
        for index, cluster in enumerate(clusters, 1):
            representative = max((summary_map[track_id] for track_id in cluster), key=lambda item: item.quality)
            groups.append(IdentityGroup(
                id=f"person-{index:04d}", track_ids=cluster, thumbnail=representative.thumbnail,
                review_reasons=sorted({issue for track_id in cluster for issue in summary_map[track_id].issues}),
            ))
        memberships = {track_id: group.id for group in groups for track_id in group.track_ids}
        candidates = []
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                if (first not in prototypes or second not in prototypes or frames[first] & frames[second] or
                        memberships[first] == memberships[second]):
                    continue
                score = float(np.clip(prototypes[first] @ prototypes[second], -1, 1))
                if score >= settings.identity_candidate_threshold:
                    candidates.append(IdentityCandidate(
                        first_track_id=first, second_track_id=second, similarity=score))
        candidates.sort(key=lambda pair: (-pair.similarity, pair.first_track_id, pair.second_track_id))
        result = IdentityGrouping(
            feature_fingerprint=feature_fp, grouping_fingerprint=grouping_fp, features_artifact=artifact,
            automatic_groups=groups, groups=[group.model_copy(deep=True) for group in groups], tracks=summaries,
            candidates=candidates, model={key: ARCFACE_MODEL[key] for key in ("name", "version", "sha256", "license")},
            metrics={"feature_cache_reused": reused, "track_count": len(ordered), "group_count": len(groups),
                     "embedding_count": sum(len(entry["samples"]) for entry in data["tracks"].values()),
                     "excluded_track_count": len(ordered) - len(prototypes), "candidate_count": len(candidates),
                     "algorithm": GROUPING_VERSION}, elapsed_seconds=time.perf_counter() - start,
        )
        return result
