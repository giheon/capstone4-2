"""PrivFrame 2.0 web API (M4): the contract in frontend/src/api/contract.ts, served by the real case graph.

    uv run uvicorn privframe.service.app:app     # http://127.0.0.1:8000, this machine only
    uv run fastapi dev                           # the same with auto-reload (a reload ends running cases;
                                                 # they come back as paused and can be continued)
    cd frontend && PRIVFRAME_API=proxy npm run dev

Cases run in worker threads of this process (service/runner.py). Path operations are plain `def` functions
because they read files and touch the run manager; only the event stream is async.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ..core import understanding as views
from ..core.domain import Decision
from ..core.geometry import STRENGTH, Region, applied_ranges, build_regions, render_shape
from ..core.render import MOSAIC_BLOCKS
from ..core.settings import ROOT, CoreSettings
from ..core.support import backend_issues, missing_keys
from ..core.video import VideoAsset, encode_jpeg, index_at, load_times, read_frame
from .errors import ServiceError
from . import evaluation, topology
from .datasets import DatasetStore
from .experiments import LIMITS, ExperimentManager
from .openrouter import lookup_openrouter
from .pipeline_store import PipelineStore
from .runner import TERMINAL, Case, RunError, RunManager

MAX_UPLOAD_BYTES = 4 * 1024**3


class Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class IdentityRequest(Body):
    value: Literal["same", "different", "unknown"]


class Answer(Body):
    question_id: str
    value: str


class AnswersRequest(Body):
    answers: list[Answer] = Field(default_factory=list)


class FactUpdateRequest(Body):
    key: str
    value: str


class DecisionEdit(Body):
    """What a reviewer may change about one decision; anything left out stays as planned."""

    id: str
    method: Literal["keep", "mosaic", "remove"] | None = None
    shape: Literal["ellipse", "box", "outline"] | None = None
    ranges: list[tuple[float, float]] | None = None


class ReviewRequest(Body):
    decisions: list[DecisionEdit] = Field(default_factory=list)


class GeometryRequest(Body):
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class Resolution(Body):
    finding_id: str
    action: Literal["apply", "reject"]
    reason: str | None = None


class AuditRequest(Body):
    resolutions: list[Resolution] = Field(default_factory=list)
    back_to_review: bool = False


class ProceedRequest(Body):
    cost_cap_usd: float | None = Field(default=None, gt=0, le=20)


class StartRunRequest(Body):
    profile_id: str | None = None


class LaunchRequest(Body):
    """Runs to add to a section experiment: the product of the chosen candidates and values, on the chosen videos."""

    matrix: dict[str, list[str]] = Field(default_factory=dict)  # part → candidates; a part that is not named keeps the base profile's
    param_matrix: dict[str, list[float]] = Field(default_factory=dict)  # parameter → values
    datasets: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)  # "<dataset>/<item>" to run only some videos
    source: str = ""  # the run to continue, when the server's own choice is not wanted (one run only)

    def launch(self) -> dict[str, Any]:
        return {"matrix": self.matrix, "paramMatrix": self.param_matrix, "datasets": self.datasets, "items": self.items, "source": self.source}


class StageExperimentRequest(LaunchRequest):
    """A new section experiment: a range of pipeline sections, the base profile, and its first runs."""

    title: str = ""
    from_: str = Field(alias="from")
    to: str = ""
    base_profile: str = ""
    cost_cap_usd: float = Field(default=LIMITS["costCapUsdDefault"], gt=0, le=LIMITS["costCapUsdMax"])

    def shell(self) -> dict[str, Any]:
        return {"title": self.title, "from": self.from_, "to": self.to or self.from_, "baseProfile": self.base_profile, "costCapUsd": self.cost_cap_usd}


class LaunchPreviewRequest(LaunchRequest):
    """What a launch would run: for an experiment that exists (`experiment`), or for a range and a base profile."""

    experiment: str = ""
    from_: str = Field(default="", alias="from")
    to: str = ""
    base_profile: str = ""


class StageExperimentEdit(Body):
    title: str | None = None
    cost_cap_usd: float | None = Field(default=None, gt=0, le=LIMITS["costCapUsdMax"])


class ScoresRequest(Body):
    """Scores computed outside for one run; they replace what the same evaluator stored before."""

    evaluator: str
    scores: list[dict[str, Any]] = Field(default_factory=list)


class VideoSetRequest(Body):
    name: str
    description: str = ""
    sendable: bool = False  # may frames of these videos be sent to cloud models


class VideoSetCaseRequest(Body):
    case_id: str
    title: str = ""


class VideoSetEdit(Body):
    name: str | None = None
    description: str | None = None
    sendable: bool | None = None


class VideoEdit(Body):
    title: str | None = None
    memo: str | None = None
    expected: dict[str, Any] | None = None  # what is in the video; an empty object clears it


class Ok(BaseModel):
    ok: bool = True


# ---------- dependencies ----------


def get_manager(request: Request) -> RunManager:
    return request.app.state.manager


def get_store(request: Request) -> PipelineStore:
    return request.app.state.store


def get_experiments(request: Request) -> ExperimentManager:
    return request.app.state.experiments


def get_datasets(request: Request) -> DatasetStore:
    return request.app.state.datasets


ManagerDep = Annotated[RunManager, Depends(get_manager)]
StoreDep = Annotated[PipelineStore, Depends(get_store)]
ExperimentsDep = Annotated[ExperimentManager, Depends(get_experiments)]
DatasetsDep = Annotated[DatasetStore, Depends(get_datasets)]


def get_case(case_id: str, manager: ManagerDep) -> Case:
    return manager.get(case_id)


CaseDep = Annotated[Case, Depends(get_case)]


def sample_video(settings: CoreSettings) -> Path | None:
    """The example video for demo runs: PRIVFRAME_SAMPLE_VIDEO, or data/samples/sample.mp4 (make it with
    `uv run privframe-case make-sample`)."""
    configured = os.environ.get("PRIVFRAME_SAMPLE_VIDEO")
    path = Path(configured) if configured else settings.data_dir / "samples" / "sample.mp4"
    return path if path.exists() else None


def sample_notes(path: Path) -> dict[str, Any]:
    """Optional `<sample>.json` next to the sample video: {"title", "memo", "answers": {fact_key: value}}."""
    notes = path.with_suffix(".json")
    return json.loads(notes.read_text(encoding="utf-8")) if notes.exists() else {}


# ---------- cases ----------

cases = APIRouter(prefix="/api/cases", tags=["cases"])


@cases.get("")
def list_cases(manager: ManagerDep) -> list[dict[str, Any]]:
    # experiment runs the user answers are opened from the Lab, not listed among the uploader's own work
    return [c.summary() for c in manager.list() if not c.meta.get("autopilot") and not c.meta.get("experiment")]


@cases.post("", status_code=201)
def create_case(
    manager: ManagerDep,
    file: Annotated[UploadFile | None, File()] = None,
    memo: Annotated[str, Form(max_length=4000)] = "",
    mode: Annotated[Literal["anonymized_upload", "local_only"], Form()] = "anonymized_upload",
    sample: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    if sample:
        path = sample_video(manager.settings)
        if path is None:
            raise RunError(409, "예시 영상이 준비되어 있지 않습니다. `uv run privframe-case make-sample`로 만들 수 있습니다.")
        notes = sample_notes(path)
        return manager.create(path, path.name, memo=memo or notes.get("memo", ""), mode=mode, title=notes.get("title") or "예시 영상", sample=True).detail
    if file is None or not file.filename:
        raise RunError(400, "영상 파일이 필요합니다.")
    suffix = Path(file.filename).suffix.lower() or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=manager.cases_dir) as handle:
        written = 0
        while chunk := file.file.read(1 << 20):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                handle.close()
                Path(handle.name).unlink(missing_ok=True)
                raise RunError(413, "영상이 너무 큽니다 (최대 4 GB).")
            handle.write(chunk)
    try:
        return manager.create(Path(handle.name), Path(file.filename).name, memo=memo, mode=mode, move=True).detail
    finally:
        Path(handle.name).unlink(missing_ok=True)


@cases.get("/{case_id}")
def get_case_detail(case: CaseDep) -> dict[str, Any]:
    return case.detail


@cases.get("/{case_id}/report.json")
def report_json(case: CaseDep) -> JSONResponse:
    detail = case.detail
    if not detail.get("report"):
        raise RunError(409, "보고서가 아직 없습니다.")
    body = {
        "case": {"id": case.id, "title": detail["title"], "video": detail["video"]},
        "profile": {"id": case.meta["profileId"], "name": case.meta["profileName"], "configHash": case.meta["configHash"]},
        **{key: detail[key] for key in ("facts", "questions", "analyses", "requirements", "plan", "findings", "report")},
    }
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="{case.id}-report.json"'})


@cases.get("/{case_id}/report.html", response_class=HTMLResponse)
def report_html(case: CaseDep) -> str:
    path = case.dir / "report.html"
    if not path.exists():
        raise RunError(409, "보고서가 아직 없습니다.")
    return path.read_text(encoding="utf-8")


@cases.post("/{case_id}/identity", status_code=202)
def answer_identity(case: CaseDep, body: IdentityRequest, manager: ManagerDep) -> Ok:
    label = {"same": "같은 사람", "different": "다른 사람", "unknown": "모르겠음 → 같은 사람으로 처리"}[body.value]
    manager.resume(case, "identity", body.value, label)
    return Ok()


@cases.post("/{case_id}/answers", status_code=202)
def answer_questions(case: CaseDep, body: AnswersRequest, manager: ManagerDep) -> Ok:
    values = {a.question_id: a.value for a in body.answers}
    summary = " · ".join(f"{k}={v}" for k, v in values.items()) or "답변 없음"
    if manager.waiting_for(case, "questions"):
        manager.resume(case, "questions", values, summary)
    elif manager.waiting_for(case, "review"):
        manager.resume(case, "review", {"action": "answers", "answers": values}, f"늦은 답변 {summary}")
    else:
        raise RunError(409, "지금은 답변을 받을 수 없습니다.")
    return Ok()


@cases.post("/{case_id}/facts", status_code=202)
def update_fact(case: CaseDep, body: FactUpdateRequest, manager: ManagerDep) -> Ok:
    if not manager.waiting_for(case, "review"):
        raise RunError(409, "사실은 검토 단계에서만 고칠 수 있습니다.")
    manager.resume(case, "review", {"action": "facts", "facts": [{"key": body.key, "value": body.value}]}, f"사실 수정 {body.key}={body.value}")
    return Ok()


@cases.post("/{case_id}/review", status_code=202)
def submit_review(case: CaseDep, body: ReviewRequest, manager: ManagerDep) -> Ok:
    # a malformed edit is refused here (422): once inside the run it would fail the whole case
    edits = [edit.model_dump(by_alias=True, exclude_none=True) for edit in body.decisions]
    manager.resume(case, "review", {"action": "approve", "decisions": edits}, f"검토 제출 (결정 {len(edits)}건)")
    return Ok()


@cases.post("/{case_id}/audit", status_code=202)
def submit_audit(case: CaseDep, body: AuditRequest, manager: ManagerDep) -> Ok:
    if body.back_to_review and manager.waiting_for(case, "audit") and not case.detail.get("canReturnToReview"):
        raise RunError(409, "계획으로 돌아가 다시 고칠 수 있는 횟수를 모두 썼습니다. 권고마다 적용하거나 거부해 주세요.")
    value = {"resolutions": [r.model_dump(by_alias=True) for r in body.resolutions], "backToReview": body.back_to_review}
    applied = sum(1 for r in body.resolutions if r.action == "apply")
    summary = "계획으로 돌아가 직접 수정" if body.back_to_review else f"적용 {applied} · 위험 수용 {len(body.resolutions) - applied}"
    manager.resume(case, "audit", value, summary)
    return Ok()


@cases.post("/{case_id}/pause", status_code=202)
def pause_case(case: CaseDep, manager: ManagerDep) -> Ok:
    manager.pause(case)
    return Ok()


@cases.post("/{case_id}/resume", status_code=202)
def resume_case(case: CaseDep, manager: ManagerDep, body: ProceedRequest | None = None) -> Ok:
    manager.proceed(case, body.cost_cap_usd if body else None)
    return Ok()


# ---------- media ----------


@cases.get("/{case_id}/media/proxy")
def media_proxy(case: CaseDep) -> FileResponse:
    path = case.dir / "proxy.mp4"
    if not path.exists():
        raise RunError(409, "분석용 영상이 아직 준비되지 않았습니다.")
    return FileResponse(path, media_type="video/mp4")


@cases.get("/{case_id}/media/output")
def media_output(case: CaseDep, download: Annotated[bool, Query()] = False) -> FileResponse:
    files = sorted((case.dir / "output").glob("*")) if (case.dir / "output").exists() else []
    if not files:
        raise RunError(409, "출력 영상이 아직 없습니다.")
    return FileResponse(files[0], filename=files[0].name if download else None)


@cases.get("/{case_id}/frame")
def frame(case: CaseDep, manager: ManagerDep, t: Annotated[float, Query(ge=0)] = 0.0, w: Annotated[int, Query(ge=64, le=1920)] = 640) -> Response:
    asset = _video_asset(case, manager)
    image = read_frame(asset, index_at(load_times(asset), t), "proxy")
    return Response(encode_jpeg(image, quality=80, max_side=w), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def _video_asset(case: Case, manager: RunManager) -> VideoAsset:
    if not (case.dir / "proxy.mp4").exists():
        raise RunError(409, "분석용 영상이 아직 준비되지 않았습니다.")
    video = manager.state(case).get("video") or {}
    if "proxy" not in video:
        raise RunError(409, "분석용 영상이 아직 준비되지 않았습니다.")
    return VideoAsset.model_validate(video)


def _runs(frames: dict[int, list[Region]], width: int, height: int) -> list[list[Any]]:
    """Per-frame regions as runs of consecutive frames: [first frame, [[x, y, w, h] per frame]], the box as a
    fraction of the frame. A frame with two regions gives two runs."""
    runs: list[list[Any]] = []
    for layer in range(max((len(items) for items in frames.values()), default=0)):
        start, previous, boxes = 0, None, []
        for index in sorted(f for f, items in frames.items() if len(items) > layer):
            if previous is None or index != previous + 1:
                if boxes:
                    runs.append([start, boxes])
                start, boxes = index, []
            region = frames[index][layer]
            boxes.append([round(region.x / width, 4), round(region.y / height, 4), round(region.w / width, 4), round(region.h / height, 4)])
            previous = index
        if boxes:
            runs.append([start, boxes])
    return runs


def _geometry(case: Case, manager: RunManager, decisions: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Where each decision's mosaic goes on every frame, computed by the renderer's own rules (core/geometry.py),
    so the review preview and the output cannot disagree. A kept target gets the area a mosaic would cover."""
    path = case.dir / "understanding.json"
    if not path.exists():
        raise RunError(409, "영상 분석이 아직 끝나지 않았습니다.")
    asset = _video_asset(case, manager)
    understanding = views.load(path)
    if not understanding.visible and understanding.tracks:
        views.with_coverage(understanding, asset)
    timeline = views.timeline(asset, understanding)
    width, height = asset.meta.display_width, asset.meta.display_height
    plan = decisions if decisions is not None else ((case.detail.get("plan") or {}).get("decisions") or [])
    out: dict[str, Any] = {}
    for item in plan:
        if item.get("scope") == "metadata":
            continue
        try:
            decision = Decision.model_validate({"rationale": "", "targetLabel": "", **item, "method": "mosaic"})
        except ValueError as error:
            raise RunError(422, f"결정 {item.get('id', '?')}의 형식이 올바르지 않습니다.") from error
        regions = build_regions(understanding, [decision], timeline, width=width, height=height)
        out[decision.id] = {"shape": render_shape(decision), "strength": STRENGTH.get(decision.scope, 1.0), "ranges": applied_ranges(decision, understanding, timeline), "runs": _runs(regions, width, height)}
    return {"width": width, "height": height, "durationSec": timeline.duration, "frames": [round(t, 4) for t in timeline.times], "decisions": out, "mosaicBlocks": MOSAIC_BLOCKS}


@cases.get("/{case_id}/geometry")
def geometry(case: CaseDep, manager: ManagerDep) -> dict[str, Any]:
    """The preview geometry of the case's current plan."""
    return _geometry(case, manager, None)


@cases.post("/{case_id}/geometry")
def geometry_of(case: CaseDep, manager: ManagerDep, body: GeometryRequest) -> dict[str, Any]:
    """The preview geometry of an edited plan (the review screen sends its unsaved decisions)."""
    return _geometry(case, manager, body.decisions)


# ---------- run events ----------

runs = APIRouter(prefix="/api/runs", tags=["runs"])


@runs.get("/{case_id}/events", response_class=EventSourceResponse)
async def run_events(case: CaseDep, manager: ManagerDep, last_event_id: Annotated[str | None, Header()] = None, since: Annotated[int, Query(ge=0)] = 0) -> AsyncIterable[ServerSentEvent]:
    """Normalized run events as Server-Sent Events. `Last-Event-ID` (or `since`) resumes after a reconnect."""
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else since
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    backlog, unsubscribe = manager.subscribe(case, start, lambda event: loop.call_soon_threadsafe(queue.put_nowait, event))
    try:
        yield ServerSentEvent(comment="connected", retry=1500)
        for event in backlog:
            yield ServerSentEvent(data=event, id=str(event["seq"]))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ServerSentEvent(comment="ping")
                continue
            yield ServerSentEvent(data=event, id=str(event["seq"]))
    finally:
        unsubscribe()


# ---------- lab ----------

lab = APIRouter(prefix="/api/lab", tags=["lab"])


@lab.get("/graph")
def lab_graph() -> dict[str, Any]:
    return topology.graph_spec()


@lab.get("/runs")
def lab_runs(manager: ManagerDep) -> list[dict[str, Any]]:
    return [c.run_summary() for c in manager.list()]


@lab.post("/runs", status_code=201)
def start_demo_run(manager: ManagerDep, store: StoreDep, body: StartRunRequest | None = None) -> dict[str, Any]:
    """Run the example video unattended with a profile (questions are answered from the sample's notes,
    otherwise "모름"; the plan is approved as proposed)."""
    path = sample_video(manager.settings)
    if path is None:
        raise RunError(409, "예시 영상이 준비되어 있지 않습니다. `uv run privframe-case make-sample`로 만들 수 있습니다.")
    profile_id = (body.profile_id if body else None) or manager.service_profile("anonymized_upload")
    errors = [i for i in all_issues(manager, store, store.get_profile(profile_id)["spec"]) if i["level"] == "error"]
    if errors:
        raise RunError(400, f"프로필에 오류가 있어 실행할 수 없습니다: {errors[0]['text']}")
    notes = sample_notes(path)
    profile = manager.resolve_profile(profile_id)
    case = manager.create(path, path.name, memo=notes.get("memo", ""), profile_id=profile_id, autopilot=True, autopilot_answers=notes.get("answers"), title=f"{notes.get('title') or '예시 영상'} (데모 · {profile.name})", sample=True, kind="demo")
    return case.run_summary()


@lab.get("/catalog")
def lab_catalog(store: StoreDep) -> dict[str, Any]:
    return store.catalog()


@lab.get("/catalog/lookup")
def catalog_lookup(ref: Annotated[str, Query(min_length=1, max_length=200)]) -> dict[str, Any]:
    return lookup_openrouter(ref)


@lab.post("/catalog/candidates", status_code=201)
def add_candidate(body: dict[str, Any], store: StoreDep) -> dict[str, Any]:
    return store.add_candidate(body)


@lab.delete("/catalog/candidates/{slot}/{candidate}")
def remove_candidate(slot: str, candidate: str, store: StoreDep) -> Ok:
    store.remove_candidate(slot, candidate)
    return Ok()


def estimate_cost(manager: RunManager, config_hash: str, parts: dict[str, Any]) -> float:
    """Expected cost of a one-minute case: the mean of finished runs with this exact configuration when there
    are any, otherwise a token model of the pipeline's calls at catalog prices."""
    done = [c.detail["costUsd"] for c in manager.list() if c.meta["configHash"] == config_hash and c.status == "COMPLETED"]
    if done:
        return round(sum(done) / len(done), 4)

    def price(slot: str, tokens_in: float, tokens_out: float, cached: float = 0.0) -> float:
        rate = (parts.get(slot) or {}).get("price") or {"in": 0, "out": 0}
        return ((tokens_in - cached) * rate["in"] + cached * rate["in"] * 0.1 + tokens_out * rate["out"]) / 1_000_000

    total = price("llm.analyst", 120_000, 5_500, 95_000) + price("llm.planner", 6_000, 1_600) + price("vlm.scene", 16_000, 2_000) + price("vlm.person", 5_000, 700) + price("vlm.inspect", 5_200, 200)
    return round(total, 4)


def all_issues(manager: RunManager, store: PipelineStore, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The catalog's own checks plus what this backend cannot run yet."""
    profile = store.resolved(spec)  # the store's parsed catalog, and its reading of a draft's values
    return [*store.issues(spec), *backend_issues(profile), *([] if manager.fixture is not None else missing_keys(profile, manager.settings.present()))]


def checked(manager: RunManager, store: PipelineStore, spec: dict[str, Any]) -> dict[str, Any]:
    """What the Lab shows next to a profile or a draft: its hash, what is wrong with it, its parts and what it costs."""
    resolved = store.resolve(spec)
    counts = store.api_local_counts(spec)
    return {
        "configHash": resolved["configHash"],
        "issues": all_issues(manager, store, spec),
        "components": resolved["components"],
        "estimate": {"costUsd": estimate_cost(manager, resolved["configHash"], resolved["part"]), "framesLeaveDevice": store.frames_leave_device(spec), "apiParts": counts["api"], "localParts": counts["local"]},
    }


def profile_summary(manager: RunManager, store: PipelineStore, stored: dict[str, Any]) -> dict[str, Any]:
    spec = stored["spec"]
    service = {**store.service(), "default": manager.service_profile("anonymized_upload")}  # what uploads actually run with
    return {
        **spec,
        **checked(manager, store, spec),
        "path": stored["path"],
        "source": stored["source"],
        "updatedAt": stored["updatedAt"],
        "service": [target for target, profile_id in service.items() if profile_id == spec["id"]],
        "gate": "not_run",  # regression experiments are measured in M5
    }


@lab.get("/profiles")
def list_profiles(manager: ManagerDep, store: StoreDep) -> list[dict[str, Any]]:
    return [profile_summary(manager, store, stored) for stored in store.list_profiles()]


@lab.post("/profiles", status_code=201)
def create_profile(body: dict[str, Any], manager: ManagerDep, store: StoreDep) -> dict[str, Any]:
    return profile_summary(manager, store, store.save_profile(body, "create"))


@lab.post("/profiles/check")
def check_profile(body: dict[str, Any], manager: ManagerDep, store: StoreDep) -> dict[str, Any]:
    """Validate a draft from the editor; parts it does not mention take the catalog default."""
    defaults = {slot["id"]: slot["default"] for slot in store.slots()}
    spec = {"name": "", "description": "", **body, "id": body.get("id") or "draft", "selections": {**defaults, **(body.get("selections") or {})}, "params": body.get("params") or {}}
    return checked(manager, store, spec)


@lab.get("/profiles/{profile_id}")
def get_profile(profile_id: str, manager: ManagerDep, store: StoreDep) -> dict[str, Any]:
    return profile_summary(manager, store, store.get_profile(profile_id))


@lab.put("/profiles/{profile_id}")
def update_profile(profile_id: str, body: dict[str, Any], manager: ManagerDep, store: StoreDep) -> dict[str, Any]:
    return profile_summary(manager, store, store.save_profile({**body, "id": profile_id}, "update"))


@lab.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, store: StoreDep) -> Ok:
    store.delete_profile(profile_id)
    return Ok()


@lab.post("/profiles/{profile_id}/promote")
def promote_profile(profile_id: str, store: StoreDep) -> dict[str, Any]:
    """Promotion needs a regression experiment that passed with the current configuration; experiments on
    the real pipeline are M5, so nothing can be promoted from the Lab yet."""
    store.get_profile(profile_id)
    raise RunError(409, "현재 설정으로 회귀 게이트를 통과한 실험이 있어야 승격할 수 있습니다. 실제 파이프라인의 실험은 M5에서 연결됩니다.")


@lab.get("/experiments")
def list_experiments() -> list[dict[str, Any]]:
    """Whole-pipeline experiments scored against labelled scenarios (M5). Section experiments are below."""
    return []


@lab.post("/experiments")
def start_experiment() -> None:
    raise RunError(501, "정답이 있는 시나리오로 전체 파이프라인을 채점하는 실험은 M5에서 연결됩니다. 지금은 구간 실험으로 부품 조합을 비교할 수 있습니다.")


@lab.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> None:
    raise RunError(404, "실험을 찾을 수 없습니다.")


# ---------- section experiments ----------


@lab.get("/sections")
def lab_sections(experiments: ExperimentsDep) -> list[dict[str, Any]]:
    """The sections the pipeline can be run in, with the parts and parameters each one reads."""
    return experiments.sections()


@lab.get("/evaluators")
def lab_evaluators() -> list[dict[str, Any]]:
    """The evaluators that score experiment runs when they complete (service/evaluation.py)."""
    return evaluation.describe()


@lab.get("/stage-experiments/limits")
def stage_experiment_limits() -> dict[str, Any]:
    """How much one launch may add, and what all runs of an experiment may cost together."""
    return LIMITS


@lab.get("/stage-experiments")
def list_stage_experiments(experiments: ExperimentsDep) -> list[dict[str, Any]]:
    return experiments.list()


@lab.post("/stage-experiments/preview")
def preview_stage_launch(body: LaunchPreviewRequest, experiments: ExperimentsDep) -> dict[str, Any]:
    """What a launch would run and where each run would start, without starting anything."""
    return experiments.preview({**body.launch(), "experiment": body.experiment, "from": body.from_, "to": body.to or body.from_, "baseProfile": body.base_profile})


@lab.post("/stage-experiments", status_code=201)
def start_stage_experiment(body: StageExperimentRequest, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.start({**body.launch(), **body.shell()})


@lab.get("/stage-experiments/{experiment_id}")
def get_stage_experiment(experiment_id: str, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.get(experiment_id)


@lab.patch("/stage-experiments/{experiment_id}")
def edit_stage_experiment(experiment_id: str, body: StageExperimentEdit, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.edit(experiment_id, title=body.title, cost_cap_usd=body.cost_cap_usd)


@lab.post("/stage-experiments/{experiment_id}/runs", status_code=201)
def add_stage_runs(experiment_id: str, body: LaunchRequest, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.launch(experiment_id, body.launch())


@lab.delete("/stage-experiments/{experiment_id}/runs/{run_id}")
def delete_stage_run(experiment_id: str, run_id: str, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.delete_run(experiment_id, run_id)


@lab.put("/stage-experiments/{experiment_id}/runs/{run_id}/scores")
def set_stage_run_scores(experiment_id: str, run_id: str, body: ScoresRequest, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.set_scores(experiment_id, run_id, body.evaluator, body.scores)


@lab.post("/stage-experiments/{experiment_id}/stop")
def stop_stage_experiment(experiment_id: str, experiments: ExperimentsDep) -> dict[str, Any]:
    return experiments.stop(experiment_id)


@lab.delete("/stage-experiments/{experiment_id}")
def delete_stage_experiment(experiment_id: str, experiments: ExperimentsDep) -> Ok:
    experiments.delete(experiment_id)
    return Ok()


@lab.get("/artifacts")
def lab_artifacts(manager: ManagerDep) -> dict[str, Any]:
    """What the step store holds: results that later runs with the same inputs take instead of computing.
    Each entry names the graph node that produces it."""
    labels = topology.node_labels()
    out = {}
    for step, stats in manager.artifacts.stats().items():
        node = "ingest" if step == "memo_facts" else f"perceive/{step}"
        out[step] = {**stats, "node": node, "label": "메모에서 사실 추출" if step == "memo_facts" else labels.get(node, step)}
    return {"steps": out}


# ---------- video datasets ----------


@lab.get("/video-sets")
def list_video_sets(datasets: DatasetsDep) -> list[dict[str, Any]]:
    return [datasets.public(dataset) for dataset in datasets.list()]


@lab.post("/video-sets", status_code=201)
def create_video_set(body: VideoSetRequest, datasets: DatasetsDep) -> dict[str, Any]:
    return datasets.public(datasets.create(body.name, body.description, body.sendable))


@lab.get("/video-sets/{dataset_id}")
def get_video_set(dataset_id: str, datasets: DatasetsDep) -> dict[str, Any]:
    return datasets.public(datasets.get(dataset_id))


@lab.patch("/video-sets/{dataset_id}")
def edit_video_set(dataset_id: str, body: VideoSetEdit, datasets: DatasetsDep) -> dict[str, Any]:
    return datasets.public(datasets.update(dataset_id, name=body.name, description=body.description, sendable=body.sendable))


@lab.delete("/video-sets/{dataset_id}")
def delete_video_set(dataset_id: str, datasets: DatasetsDep) -> Ok:
    datasets.delete(dataset_id)
    return Ok()


@lab.patch("/video-sets/{dataset_id}/items/{item_id}")
def edit_video(dataset_id: str, item_id: str, body: VideoEdit, datasets: DatasetsDep) -> dict[str, Any]:
    item = datasets.update_item(dataset_id, item_id, title=body.title, memo=body.memo, expected=body.expected)
    return DatasetStore.public_item(item)


@lab.post("/video-sets/{dataset_id}/items", status_code=201)
def add_video(
    dataset_id: str,
    datasets: DatasetsDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(max_length=200)] = "",
    memo: Annotated[str, Form(max_length=4000)] = "",
    expected: Annotated[str, Form(max_length=8000)] = "",
) -> dict[str, Any]:
    """Add an uploaded video. `expected` (what is in the video) is a JSON object."""
    try:
        truth = json.loads(expected) if expected.strip() else None
    except json.JSONDecodeError as error:
        raise RunError(422, "expected는 JSON 객체여야 합니다.") from error
    if truth is not None and not isinstance(truth, dict):
        raise RunError(422, "expected는 JSON 객체여야 합니다.")
    datasets.get(dataset_id)
    name = Path(file.filename or "upload.mp4").name
    with tempfile.TemporaryDirectory(dir=datasets.root.parent) as folder:
        target = Path(folder) / name
        size = 0
        with target.open("wb") as handle:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise RunError(413, "파일이 너무 큽니다 (최대 4 GB).")
                handle.write(chunk)
        item = datasets.add_item(dataset_id, target, name, title=title, memo=memo, expected=truth)
    return DatasetStore.public_item(item)


@lab.post("/video-sets/{dataset_id}/items/from-case", status_code=201)
def add_video_from_case(dataset_id: str, body: VideoSetCaseRequest, datasets: DatasetsDep, manager: ManagerDep) -> dict[str, Any]:
    """Add the video of an existing case, with its memo. Runs of that case can then be continued by experiments
    on this video (the video is the same file)."""
    case = manager.get(body.case_id)
    item = datasets.add_item(dataset_id, Path(case.meta["original"]), case.meta["filename"], title=body.title or case.detail["title"], memo=case.meta.get("memo", ""), origin=case.id)
    return DatasetStore.public_item(item)


@lab.delete("/video-sets/{dataset_id}/items/{item_id}")
def remove_video(dataset_id: str, item_id: str, datasets: DatasetsDep) -> Ok:
    datasets.remove_item(dataset_id, item_id)
    return Ok()


@lab.get("/datasets")
def lab_datasets() -> dict[str, Any]:
    return topology.datasets()


# ---------- app ----------

meta = APIRouter(prefix="/api", tags=["meta"])


@meta.get("/config")
def config(manager: ManagerDep) -> dict[str, Any]:
    """What kind of backend this is, so the web app can tell a real run from the M2 mock."""
    keys = manager.settings.present()
    return {"backend": "real", "serviceProfile": manager.service_profile("anonymized_upload"), "sampleAvailable": sample_video(manager.settings) is not None, "keys": {"openrouter": keys["OPENROUTER_API_KEY"], "anthropic": keys["ANTHROPIC_API_KEY"], "langsmith": keys["LANGSMITH_API_KEY"]}, "running": sum(1 for c in manager.list() if c.status not in TERMINAL and c.running)}


def fixture_manager(settings: CoreSettings) -> RunManager:
    """PRIVFRAME_FIXTURE=1: every profile runs on the scripted stand-ins (no keys, network or model downloads),
    for UI work and end-to-end tests. Tracing is off so fixture runs never reach LangSmith."""
    from ..core.fixture import Fixture

    offline = settings.model_copy(update={"langsmith_api_key": None, "openrouter_api_key": None})
    return RunManager(offline, fixture=Fixture())


def create_app(settings: CoreSettings | None = None, manager: RunManager | None = None, store: PipelineStore | None = None) -> FastAPI:
    settings = settings or CoreSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.manager = manager or (fixture_manager(settings) if os.environ.get("PRIVFRAME_FIXTURE") == "1" else RunManager(settings))
        app.state.store = store or PipelineStore(settings.pipeline_dir)
        data = app.state.manager.cases_dir.parent
        app.state.datasets = DatasetStore(data / "datasets", lambda: (path, sample_notes(path)) if (path := sample_video(settings)) else None)
        def combination_issues(profile_id: str, overrides: dict[str, str], params: dict[str, float]) -> list[dict[str, Any]]:
            """The Lab's profile check for a base profile with some parts and values swapped."""
            spec = app.state.store.get_profile(profile_id.removesuffix("-fixture"))["spec"]
            return all_issues(app.state.manager, app.state.store, {**spec, "selections": {**spec["selections"], **overrides}, "params": {**(spec.get("params") or {}), **params}})

        app.state.experiments = ExperimentManager(app.state.manager, app.state.datasets, data / "experiments", topology.node_labels(), check=combination_issues)
        yield
        if manager is None:
            app.state.manager.close()

    app = FastAPI(title="PrivFrame", version="2.0-m4", lifespan=lifespan)

    @app.exception_handler(ServiceError)
    def refused(request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    for router in (meta, cases, runs, lab):
        app.include_router(router)
    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.frontend("/", directory=str(dist))
    return app


app = create_app()
