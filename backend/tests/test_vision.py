from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from privframe.adapters.vision import (
    HaarDetector, IoUTracker, OpenCVRenderer, OpenCVVideoAnalyzer, VideoProcessingError,
    VisionSettings, YuNetDetector, _clamp_box, _small_frame, apply_treatment, box_iou,
    is_scene_cut, probe_video, scene_descriptor,
)
from privframe.domain.models import Action, Box, Detection, Track, TrackPoint, Treatment, VideoObservation, VideoRecord
from privframe.infrastructure.storage import ArtifactStore, sha256_file


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def ffmpeg():
    binary = shutil.which("ffmpeg")
    if not binary or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for media integration tests")
    return binary


def make_video(store: ArtifactStore, ffmpeg: str, audio: bool = False) -> VideoRecord:
    path = store.resolve("videos/checker.mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    size = (96, 64)
    rows, columns = np.indices((size[1], size[0]))
    base = ((rows + columns) % 2 * 255).astype(np.uint8)
    frame = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)
    command = [ffmpeg, "-v", "error", "-n", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s:v", "96x64",
               "-r", "12", "-i", "pipe:0"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=0.5"]
    command += ["-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-threads", "1"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    subprocess.run(command, input=frame.tobytes() * 6, capture_output=True, check=True, timeout=30)
    return VideoRecord(id="source", filename=path.name, artifact=store.relative(path), content_hash=sha256_file(path))


def observation_for(video: VideoRecord, store: ArtifactStore, *, points=None, audio=False):
    info = probe_video(store.resolve(video.artifact))
    if points is None:
        points = [TrackPoint(frame=frame, time=frame / info["fps"],
                             box=Box(x=24, y=16, width=32, height=32), confidence=1)
                  for frame in range(info["frame_count"])]
    return VideoObservation(
        video_id=video.id, original_hash=video.content_hash, working_artifact=video.artifact,
        working_hash=video.content_hash, width=info["width"], height=info["height"], fps=info["fps"],
        frame_count=info["frame_count"], duration=info["duration"], has_audio=audio,
        tracks=[Track(id="face-0001", points=points, start=0, end=info["duration"])],
        detector="test", tracker="iou-greedy-v1", analysis_fingerprint="test",
    )


def read_frame(path: Path, index=0):
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        success, frame = capture.read()
        assert success
        return frame
    finally:
        capture.release()


def detection(x=10, y=10, width=30, height=30):
    return Detection(box=Box(x=x, y=y, width=width, height=height), confidence=0.9)


def test_iou_tracker_gap_limit_scene_reset_and_one_to_one():
    tracker = IoUTracker(max_gap=2)
    initial = tracker.update(0, 0, [detection(), detection(x=60)])
    assert set(initial) == {"face-0001", "face-0002"}
    assert tracker.update(1, 1 / 30, []) == {}
    assert tracker.update(2, 2 / 30, []) == {}
    resumed = tracker.update(3, 3 / 30, [detection(x=12), detection(x=61)])
    assert set(resumed) == set(initial)
    reset = tracker.update(4, 4 / 30, [detection(x=12)], scene_cut=True)
    assert set(reset) == {"face-0003"}
    for frame in (5, 6, 7):
        tracker.update(frame, frame / 30, [])
    assert set(tracker.update(8, 8 / 30, [detection(x=12)])) == {"face-0004"}
    with pytest.raises(ValueError, match="monotonically"):
        tracker.update(8, 8 / 30, [detection()])
    assert box_iou(detection().box, detection(x=100).box) == 0
    assert box_iou(detection().box, detection().box) == 1


def test_tracker_never_assigns_one_track_to_two_detections():
    tracker = IoUTracker()
    tracker.update(0, 0, [detection()])
    result = tracker.update(1, 1 / 30, [detection(), detection(x=11)])
    assert set(result) == {"face-0001", "face-0002"}


def test_downsampling_bounds_and_scene_change():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    resized, x_scale, y_scale = _small_frame(frame, 640)
    assert resized.shape == (360, 640, 3)
    assert x_scale == y_scale == 3
    assert _clamp_box(-5, -4, 10, 10, 96, 64) == Box(x=0, y=0, width=5, height=6)
    assert _clamp_box(200, 0, 1, 1, 96, 64) is None
    assert _clamp_box(float("nan"), 0, 1, 1, 96, 64) is None
    black, white = scene_descriptor(np.zeros((64, 64, 3), np.uint8)), scene_descriptor(
        np.full((64, 64, 3), 255, np.uint8),
    )
    assert is_scene_cut(black, white, 0.45)
    assert not is_scene_cut(black, black, 0.45)
    assert not is_scene_cut(None, white, 0.45)


@pytest.mark.parametrize("action", [Action.BLUR, Action.MOSAIC, Action.MASK])
def test_treatment_actual_parameters_strengths_and_clamped_margin(action):
    rng = np.random.default_rng(24)
    original = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    values = []
    for strength in (1, 2, 3):
        frame = original.copy()
        params = apply_treatment(frame, Box(x=0, y=0, width=30, height=30),
                                 Treatment(action=action, strength=strength))
        assert params["box"]["x"] == params["box"]["y"] == 0
        assert np.array_equal(frame[50:, 70:], original[50:, 70:])
        assert not np.array_equal(frame[:30, :30], original[:30, :30])
        values.append(params)
    assert values[0]["margin_ratio"] < values[1]["margin_ratio"] < values[2]["margin_ratio"]
    if action == Action.BLUR:
        assert values[0]["kernel_size"] < values[1]["kernel_size"] < values[2]["kernel_size"]
    if action == Action.MOSAIC:
        assert values[0]["block_size"] < values[1]["block_size"] < values[2]["block_size"]


def test_keep_has_no_pixel_effect_and_invalid_box_rejected():
    frame = np.full((64, 96, 3), 80, np.uint8)
    apply_treatment(frame, Box(x=10, y=10, width=20, height=20), Treatment(action=Action.KEEP))
    assert (frame == 80).all()
    with pytest.raises(VideoProcessingError, match="bounds"):
        apply_treatment(frame, Box(x=95, y=0, width=2, height=2), Treatment(action=Action.KEEP))


@pytest.mark.parametrize("audio", [False, True])
def test_real_render_preserves_audio_frames_masks_and_original(store, ffmpeg, audio):
    video = make_video(store, ffmpeg, audio)
    observation = observation_for(video, store, audio=audio)
    renderer = OpenCVRenderer(store)
    result = renderer.render(observation, {"face-0001": Treatment(action=Action.MASK, strength=2)},
                             "run", "attempt1")
    info = probe_video(store.resolve(result.artifact))
    assert (info["width"], info["height"], info["frame_count"]) == (96, 64, 6)
    assert result.has_audio == info["has_audio"] == audio
    assert result.frame_count == 6
    assert sha256_file(store.resolve(video.artifact)) == video.content_hash
    assert sha256_file(store.resolve(result.artifact)) == result.content_hash
    masked = read_frame(store.resolve(result.artifact))
    assert masked[20:40, 28:50].max() < 8
    assert len(result.parameters["tracks"]["face-0001"]) == 6
    if audio:
        # The mux stage copies normalized audio packets rather than silently dropping or re-encoding them.
        args = [ffmpeg, "-v", "error", "-i", str(store.resolve(video.artifact)), "-map", "0:a:0",
                "-c:a", "copy", "-f", "adts", "pipe:1"]
        before = subprocess.run(args, capture_output=True, check=True).stdout
        args[4] = str(store.resolve(result.artifact))
        after = subprocess.run(args, capture_output=True, check=True).stdout
        assert before == after
    with pytest.raises(VideoProcessingError, match="already exists"):
        renderer.render(observation, {"face-0001": Treatment(action=Action.KEEP)}, "run", "attempt1")
    another = renderer.render(observation, {"face-0001": Treatment(action=Action.KEEP)}, "run", "attempt2")
    assert another.artifact != result.artifact


def test_real_render_does_not_fill_undetected_gaps(store, ffmpeg):
    video = make_video(store, ffmpeg)
    observation = observation_for(video, store)
    observation.tracks[0].points = [observation.tracks[0].points[0], observation.tracks[0].points[2]]
    result = OpenCVRenderer(store).render(
        observation, {"face-0001": Treatment(action=Action.MASK, strength=3)}, "gap-run", "attempt",
    )
    first = read_frame(store.resolve(result.artifact), 0)
    gap = read_frame(store.resolve(result.artifact), 1)
    assert first[20:40, 28:50].max() < 8
    assert gap[20:40, 28:50].mean() > 80
    assert [point["frame"] for point in result.parameters["tracks"]["face-0001"]] == [0, 2]


def test_render_requires_complete_map_and_valid_observation(store, ffmpeg):
    video = make_video(store, ffmpeg)
    observation = observation_for(video, store)
    renderer = OpenCVRenderer(store)
    for treatments in ({}, {"unknown": Treatment(action=Action.KEEP)}):
        with pytest.raises(VideoProcessingError, match="complete treatment map"):
            renderer.render(observation, treatments, "run", "missing")
    observation.tracks[0].points[0].box = Box(x=90, y=0, width=10, height=10)
    with pytest.raises(VideoProcessingError, match="bounds"):
        renderer.render(observation, {"face-0001": Treatment(action=Action.KEEP)}, "run", "bounds")
    observation.tracks[0].points[0].box = Box(x=0, y=0, width=10, height=10)
    observation.fps = 30
    with pytest.raises(VideoProcessingError, match="frame basis"):
        renderer.render(observation, {"face-0001": Treatment(action=Action.KEEP)}, "run", "rate")


def test_render_timeout_kills_encoder_and_cleans_partial_output(store, ffmpeg, monkeypatch):
    from privframe.adapters import vision

    video = make_video(store, ffmpeg)
    observation = observation_for(video, store)
    observation.normalization = {"settings": {"video_timeout_seconds": 0.01}}
    original = vision.apply_treatment

    def slow_treatment(*args):
        time.sleep(0.05)
        return original(*args)

    monkeypatch.setattr(vision, "apply_treatment", slow_treatment)
    with pytest.raises(VideoProcessingError, match="timed out"):
        OpenCVRenderer(store).render(observation, {"face-0001": Treatment(action=Action.MASK, strength=3)},
                                     "timeout-run", "attempt")
    assert not store.resolve("runs/timeout-run/renders/attempt/output.mp4").exists()
    assert not list(store.resolve("runs/timeout-run/renders/attempt").glob("*.partial.mp4"))


def test_analysis_detects_every_frame_records_only_observed_points_and_thumbnail(store, ffmpeg, monkeypatch):
    from privframe.adapters import vision

    video = make_video(store, ffmpeg)

    class StubDetector:
        model_hash = "stub"
        calls = 0

        def detect(self, frame):
            index = self.calls
            self.calls += 1
            return [detection()] if index % 2 == 0 else []

    detector = StubDetector()
    monkeypatch.setattr(vision, "HaarDetector", lambda *args: detector)
    observation = OpenCVVideoAnalyzer(store, Path("unused")).analyze(video, {"detector": "haar"}, "track-run")
    assert detector.calls == observation.frame_count == 15
    assert len(observation.tracks) == 1
    track = observation.tracks[0]
    assert [point.frame for point in track.points] == list(range(0, 15, 2))
    assert all(point.time == point.frame / 30 for point in track.points)
    assert len(observation.normalization["detection_gaps"]) == 7
    assert all(gap["rendered"] is False for gap in observation.normalization["detection_gaps"])
    thumbnail = cv2.imread(str(store.resolve(track.thumbnail)))
    assert thumbnail.shape[:2] == (30, 30)


def test_no_face_observation_can_be_explicitly_rendered_without_boxes(store, ffmpeg):
    video = make_video(store, ffmpeg)
    observation = observation_for(video, store)
    observation.tracks = []
    result = OpenCVRenderer(store).render(observation, {}, "no-face-run", "attempt")
    assert result.treatments == {}
    assert result.frame_count == observation.frame_count


@pytest.mark.parametrize("rotation", [90, -90])
def test_normalization_rotation_audio_cfr_and_fingerprint_reuse(store, ffmpeg, rotation):
    video = make_video(store, ffmpeg, audio=True)
    rotated = store.resolve("videos/rotated.mp4")
    help_text = subprocess.run([ffmpeg, "-h", "full"], capture_output=True, text=True, check=True).stdout
    rotation_input = ["-display_rotation:v:0", str(rotation)] if "-display_rotation" in help_text else []
    rotation_output = [] if rotation_input else ["-metadata:s:v:0", f"rotate={rotation}"]
    subprocess.run([ffmpeg, "-v", "error", "-n", *rotation_input, "-i", str(store.resolve(video.artifact)),
                    "-c", "copy", *rotation_output, str(rotated)], capture_output=True, check=True)
    video = VideoRecord(id="rotated", filename=rotated.name, artifact=store.relative(rotated),
                        content_hash=sha256_file(rotated))
    config = {"detector": "haar", "normalization_fps": 30}
    analyzer = OpenCVVideoAnalyzer(store, Path("unused.onnx"))
    observation = analyzer.analyze(video, config, "rotation-run")
    assert (observation.width, observation.height) == (64, 96)
    assert observation.fps == 30
    assert observation.frame_count == 15
    assert observation.has_audio
    assert observation.normalization["input_rotation"] == [rotation]
    assert observation.normalization["gap_policy"] == "no_interpolation"
    assert analyzer.fingerprint(video, config) == observation.analysis_fingerprint
    assert analyzer.fingerprint(video, {**config, "chunk_size": 600, "llm_model": "different"}) == (
        observation.analysis_fingerprint
    )
    assert analyzer.fingerprint(video, {**config, "detector_threshold": 0.6}) != observation.analysis_fingerprint
    again = analyzer.analyze(video, config, "rotation-run")
    assert observation.working_artifact != again.working_artifact
    assert store.resolve(observation.working_artifact).is_file()


def test_detector_model_missing_or_tampered_is_explicit(tmp_path):
    with pytest.raises(VideoProcessingError, match="missing"):
        YuNetDetector(tmp_path / "missing.onnx")
    model = tmp_path / "wrong.onnx"
    model.write_bytes(b"not the correct detector")
    with pytest.raises(VideoProcessingError, match="checksum"):
        YuNetDetector(model)
    assert HaarDetector().detect(np.zeros((64, 64, 3), np.uint8)) == []


def test_pinned_yunet_model_cpu_smoke():
    model = Path(__file__).resolve().parents[2] / "models/face_detection_yunet_2023mar.onnx"
    if not model.is_file():
        pytest.skip("Download the pinned model with scripts/download_model.py to run YuNet integration")
    assert YuNetDetector(model).detect(np.zeros((96, 128, 3), np.uint8)) == []


@pytest.mark.parametrize("config", [{"detector": "invalid"}, {"detector_max_side": 1000},
                                    {"tracker_max_gap": 3}, {"normalization_fps": 0}])
def test_invalid_vision_configuration(config):
    with pytest.raises(VideoProcessingError):
        VisionSettings.from_config(config)
