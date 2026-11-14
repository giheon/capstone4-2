"""Service/pipeline acceptance boundaries with real local retrieval and video output."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from uuid import uuid4

import cv2
import pytest

from privframe.adapters.decision import FixtureDecider
from privframe.bootstrap import create_pipeline, create_service
from privframe.config import ROOT, Settings, fingerprint
from privframe.domain.models import (
    Box, DocumentRecord, RunSnapshot, ScenarioInput, SourceMetadata,
    Track, TrackPoint, Treatment, VideoObservation, VideoRecord,
)
from privframe.infrastructure.storage import sha256_file


class InlineDispatcher:
    def __init__(self):
        self.pipeline = None
        self.paused = False
        self.calls = []

    def send(self, task, *args):
        self.calls.append((task, args))
        if not self.paused:
            self.deliver(task, *args)
        return f"inline-{len(self.calls)}"

    def deliver(self, task, *args):
        operation = {"extract": self.pipeline.extract, "index": self.pipeline.build_index,
                     "stage": self.pipeline.run_stage, "component": self.pipeline.run_component}[task]
        operation(*args)


class SyntheticTracksAnalyzer:
    """Deterministic two-track observations on an actual canonical six-frame file."""

    def __init__(self):
        self.calls = 0
        self.on_analyze = None

    def fingerprint(self, video, config):
        return fingerprint({"video": video.content_hash, "detector": config["detector"],
                            "fps": config["normalization_fps"]})

    def analyze(self, video, config, run_id):
        self.calls += 1
        if self.on_analyze:
            self.on_analyze()
        return VideoObservation(
            video_id=video.id, original_hash=video.content_hash, working_artifact=video.artifact,
            working_hash=video.content_hash, width=64, height=64, fps=30, frame_count=6,
            duration=0.2, has_audio=False, detector=config["detector"], tracker="iou-test",
            analysis_fingerprint=self.fingerprint(video, config),
            tracks=[Track(id=f"track{number}", start=0, end=0.2, points=[
                TrackPoint(frame=frame, time=frame / 30, box=Box(x=x, y=16, width=16, height=16),
                           confidence=0.95)
                for frame in range(6)
            ]) for number, x in [(1, 8), (2, 40)]],
            normalization={"settings": {"video_timeout_seconds": 30}},
        )


@dataclass
class System:
    service: object
    pipeline: object
    dispatcher: InlineDispatcher
    analyzer: SyntheticTracksAnalyzer
    video: VideoRecord

    def add_video(self, data):
        identity = uuid4().hex
        artifact = f"videos/{identity}/original.mp4"
        self.service.artifacts.put_bytes(artifact, data)
        self.video = VideoRecord(id=identity, filename="synthetic.mp4", artifact=artifact,
                                 content_hash=sha256_file(self.service.artifacts.resolve(artifact)))
        return self.service.repository.insert("video", self.video)

    def add_document(self, text=None):
        identity = uuid4().hex
        text = text or ("공개된 장소에서 얼굴 영상을 촬영한 개인정보의 보호 및 이용 기준을 확인한다. " * 90)
        artifact = f"documents/{identity}/original.txt"
        self.service.artifacts.put_bytes(artifact, text.encode())
        record = DocumentRecord(id=identity, filename="source.txt", format="txt",
                                content_hash=sha256_file(self.service.artifacts.resolve(artifact)),
                                metadata=SourceMetadata(title="영상정보 보호 자료", kind="guide",
                                                        source_url="https://www.privacy.go.kr/", edition="test"))
        return self.service.queue_document(record)

    def add_index(self, document=None):
        document = document or self.add_document()
        value = self.service.create_index([document.id], "fixture")
        assert value.status == "READY", value.error
        return value

    def run(self, index=None):
        return self.service.create_run(self.video.id, index.id if index else None,
                                       ScenarioInput(context="공개 장소 얼굴 영상의 교육용 이용"), "fixture")

    def completed(self):
        run = self.run(self.add_index())
        run = self.service.request_stage(run.id, "decide")
        assert run.status == "REVIEW_READY", run.review_reasons
        run = self.service.request_stage(run.id, "render")
        assert run.status == "COMPLETED", run.error
        return run


@pytest.fixture(scope="session")
def blank_video_bytes(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for the video integration tests")
    path = tmp_path_factory.mktemp("canonical-video") / "blank.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=30",
        "-frames:v", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", str(path),
    ], check=True, timeout=30, capture_output=True)
    return path.read_bytes()


@pytest.fixture(scope="session")
def textured_video_bytes(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for the video integration tests")
    path = tmp_path_factory.mktemp("textured-video") / "pattern.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30",
        "-frames:v", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", str(path),
    ], check=True, timeout=30, capture_output=True)
    return path.read_bytes()


@pytest.fixture
def system(tmp_path, blank_video_bytes):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for name in ("baseline", "fixture"):
        shutil.copyfile(ROOT / f"configs/{name}.yaml", profiles / f"{name}.yaml")
    # Keep at least one retrieved candidate outside the actual LLM input budget.
    with (profiles / "fixture.yaml").open("a") as handle:
        handle.write("\nevidence_char_budget: 1200\n")
    (profiles / "fixture-other.yaml").write_text("extends: fixture\nname: fixture-other\ndetector: yunet\n")
    settings = Settings(data_dir=tmp_path / "data", profiles_dir=profiles,
                        model_path=ROOT / "models/face_detection_yunet_2023mar.onnx")
    dispatcher = InlineDispatcher()
    service = create_service(settings, dispatcher)
    # Freeze this test process's genuine runtime metadata while teammates edit
    # source files, so cache assertions depend on inputs rather than edit timing.
    runtime = service.runtime()
    service.runtime = lambda: runtime
    pipeline = create_pipeline(service)
    dispatcher.pipeline = pipeline
    analyzer = SyntheticTracksAnalyzer()
    pipeline.adapters.analyzer = analyzer
    identity = uuid4().hex
    artifact = f"videos/{identity}/original.mp4"
    service.artifacts.put_bytes(artifact, blank_video_bytes)
    video = VideoRecord(id=identity, filename="blank.mp4", artifact=artifact,
                        content_hash=sha256_file(service.artifacts.resolve(artifact)))
    service.repository.insert("video", video)
    value = System(service, pipeline, dispatcher, analyzer, video)
    yield value
    service.repository.engine.dispose()


@pytest.mark.parametrize("violation,reason", [
    ("missing", "TRACK_MAP_INVALID"), ("duplicate", "TRACK_MAP_INVALID"),
    ("unknown", "TRACK_MAP_INVALID"), ("unprovided", "EVIDENCE_ID_INVALID"),
])
def test_invalid_model_track_map_or_unsupplied_evidence_blocks_automatic_render(system, violation, reason):
    class InvalidModel:
        def decide(self, scenario, observation, evidence):
            result = FixtureDecider({"mode": "fixture"}).decide(scenario, observation, evidence)
            if violation == "missing":
                result.plan.decisions.pop()
            elif violation == "duplicate":
                result.plan.decisions.append(result.plan.decisions[0].model_copy(deep=True))
            elif violation == "unknown":
                result.plan.decisions[0].track_id = "unobserved-track"
            else:
                provided = {item.id for item in evidence.provided}
                candidate = next(item for item in evidence.candidates if item.id not in provided)
                result.plan.decisions[0].evidence_ids = [candidate.id]
            return result
    system.pipeline.adapters.decider = lambda config: InvalidModel()
    run = system.run(system.add_index())
    run = system.service.request_stage(run.id, "decide")
    assert run.status == "REVIEW_REQUIRED"
    assert any(value.startswith(reason) for value in run.review_reasons)
    before = run.model_dump()
    with pytest.raises(ValueError, match="모든 트랙"):
        system.service.request_stage(run.id, "render")
    assert system.service.repository.get("run", run.id, RunSnapshot).model_dump() == before


def test_review_required_needs_every_override_then_renders_actual_canonical_frames(system):
    run = system.run()
    run = system.service.request_stage(run.id, "decide")
    assert run.status == "REVIEW_REQUIRED" and run.evidence.provided == []
    with pytest.raises(ValueError, match="unknown track"):
        system.service.overrides(run.id, {"unknown": Treatment(action="MASK", strength=3)}, False)
    system.service.overrides(run.id, {"track1": Treatment(action="MASK", strength=3)}, False)
    with pytest.raises(ValueError, match="모든 트랙"):
        system.service.request_stage(run.id, "render")
    run = system.service.overrides(run.id, {
        "track1": Treatment(action="MASK", strength=3), "track2": Treatment(action="KEEP"),
    }, False)
    run = system.service.request_stage(run.id, "render")
    assert run.status == "COMPLETED", run.error
    assert run.manual_output and run.review_reasons and run.render.frame_count == 6
    capture = cv2.VideoCapture(str(system.service.artifacts.resolve(run.render.artifact)))
    success, frame = capture.read()
    capture.release()
    assert success and frame[22:26, 14:18].mean() < 5
    assert frame[22:26, 46:50].mean() > 200
    report = json.loads(system.service.artifacts.resolve(run.reports["json"]).read_text())
    assert report["manual_output"] and report["review_reasons"] == run.review_reasons
    assert report["effective_treatments"]["track1"] == {"action": "MASK", "strength": 3}
    assert report["stages"] == [stage.model_dump(mode="json") for stage in run.stages]


def test_actual_zero_faces_requires_explicit_confirmation(system):
    system.pipeline.adapters.analyzer = create_pipeline(system.service).adapters.analyzer
    run = system.run()
    assert run.status == "AWAITING_LABELS", run.error
    assert run.observation.tracks == [] and run.observation.frame_count == 6
    run = system.service.request_stage(run.id, "decide")
    assert any(item.startswith("NO_FACES_DETECTED") for item in run.review_reasons)
    with pytest.raises(ValueError, match="명시적으로"):
        system.service.request_stage(run.id, "render")
    system.service.overrides(run.id, {}, True)
    completed = system.service.request_stage(run.id, "render")
    assert completed.status == "COMPLETED", completed.error
    assert completed.no_faces_confirmed and completed.manual_output
    assert completed.effective_treatments == {} and completed.render.frame_count == 6


def test_same_analysis_fingerprint_fork_reuses_vision_changed_detector_reanalyzes(system):
    parent = system.run()
    assert system.analyzer.calls == 1
    child = system.service.fork(parent.id, scenario=ScenarioInput(context="새 이용 상황"))
    assert child.status == "AWAITING_LABELS" and child.parent_id == parent.id
    assert system.analyzer.calls == 1 and child.observation == parent.observation
    changed = system.service.fork(parent.id, profile="fixture-other")
    assert system.analyzer.calls == 2 and changed.observation.detector == "yunet"
    assert changed.observation.analysis_fingerprint != parent.observation.analysis_fingerprint
    assert system.service.repository.get("run", parent.id, RunSnapshot) == parent


def test_completed_runs_are_immutable_and_redelivery_does_not_reinvoke_stage(system):
    completed = system.completed()
    before = completed.model_dump()
    for stage in ("analyze", "decide", "render"):
        with pytest.raises(ValueError, match="immutable"):
            system.service.request_stage(completed.id, stage)
    with pytest.raises(ValueError, match="fork"):
        system.service.labels(completed.id, {"track1": "new label"})
    with pytest.raises(ValueError, match="review"):
        system.service.overrides(completed.id, {}, False)
    def no_render(*args):
        raise AssertionError("Redelivered completed stage must not render")
    system.pipeline.adapters.renderer.render = no_render
    last = completed.stages[-1]
    system.pipeline.run_stage(completed.id, last.stage, last.id)
    assert system.service.repository.get("run", completed.id, RunSnapshot).model_dump() == before


def test_superseded_running_attempt_cannot_replace_new_active_attempt(system):
    system.dispatcher.paused = True
    run = system.run()
    old = run.stages[-1].id
    def supersede():
        system.analyzer.on_analyze = None
        system.service.request_stage(run.id, "analyze", retry=True)
    system.analyzer.on_analyze = supersede
    system.pipeline.run_stage(run.id, "analyze", old)
    active = system.service.repository.get("run", run.id, RunSnapshot)
    assert active.status == "ANALYZING" and active.observation is None
    assert active.stages[0].status == "SUPERSEDED"
    assert active.stages[-1].id != old and active.stages[-1].status == "QUEUED"
    new = active.stages[-1].id
    # A stale queue message must also leave the new queued attempt unchanged.
    system.pipeline.run_stage(run.id, "analyze", old)
    assert system.service.repository.get("run", run.id, RunSnapshot) == active
    system.pipeline.run_stage(run.id, "analyze", new)
    ready = system.service.repository.get("run", run.id, RunSnapshot)
    assert ready.status == "AWAITING_LABELS" and system.analyzer.calls == 2
    assert ready.stages[0].status == "SUPERSEDED" and ready.stages[-1].status == "COMPLETED"


def test_evidence_is_saved_before_decider_initialization_or_call_fails(system):
    run = system.run(system.add_index())
    def unavailable(config):
        persisted = system.service.repository.get("run", run.id, RunSnapshot)
        assert persisted.evidence.provided and persisted.evidence.index_id == run.index_id
        raise RuntimeError("Model provider unavailable")
    system.pipeline.adapters.decider = unavailable
    failed = system.service.request_stage(run.id, "decide")
    assert failed.status == "FAILED" and "provider unavailable" in failed.error
    assert failed.evidence.provided and failed.decision is None
    report = json.loads(system.service.artifacts.resolve(failed.reports["json"]).read_text())
    assert report["evidence"] == failed.evidence.model_dump(mode="json")
    assert report["stages"][-1]["status"] == "FAILED"


def test_labels_and_failed_upload_queue_are_publicly_recoverable(system):
    run = system.run()
    with pytest.raises(ValueError, match="Unknown track"):
        system.service.labels(run.id, {"unknown": "description"})
    with pytest.raises(ValueError, match="500"):
        system.service.labels(run.id, {"track1": "x" * 501})
    assert system.service.labels(run.id, {"track1": "교육 참여자"}).scenario.track_labels == {
        "track1": "교육 참여자",
    }
    def unavailable(*args):
        raise ConnectionError("queue disconnected")
    system.dispatcher.send = unavailable
    document = system.add_document("다시 추출할 수 있는 저장 원문")
    assert document.status == "FAILED" and "queue disconnected" in document.error
    assert system.service.artifacts.resolve(f"documents/{document.id}/original.txt").is_file()


@pytest.mark.parametrize("stage", ["analyze", "decide"])
def test_snapshot_artifact_failure_preserves_failed_attempt_and_generated_data_in_database(system, stage):
    index = system.add_index()
    if stage == "analyze":
        system.dispatcher.paused = True
        run = system.run(index)
    else:
        run = system.run(index)
        system.dispatcher.paused = True
        run = system.service.request_stage(run.id, "decide")
    attempt = run.stages[-1].id
    original_write = system.service.artifacts.put_bytes
    def snapshot_unavailable(path, data):
        if path.endswith("/snapshot.json"):
            raise OSError("snapshot storage unavailable")
        return original_write(path, data)
    system.service.artifacts.put_bytes = snapshot_unavailable
    try:
        system.pipeline.run_stage(run.id, stage, attempt)
    except OSError:
        # The Celery task may propagate the I/O error; the durable domain record
        # must still describe failure while SQLite itself remains healthy.
        pass
    failed = system.service.repository.get("run", run.id, RunSnapshot)
    assert failed.status == "FAILED" and "snapshot storage unavailable" in failed.error
    assert failed.stages[-1].id == attempt and failed.stages[-1].status == "FAILED"
    assert "snapshot storage unavailable" in failed.stages[-1].error
    assert failed.stages[-1].snapshot_artifact is None
    assert failed.observation and len(failed.observation.tracks) == 2
    assert failed.observation.original_hash == system.video.content_hash
    report = json.loads(system.service.artifacts.resolve(failed.reports["json"]).read_text())
    assert report["status"] == "FAILED" and report["error"] == failed.error
    assert report["stages"] == [item.model_dump(mode="json") for item in failed.stages]
    if stage == "decide":
        assert failed.evidence and failed.evidence.provided and failed.evidence.index_id == index.id
        assert failed.decision and failed.decision.plan and failed.decision.raw["fixture"]
        assert {entry.track_id for entry in failed.decision.plan.decisions} == {"track1", "track2"}
        assert report["evidence"] == failed.evidence.model_dump(mode="json")


@pytest.mark.parametrize("indexed", [True, False])
def test_strength_revision_renders_only_and_preserves_completed_parent(system, textured_video_bytes, indexed):
    system.add_video(textured_video_bytes)
    parent = system.run(system.add_index() if indexed else None)
    parent = system.service.request_stage(parent.id, "decide")
    system.service.overrides(parent.id, {
        "track1": Treatment(action="MOSAIC", strength=1), "track2": Treatment(action="KEEP"),
    }, False)
    parent = system.service.request_stage(parent.id, "render")
    assert parent.status == "COMPLETED", parent.error
    parent_data = parent.model_dump(mode="json")
    parent_output = system.service.artifacts.resolve(parent.render.artifact).read_bytes()
    calls_before = len(system.dispatcher.calls)
    def never(*args, **kwargs):
        raise AssertionError("A treatment revision must not analyze, retrieve, rerank or decide")
    system.pipeline.adapters.analyzer.analyze = never
    system.pipeline.adapters.analyzer.fingerprint = never
    system.pipeline.adapters.retriever = never
    system.pipeline.adapters.decider = never
    system.pipeline.adapters.reranker = never
    render = system.pipeline.adapters.renderer.render
    renders = []
    def render_only(*args):
        renders.append(args)
        return render(*args)
    system.pipeline.adapters.renderer.render = render_only
    system.service.runtime = lambda: {"revision_fixture_runtime": True}
    revised = system.service.revise(parent.id)
    assert revised.id != parent.id and revised.parent_id == parent.id
    assert revised.status == ("REVIEW_READY" if indexed else "REVIEW_REQUIRED")
    assert revised.reused_stages == {"analyze": parent.id, "decide": parent.id}
    assert revised.observation == parent.observation and revised.evidence == parent.evidence
    assert revised.decision == parent.decision and revised.scenario == parent.scenario
    assert revised.overrides == parent.overrides and revised.review_reasons == parent.review_reasons
    assert {key: value for key, value in revised.config.items() if key != "runtime"} == {
        key: value for key, value in parent.config.items() if key != "runtime"
    }
    assert revised.config["runtime"] == {"revision_fixture_runtime": True}
    assert revised.stages == [] and revised.render is None and revised.reports == {}
    assert revised.effective_treatments == {} and len(system.dispatcher.calls) == calls_before
    treatments = {key: value.model_copy(deep=True) for key, value in revised.overrides.items()}
    treatments["track1"] = Treatment(action="MOSAIC", strength=3)
    system.service.overrides(revised.id, treatments, False)
    output = system.service.request_stage(revised.id, "render")
    assert output.status == "COMPLETED", output.error
    assert len(renders) == 1 and [stage.stage for stage in output.stages] == ["render"]
    assert len(system.dispatcher.calls) == calls_before + 1
    assert output.effective_treatments["track1"] == Treatment(action="MOSAIC", strength=3)
    assert output.render.artifact != parent.render.artifact and output.render.content_hash != parent.render.content_hash
    first = cv2.VideoCapture(str(system.service.artifacts.resolve(parent.render.artifact)))
    second = cv2.VideoCapture(str(system.service.artifacts.resolve(output.render.artifact)))
    try:
        ok1, before = first.read()
        ok2, after = second.read()
    finally:
        first.release()
        second.release()
    assert ok1 and ok2 and cv2.absdiff(before, after).max() > 5
    assert system.service.repository.get("run", parent.id, RunSnapshot).model_dump(mode="json") == parent_data
    assert system.service.artifacts.resolve(parent.render.artifact).read_bytes() == parent_output
    report = json.loads(system.service.artifacts.resolve(output.reports["json"]).read_text())
    assert report["reused_stages"] == {"analyze": parent.id, "decide": parent.id}
    assert report["evidence"] == parent_data["evidence"] and report["decision"] == parent_data["decision"]


def test_revision_rejects_unfinished_source_without_creating_or_queueing_a_run(system):
    source = system.run()
    before = system.service.repository.list("run", RunSnapshot)
    calls = list(system.dispatcher.calls)
    with pytest.raises(ValueError, match="completed"):
        system.service.revise(source.id)
    assert system.service.repository.list("run", RunSnapshot) == before
    assert system.dispatcher.calls == calls
