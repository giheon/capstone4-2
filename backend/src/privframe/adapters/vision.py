"""CPU video adapters using one normalized coordinate and frame basis.

Tracks represent nearby detections, not an identity claim. Missing detections are
never invented or rendered, even when the tracker reconnects a short gap.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from privframe.adapters.model_manifest import YUNET_MODEL
from privframe.domain.models import (
    Action, Box, Detection, RenderResult, Track, TrackPoint, Treatment, VideoObservation, VideoRecord,
)
from privframe.infrastructure.storage import ArtifactStore, sha256_file


ANALYSIS_VERSION = "opencv-frame-analysis-v1"
RENDER_VERSION = "opencv-face-redaction-v1"
NORMALIZATION_VERSION = "ffmpeg-autorotate-cfr-v2"


class VideoProcessingError(RuntimeError):
    """Invalid media, unavailable models, failed codecs, or an interrupted stage."""


@dataclass(frozen=True)
class VisionSettings:
    detector: str = "yunet"
    detector_threshold: float = 0.8
    detector_max_side: int = 640
    normalization_fps: float = 30.0
    tracker_iou_threshold: float = 0.3
    tracker_max_gap: int = 2
    scene_cut_threshold: float = 0.45
    video_timeout_seconds: float = 300.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> VisionSettings:
        # The application validates the complete flat config. Only vision keys
        # enter this adapter/fingerprint, so a retrieval change can reuse analysis.
        settings = cls(**{key: config[key] for key in cls.__dataclass_fields__ if key in config})
        if settings.detector not in {"yunet", "haar"}:
            raise VideoProcessingError(f"Unsupported detector: {settings.detector}")
        if not 0 < settings.detector_threshold <= 1:
            raise VideoProcessingError("detector_threshold must be in (0, 1]")
        if not isinstance(settings.detector_max_side, int) or not 64 <= settings.detector_max_side <= 640:
            raise VideoProcessingError("detector_max_side must be an integer between 64 and 640")
        if not 1 <= settings.normalization_fps <= 60:
            raise VideoProcessingError("normalization_fps must be between 1 and 60")
        if not 0 < settings.tracker_iou_threshold <= 1:
            raise VideoProcessingError("tracker_iou_threshold must be in (0, 1]")
        if not isinstance(settings.tracker_max_gap, int) or not 0 <= settings.tracker_max_gap <= 2:
            raise VideoProcessingError("tracker_max_gap must be an integer between 0 and 2")
        if not 0 < settings.scene_cut_threshold <= 1:
            raise VideoProcessingError("scene_cut_threshold must be in (0, 1]")
        if not math.isfinite(settings.video_timeout_seconds) or settings.video_timeout_seconds <= 0:
            raise VideoProcessingError("video_timeout_seconds must be a positive finite value")
        return settings


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise VideoProcessingError("Artifact identifiers may contain only letters, digits, '_' and '-'")
    return value


def _binary(name: str) -> str:
    binary = shutil.which(name)
    if binary is None:
        raise VideoProcessingError(f"{name} is required on PATH")
    return binary


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoProcessingError(f"{Path(args[0]).name} timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        raise VideoProcessingError(f"Cannot execute {Path(args[0]).name}: {exc}") from exc
    if result.returncode:
        raise VideoProcessingError(f"{Path(args[0]).name} failed: {result.stderr[-4000:].strip()}")
    return result


def probe_video(path: Path, timeout: float = 30.0) -> dict[str, Any]:
    result = _run([
        _binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ], timeout)
    try:
        data = json.loads(result.stdout)
        streams = [stream for stream in data["streams"] if stream.get("codec_type") == "video"]
        if not streams:
            raise ValueError("no video stream")
        video = streams[0]
        width, height = int(video["width"]), int(video["height"])
        if width <= 0 or height <= 0:
            raise ValueError("invalid frame dimensions")
        rate = video.get("avg_frame_rate", "0/1")
        if rate in {"0/0", "0/1", "N/A"}:
            rate = video.get("r_frame_rate", "0/1")
        count = video.get("nb_frames", "0")
        return {
            "width": width,
            "height": height,
            "fps": float(Fraction(rate)) if rate not in {"0/0", "N/A"} else 0.0,
            "frame_count": int(count) if count not in {"N/A", None} else 0,
            "duration": float(video.get("duration") or data.get("format", {}).get("duration") or 0),
            "has_audio": any(stream.get("codec_type") == "audio" for stream in data["streams"]),
            "audio_stream_count": sum(stream.get("codec_type") == "audio" for stream in data["streams"]),
            "rotation": [entry.get("rotation") for entry in video.get("side_data_list", [])
                         if "rotation" in entry],
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise VideoProcessingError(f"Invalid video metadata for {path.name}: {exc}") from exc


def _clamp_box(x: float, y: float, width: float, height: float,
               frame_width: int, frame_height: int) -> Box | None:
    if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
        return None
    left, top = max(0, math.floor(x)), max(0, math.floor(y))
    right, bottom = min(frame_width, math.ceil(x + width)), min(frame_height, math.ceil(y + height))
    if left >= right or top >= bottom:
        return None
    return Box(x=left, y=top, width=right - left, height=bottom - top)


def _small_frame(frame: np.ndarray, max_side: int) -> tuple[np.ndarray, float, float]:
    height, width = frame.shape[:2]
    scale = min(1.0, max_side / max(width, height))
    small_width, small_height = max(1, round(width * scale)), max(1, round(height * scale))
    resized = frame if scale == 1 else cv2.resize(frame, (small_width, small_height),
                                                 interpolation=cv2.INTER_AREA)
    return resized, width / small_width, height / small_height


class YuNetDetector:
    def __init__(self, model_path: Path, threshold: float = 0.8, max_side: int = 640):
        if not model_path.is_file():
            raise VideoProcessingError("YuNet model is missing; run python scripts/download_model.py")
        self.model_hash = sha256_file(model_path)
        if self.model_hash != YUNET_MODEL["sha256"]:
            raise VideoProcessingError("YuNet model checksum mismatch; download the pinned 2023mar model")
        self.max_side = max_side
        try:
            self.model = cv2.FaceDetectorYN.create(
                str(model_path), "", (320, 320), threshold, 0.3, 5000,
                cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
            )
        except cv2.error as exc:
            raise VideoProcessingError(f"Cannot load YuNet with OpenCV {cv2.__version__}: {exc}") from exc

    def detect(self, frame: np.ndarray) -> list[Detection]:
        resized, x_scale, y_scale = _small_frame(frame, self.max_side)
        try:
            self.model.setInputSize((resized.shape[1], resized.shape[0]))
            _, faces = self.model.detect(resized)
        except cv2.error as exc:
            raise VideoProcessingError(f"YuNet inference failed: {exc}") from exc
        detections = []
        for face in ([] if faces is None else faces):
            x, y, width, height = map(float, face[:4])
            box = _clamp_box(x * x_scale, y * y_scale, width * x_scale, height * y_scale,
                             frame.shape[1], frame.shape[0])
            if box is not None:
                detections.append(Detection(box=box, confidence=max(0.0, min(1.0, float(face[-1])))))
        return detections


class HaarDetector:
    """Explicit alternate; sigmoid cascade scores are heuristic, not calibrated probabilities."""

    def __init__(self, threshold: float = 0.8, max_side: int = 640):
        path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self.model_hash = sha256_file(path)
        self.model = cv2.CascadeClassifier(str(path))
        if self.model.empty():
            raise VideoProcessingError("OpenCV's Haar cascade could not be loaded")
        self.threshold, self.max_side = threshold, max_side

    def detect(self, frame: np.ndarray) -> list[Detection]:
        resized, x_scale, y_scale = _small_frame(frame, self.max_side)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        boxes, _, weights = self.model.detectMultiScale3(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20), outputRejectLevels=True,
        )
        detections = []
        for coords, weight in zip(boxes, weights, strict=True):
            confidence = 1.0 / (1.0 + math.exp(-max(-100.0, min(100.0, float(weight)))))
            if confidence < self.threshold:
                continue
            x, y, width, height = map(float, coords)
            box = _clamp_box(x * x_scale, y * y_scale, width * x_scale, height * y_scale,
                             frame.shape[1], frame.shape[0])
            if box is not None:
                detections.append(Detection(box=box, confidence=confidence))
        return detections


def box_iou(first: Box, second: Box) -> float:
    width = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
    height = max(0, min(first.y + first.height, second.y + second.height) - max(first.y, second.y))
    intersection = width * height
    return intersection / (first.width * first.height + second.width * second.height - intersection)


class IoUTracker:
    """Greedy one-to-one overlap tracking with an explicit short-gap reconnect limit."""

    def __init__(self, iou_threshold: float = 0.3, max_gap: int = 2):
        if not 0 < iou_threshold <= 1 or not 0 <= max_gap <= 2:
            raise ValueError("IoU threshold must be in (0,1], max_gap between 0 and 2")
        self.iou_threshold, self.max_gap = iou_threshold, max_gap
        self._active: dict[str, tuple[int, Detection]] = {}
        self._counter = 0
        self._last_frame = -1

    def update(self, frame: int, time: float, detections: list[Detection],
               scene_cut: bool = False) -> dict[str, Detection]:
        if frame <= self._last_frame:
            raise ValueError("Tracker frames must increase monotonically")
        self._last_frame = frame
        if scene_cut:
            self._active.clear()
        self._active = {key: value for key, value in self._active.items()
                        if frame - value[0] <= self.max_gap + 1}
        candidates = sorted(
            (-box_iou(previous.box, current.box), key, index)
            for key, (_, previous) in self._active.items()
            for index, current in enumerate(detections)
            if box_iou(previous.box, current.box) >= self.iou_threshold
        )
        used_tracks: set[str] = set()
        used_detections: set[int] = set()
        result = {}
        for _, key, index in candidates:
            if key not in used_tracks and index not in used_detections:
                result[key] = detections[index]
                used_tracks.add(key)
                used_detections.add(index)
        for index, detection in enumerate(detections):
            if index not in used_detections:
                self._counter += 1
                result[f"face-{self._counter:04d}"] = detection
        for key, detection in result.items():
            self._active[key] = (frame, detection)
        return result


def scene_descriptor(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(cv2.resize(frame, (64, 64), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    histogram = cv2.calcHist([gray], [0], None, [32], [0, 256]).reshape(-1)
    return histogram / histogram.sum()


def is_scene_cut(previous: np.ndarray | None, current: np.ndarray, threshold: float) -> bool:
    return previous is not None and float(np.abs(previous - current).sum() / 2) >= threshold


class OpenCVVideoAnalyzer:
    def __init__(self, artifacts: ArtifactStore, model_path: Path):
        self.artifacts, self.model_path = artifacts, model_path

    def _model_hash(self, settings: VisionSettings) -> str:
        path = (self.model_path if settings.detector == "yunet" else
                Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        if not path.is_file():
            raise VideoProcessingError("Detector model missing; run python scripts/download_model.py")
        result = sha256_file(path)
        if settings.detector == "yunet" and result != YUNET_MODEL["sha256"]:
            raise VideoProcessingError("YuNet model checksum mismatch")
        return result

    def fingerprint(self, video: VideoRecord, config: dict[str, Any]) -> str:
        settings = VisionSettings.from_config(config)
        runtime = _run([_binary("ffmpeg"), "-version"], 10).stdout.splitlines()[0]
        payload = {
            "original_hash": video.content_hash,
            "settings": settings.__dict__,
            "model_hash": self._model_hash(settings),
            "opencv_version": cv2.__version__,
            "ffmpeg_version": runtime,
            "analysis_version": ANALYSIS_VERSION,
            "normalization_version": NORMALIZATION_VERSION,
            "tracker": "iou-greedy-v1",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def analyze(self, video: VideoRecord, config: dict[str, Any], run_id: str) -> VideoObservation:
        settings = VisionSettings.from_config(config)
        fingerprint = self.fingerprint(video, config)
        source = self.artifacts.resolve(video.artifact)
        if not source.is_file() or sha256_file(source) != video.content_hash:
            raise VideoProcessingError("Original video is missing or its checksum changed")
        original_metadata = probe_video(source, settings.video_timeout_seconds)
        prefix = f"runs/{_identifier(run_id)}/analysis-{uuid.uuid4().hex}"
        working = self.artifacts.resolve(f"{prefix}/working.mp4")
        working.parent.mkdir(parents=True, exist_ok=False)
        # FFmpeg enables rotation from stream metadata by default. The explicit
        # boolean option has incompatible argument parsing between versions.
        command = [
            _binary("ffmpeg"), "-nostdin", "-v", "error", "-n", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-vf",
            f"fps={settings.normalization_fps:g},pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-threads", "1", "-c:a", "aac", "-b:a", "128k",
            "-map_metadata", "-1", "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(working),
        ]
        try:
            _run(command, settings.video_timeout_seconds)
        except VideoProcessingError:
            working.unlink(missing_ok=True)
            raise
        metadata = probe_video(working, settings.video_timeout_seconds)
        if abs(metadata["fps"] - settings.normalization_fps) > 0.01:
            raise VideoProcessingError("Normalized video frame rate does not match its configured basis")
        if metadata["audio_stream_count"] != original_metadata["audio_stream_count"]:
            raise VideoProcessingError("Normalization did not preserve all audio streams")
        detector = (YuNetDetector(self.model_path, settings.detector_threshold, settings.detector_max_side)
                    if settings.detector == "yunet" else
                    HaarDetector(settings.detector_threshold, settings.detector_max_side))
        tracker = IoUTracker(settings.tracker_iou_threshold, settings.tracker_max_gap)
        capture = cv2.VideoCapture(str(working))
        if not capture.isOpened():
            raise VideoProcessingError("OpenCV could not decode the normalized video")
        points: dict[str, list[TrackPoint]] = {}
        thumbnails: dict[str, str] = {}
        cuts, gaps = [], []
        previous = None
        frame_index = 0
        deadline = time.monotonic() + settings.video_timeout_seconds
        try:
            while True:
                if time.monotonic() > deadline:
                    raise VideoProcessingError("Video analysis timed out")
                success, frame = capture.read()
                if not success:
                    break
                if frame.shape[:2] != (metadata["height"], metadata["width"]):
                    raise VideoProcessingError("Decoded frame dimensions do not match the normalized video")
                descriptor = scene_descriptor(frame)
                cut = is_scene_cut(previous, descriptor, settings.scene_cut_threshold)
                if cut:
                    cuts.append(frame_index)
                previous = descriptor
                assignments = tracker.update(frame_index, frame_index / metadata["fps"],
                                             detector.detect(frame), scene_cut=cut)
                for track_id, detection in assignments.items():
                    if track_id in points and frame_index - points[track_id][-1].frame > 1:
                        gaps.append({"track_id": track_id, "after_frame": points[track_id][-1].frame,
                                     "before_frame": frame_index, "rendered": False})
                    points.setdefault(track_id, []).append(TrackPoint(
                        frame=frame_index, time=frame_index / metadata["fps"],
                        box=detection.box, confidence=detection.confidence,
                    ))
                    if track_id not in thumbnails:
                        box = detection.box
                        crop = frame[box.y:box.y + box.height, box.x:box.x + box.width]
                        crop, _, _ = _small_frame(crop, 160)
                        encoded, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if not encoded:
                            raise VideoProcessingError("Could not encode face thumbnail")
                        thumbnails[track_id] = self.artifacts.put_bytes(
                            f"{prefix}/thumbnails/{track_id}.jpg", buffer.tobytes(),
                        )
                frame_index += 1
        finally:
            capture.release()
        if not frame_index or (metadata["frame_count"] and frame_index != metadata["frame_count"]):
            raise VideoProcessingError("Normalized video could not be decoded completely")
        tracks = [Track(id=key, points=value, thumbnail=thumbnails[key], start=value[0].time,
                        end=min(frame_index / metadata["fps"], (value[-1].frame + 1) / metadata["fps"]))
                  for key, value in sorted(points.items())]
        return VideoObservation(
            video_id=video.id, original_hash=video.content_hash, working_artifact=self.artifacts.relative(working),
            working_hash=sha256_file(working), width=metadata["width"], height=metadata["height"],
            fps=metadata["fps"], frame_count=frame_index, duration=frame_index / metadata["fps"],
            has_audio=metadata["has_audio"], tracks=tracks, detector=settings.detector,
            tracker="iou-greedy-v1", analysis_fingerprint=fingerprint,
            normalization={
                "version": NORMALIZATION_VERSION, "analysis_version": ANALYSIS_VERSION,
                "ffmpeg_version": _run([_binary("ffmpeg"), "-version"], 10).stdout.splitlines()[0],
                "opencv_version": cv2.__version__, "command": command,
                "input_dimensions": [original_metadata["width"], original_metadata["height"]],
                "input_rotation": original_metadata["rotation"], "settings": settings.__dict__,
                "detector_model_hash": detector.model_hash, "scene_cut_frames": cuts,
                "detector_asset": dict(YUNET_MODEL) if settings.detector == "yunet" else {
                    "name": "haarcascade_frontalface_default.xml", "opencv_version": cv2.__version__,
                    "sha256": detector.model_hash,
                },
                "detection_interval": 1, "gap_policy": "no_interpolation", "detection_gaps": gaps,
                "track_semantics": "temporal_overlap_not_identity",
                "haar_confidence": "sigmoid_of_uncalibrated_cascade_score" if settings.detector == "haar" else None,
                "audio_stream_count": metadata["audio_stream_count"],
            },
        )


def apply_treatment(frame: np.ndarray, box: Box, treatment: Treatment) -> dict[str, Any]:
    """Modify a single observed box in place and return the actual pixel parameters."""
    if box.x + box.width > frame.shape[1] or box.y + box.height > frame.shape[0]:
        raise VideoProcessingError("Observed box exceeds its normalized frame bounds")
    if treatment.action == Action.KEEP:
        return {"action": "KEEP", "box": box.model_dump(), "margin_ratio": 0}
    strength = treatment.strength
    if strength not in {1, 2, 3}:
        raise VideoProcessingError("Redaction requires strength 1, 2, or 3")
    margin = {1: 0.1, 2: 0.2, 3: 0.3}[strength]
    expanded = _clamp_box(box.x - box.width * margin, box.y - box.height * margin,
                          box.width * (1 + 2 * margin), box.height * (1 + 2 * margin),
                          frame.shape[1], frame.shape[0])
    if expanded is None:
        raise VideoProcessingError("Cannot render an empty box")
    roi = frame[expanded.y:expanded.y + expanded.height, expanded.x:expanded.x + expanded.width]
    params: dict[str, Any] = {"action": str(treatment.action), "strength": strength,
                              "margin_ratio": margin, "box": expanded.model_dump()}
    side = min(expanded.width, expanded.height)
    if treatment.action == Action.BLUR:
        kernel = max(3, int(math.ceil(side * {1: 0.2, 2: 0.4, 3: 0.7}[strength])) | 1)
        sigma = max(3.0, side * {1: 0.06, 2: 0.12, 3: 0.2}[strength])
        roi[:] = cv2.GaussianBlur(roi, (kernel, kernel), sigmaX=sigma, sigmaY=sigma)
        params.update(kernel_size=kernel, sigma=sigma)
    elif treatment.action == Action.MOSAIC:
        block = max(4, math.ceil(side * {1: 0.08, 2: 0.16, 3: 0.25}[strength]))
        grid = (max(1, math.ceil(expanded.width / block)), max(1, math.ceil(expanded.height / block)))
        small = cv2.resize(roi, grid, interpolation=cv2.INTER_AREA)
        roi[:] = cv2.resize(small, (expanded.width, expanded.height), interpolation=cv2.INTER_NEAREST)
        params.update(block_size=block, grid_size=list(grid))
    elif treatment.action == Action.MASK:
        roi[:] = 0
        params.update(fill_rgb=[0, 0, 0], opacity=1.0)
    else:
        raise VideoProcessingError(f"Unsupported treatment: {treatment.action}")
    return params


class OpenCVRenderer:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts

    def render(self, observation: VideoObservation, treatments: dict[str, Treatment],
               run_id: str, attempt_id: str) -> RenderResult:
        track_ids = [track.id for track in observation.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise VideoProcessingError("Observation contains duplicate track IDs")
        if set(treatments) != set(track_ids):
            missing, extra = set(track_ids) - set(treatments), set(treatments) - set(track_ids)
            raise VideoProcessingError(f"A complete treatment map is required; missing={sorted(missing)}, "
                                       f"unknown={sorted(extra)}")
        treatments = {key: Treatment.model_validate(value) for key, value in treatments.items()}
        if observation.width <= 0 or observation.height <= 0 or observation.fps <= 0 or observation.frame_count <= 0:
            raise VideoProcessingError("Observation has invalid video dimensions, rate, or frame count")
        if observation.width % 2 or observation.height % 2:
            raise VideoProcessingError("Renderer requires even dimensions from canonical normalization")
        source = self.artifacts.resolve(observation.working_artifact)
        if not source.is_file() or sha256_file(source) != observation.working_hash:
            raise VideoProcessingError("Working video is missing or its checksum changed")
        source_metadata = probe_video(source)
        if ((source_metadata["width"], source_metadata["height"], source_metadata["frame_count"]) !=
                (observation.width, observation.height, observation.frame_count)
                or abs(source_metadata["fps"] - observation.fps) > 0.01
                or source_metadata["has_audio"] != observation.has_audio):
            raise VideoProcessingError("Observation does not match the working video's coordinate and frame basis")
        schedule: dict[int, list[tuple[str, Box]]] = {}
        severity = {Action.KEEP: 0, Action.BLUR: 1, Action.MOSAIC: 2, Action.MASK: 3}
        for track in sorted(observation.tracks, key=lambda item: (severity[treatments[item.id].action], item.id)):
            frames = set()
            for point in track.points:
                if point.frame in frames or point.frame >= observation.frame_count:
                    raise VideoProcessingError("Track points contain duplicate or out-of-range frames")
                frames.add(point.frame)
                if point.box.x + point.box.width > observation.width or point.box.y + point.box.height > observation.height:
                    raise VideoProcessingError("Observed box exceeds its normalized frame bounds")
                schedule.setdefault(point.frame, []).append((track.id, point.box))
        prefix = f"runs/{_identifier(run_id)}/renders/{_identifier(attempt_id)}"
        output = self.artifacts.resolve(f"{prefix}/output.mp4")
        if output.parent.exists():
            raise VideoProcessingError("Render attempt already exists; use a new attempt ID")
        output.parent.mkdir(parents=True, exist_ok=False)
        silent = output.with_name("silent.partial.mp4")
        temporary = output.with_name("output.partial.mp4")
        timeout = float(observation.normalization.get("settings", {}).get("video_timeout_seconds", 300))
        ffmpeg = _binary("ffmpeg")
        command = [
            ffmpeg, "-nostdin", "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s:v", f"{observation.width}x{observation.height}", "-r", f"{observation.fps:g}", "-i", "pipe:0",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-fps_mode", "cfr", "-threads", "1", "-movflags", "+faststart", str(silent),
        ]
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise VideoProcessingError("OpenCV could not decode the working video")
        records: dict[str, list[dict[str, Any]]] = {key: [] for key in track_ids}
        timed_out = threading.Event()
        process = None
        timer = None
        frame_count = 0
        try:
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)

                def kill_encoder() -> None:
                    timed_out.set()
                    if process.poll() is None:
                        process.kill()

                timer = threading.Timer(timeout, kill_encoder)
                timer.daemon = True
                timer.start()
                try:
                    for index in range(observation.frame_count):
                        success, frame = capture.read()
                        if not success:
                            raise VideoProcessingError("Working video ended before the observed frame count")
                        if frame.shape[:2] != (observation.height, observation.width):
                            raise VideoProcessingError("Working video dimensions changed")
                        for track_id, box in schedule.get(index, []):
                            params = apply_treatment(frame, box, treatments[track_id])
                            records[track_id].append({"frame": index, **params})
                        process.stdin.write(frame.tobytes())
                        frame_count += 1
                    if capture.read()[0]:
                        raise VideoProcessingError("Working video exceeds the observed frame count")
                    process.stdin.close()
                    process.wait(timeout=timeout)
                except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
                    message = "Video rendering timed out" if timed_out.is_set() else "Video encoder stopped unexpectedly"
                    raise VideoProcessingError(message) from exc
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                if timed_out.is_set():
                    raise VideoProcessingError("Video rendering timed out")
                if process.returncode:
                    errors.seek(0)
                    raise VideoProcessingError(f"Video encoder failed: {errors.read()[-4000:].decode(errors='replace')}")
            mux_command = [
                ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(silent), "-i", str(source),
                "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy",
                "-map_metadata", "-1", "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(temporary),
            ]
            _run(mux_command, timeout)
            metadata = probe_video(temporary, timeout)
            if (metadata["width"], metadata["height"], metadata["frame_count"]) != (
                observation.width, observation.height, observation.frame_count,
            ) or metadata["has_audio"] != observation.has_audio or abs(metadata["fps"] - observation.fps) > 0.01:
                raise VideoProcessingError("Rendered video does not match the observed dimensions, frame count, or audio")
            temporary.rename(output)
            return RenderResult(
                artifact=self.artifacts.relative(output), content_hash=sha256_file(output), treatments=treatments,
                frame_count=frame_count, has_audio=metadata["has_audio"],
                parameters={
                    "version": RENDER_VERSION, "opencv_version": cv2.__version__,
                    "ffmpeg_version": _run([ffmpeg, "-version"], 10).stdout.splitlines()[0],
                    "coordinate_basis": "normalized_video_pixels", "frame_basis": "zero_based_cfr",
                    "working_hash": observation.working_hash, "gap_policy": "no_interpolation",
                    "tracks": records, "encode_command": command, "mux_command": mux_command,
                    "audio": "copy_all_normalized_audio_streams", "crf": 18,
                },
            )
        except OSError as exc:
            raise VideoProcessingError(f"Video renderer I/O failure: {exc}") from exc
        finally:
            capture.release()
            if timer is not None:
                timer.cancel()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if process is not None and process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            silent.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
