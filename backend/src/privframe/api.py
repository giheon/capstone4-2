import shutil
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from redis import Redis

from privframe.application.service import Service
from privframe.bootstrap import get_service, openai_configured
from privframe.config import ROOT, Settings, load_profile
from privframe.domain.models import (
    Contract, DocumentRecord, IndexRecord, RunSnapshot, ScenarioInput, SourceMetadata, Treatment, VideoRecord,
)
from privframe.infrastructure.storage import ConflictError, sha256_file


class CreateIndex(Contract):
    document_ids: list[str]
    profile: str = "baseline"


class CreateRun(Contract):
    video_id: str
    index_id: str | None = None
    scenario: ScenarioInput
    profile: str = "baseline"
    parent_id: str | None = None


class TrackLabels(Contract):
    track_labels: dict[str, str]


class Overrides(Contract):
    treatments: dict[str, Treatment]
    no_faces_confirmed: bool = False


class RetryStage(Contract):
    stage: Literal["analyze", "decide", "render"]


class ForkRun(Contract):
    scenario: ScenarioInput | None = None
    profile: str | None = None
    index_id: str | None = None


Dependency = Annotated[Service, Depends(get_service)]


async def save_upload(file: UploadFile, service: Service, directory: str, record_id: str,
                      extension: str) -> tuple[str, str]:
    artifact = f"{directory}/{record_id}/original.{extension}"
    path = service.artifacts.resolve(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with path.open("xb") as handle:
            while block := await file.read(1024 * 1024):
                count += len(block)
                if count > service.settings.upload_limit_mb * 1024 * 1024:
                    raise HTTPException(413, "파일이 업로드 용량 제한을 초과했습니다.")
                handle.write(block)
        if count == 0:
            raise HTTPException(400, "Empty file")
        if extension == "pdf":
            with path.open("rb") as handle:
                if not handle.read(1024).lstrip().startswith(b"%PDF-"):
                    raise HTTPException(400, "File is not a PDF")
        return artifact, sha256_file(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def create_app(service: Service | None = None) -> FastAPI:
    app = FastAPI(title="PrivFrame", version="0.1.0", description="Replaceable video privacy research baseline")
    app.add_middleware(CORSMiddleware, allow_origins=[Settings().frontend_origin,
                       "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
    if service:
        app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(KeyError)
    async def not_found(_, error):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def invalid(_, error):
        return JSONResponse(status_code=409 if isinstance(error, ConflictError) else 400,
                            content={"detail": str(error)})

    @app.get("/api/health")
    def health(service: Dependency):
        available = False
        try:
            client = Redis.from_url(service.settings.redis_url, socket_timeout=0.3, socket_connect_timeout=0.3)
            available = bool(client.ping())
            client.close()
        except Exception:
            pass
        return {"status": "ok", "api_key_configured": openai_configured(), "redis_available": available,
                "ffmpeg_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                "yunet_available": service.settings.model_path.is_file(), "fixture_mode_available": True}

    @app.get("/api/profiles")
    def profiles(service: Dependency):
        return [{"name": path.stem, "config": load_profile(path.stem, service.settings)}
                for path in sorted(service.settings.profiles_dir.glob("*.yaml"))]

    @app.get("/api/documents", response_model=list[DocumentRecord])
    def documents(service: Dependency):
        return service.repository.list("document", DocumentRecord)

    @app.get("/api/documents/{document_id}", response_model=DocumentRecord)
    def get_document(document_id: str, service: Dependency):
        return service.repository.get("document", document_id, DocumentRecord)

    @app.post("/api/documents", response_model=DocumentRecord, status_code=201)
    async def upload_document(service: Dependency, file: Annotated[UploadFile, File()],
                              title: Annotated[str | None, Form()] = None,
                              source_url: Annotated[str | None, Form()] = None,
                              kind: Annotated[str, Form()] = "uploaded"):
        extension = Path(file.filename or "document").suffix.lower().lstrip(".")
        if extension not in {"pdf", "txt"}:
            raise HTTPException(400, "PDF/TXT 파일을 선택하세요.")
        metadata = SourceMetadata(title=title or file.filename or "문서", source_url=source_url or None, kind=kind)
        record_id = uuid4().hex
        _, digest = await save_upload(file, service, "documents", record_id, extension)
        document = DocumentRecord(id=record_id, filename=Path(file.filename or "document").name,
                                  format=extension, content_hash=digest,
                                  metadata=metadata)
        return service.queue_document(document)

    @app.post("/api/documents/seed", status_code=202)
    def seed_documents(service: Dependency):
        try:
            return {"task_id": service.dispatcher.send("seed")}
        except Exception as error:
            raise HTTPException(503, f"Queue unavailable: {error}") from error

    @app.post("/api/documents/{document_id}/retry", response_model=DocumentRecord)
    def retry_document(document_id: str, service: Dependency):
        document = service.repository.get("document", document_id, DocumentRecord)
        if document.status != "FAILED":
            raise ValueError("Only failed documents can be retried")
        service.dispatcher.send("extract", document_id)
        return service.repository.get("document", document_id, DocumentRecord)

    @app.get("/api/indexes", response_model=list[IndexRecord])
    def indexes(service: Dependency):
        return service.repository.list("index", IndexRecord)

    @app.get("/api/indexes/{index_id}", response_model=IndexRecord)
    def get_index(index_id: str, service: Dependency):
        return service.repository.get("index", index_id, IndexRecord)

    @app.post("/api/indexes", response_model=IndexRecord, status_code=202)
    def build_index(body: CreateIndex, service: Dependency):
        return service.create_index(body.document_ids, body.profile)

    @app.post("/api/indexes/{index_id}/retry", response_model=IndexRecord, status_code=202)
    def retry_index(index_id: str, service: Dependency):
        index = service.repository.get("index", index_id, IndexRecord)
        if index.status != "FAILED":
            raise ValueError("Only failed indexes can be retried")
        service.dispatcher.send("index", index_id)
        return service.repository.get("index", index_id, IndexRecord)

    @app.get("/api/videos", response_model=list[VideoRecord])
    def videos(service: Dependency):
        return service.repository.list("video", VideoRecord)

    @app.post("/api/videos", response_model=VideoRecord, status_code=201)
    async def upload_video(service: Dependency, file: Annotated[UploadFile, File()]):
        extension = Path(file.filename or "video").suffix.lower().lstrip(".")
        if extension not in {"mp4", "mov", "mkv", "webm", "avi"}:
            raise HTTPException(400, "MP4/MOV/MKV/WebM/AVI 영상 파일을 선택하세요.")
        record_id = uuid4().hex
        artifact, digest = await save_upload(file, service, "videos", record_id, extension)
        video = VideoRecord(id=record_id, filename=Path(file.filename or "video").name,
                            content_hash=digest, artifact=artifact)
        return service.repository.insert("video", video)

    @app.get("/api/runs", response_model=list[RunSnapshot])
    def runs(service: Dependency):
        return service.repository.list("run", RunSnapshot)

    @app.get("/api/runs/{run_id}", response_model=RunSnapshot)
    def get_run(run_id: str, service: Dependency):
        return service.repository.get("run", run_id, RunSnapshot)

    @app.post("/api/runs", response_model=RunSnapshot, status_code=202)
    def create_run(body: CreateRun, service: Dependency):
        return service.create_run(body.video_id, body.index_id, body.scenario, body.profile, body.parent_id)

    @app.post("/api/runs/{run_id}/labels", response_model=RunSnapshot)
    def labels(run_id: str, body: TrackLabels, service: Dependency):
        return service.labels(run_id, body.track_labels)

    @app.post("/api/runs/{run_id}/decide", response_model=RunSnapshot, status_code=202)
    def decide(run_id: str, service: Dependency):
        return service.request_stage(run_id, "decide")

    @app.put("/api/runs/{run_id}/overrides", response_model=RunSnapshot)
    def overrides(run_id: str, body: Overrides, service: Dependency):
        return service.overrides(run_id, body.treatments, body.no_faces_confirmed)

    @app.post("/api/runs/{run_id}/render", response_model=RunSnapshot, status_code=202)
    def render(run_id: str, service: Dependency):
        return service.request_stage(run_id, "render")

    @app.post("/api/runs/{run_id}/retry", response_model=RunSnapshot, status_code=202)
    def retry(run_id: str, body: RetryStage, service: Dependency):
        return service.request_stage(run_id, body.stage, retry=True)

    @app.post("/api/runs/{run_id}/fork", response_model=RunSnapshot, status_code=202)
    def fork(run_id: str, body: ForkRun, service: Dependency):
        if "index_id" in body.model_fields_set:
            return service.fork(run_id, body.scenario, body.profile, body.index_id)
        return service.fork(run_id, body.scenario, body.profile)

    @app.post("/api/runs/{run_id}/revise", response_model=RunSnapshot, status_code=201)
    def revise(run_id: str, service: Dependency):
        return service.revise(run_id)

    @app.post("/api/runs/{run_id}/report", response_model=RunSnapshot)
    def regenerate_report(run_id: str, service: Dependency):
        from privframe.adapters.report import SnapshotReporter
        run = service.repository.get("run", run_id, RunSnapshot)
        if run.status not in {"COMPLETED", "FAILED", "REVIEW_READY", "REVIEW_REQUIRED"}:
            raise ValueError("Wait for the active stage before generating a report")
        paths = SnapshotReporter(service.artifacts).generate(run)
        def change(value):
            value.reports = paths
        return service.repository.mutate("run", run_id, RunSnapshot, change)

    @app.get("/api/artifacts/{artifact:path}")
    def artifact(artifact: str, service: Dependency):
        if artifact.split("/", 1)[0] not in {"documents", "videos", "runs"}:
            raise HTTPException(404, "Artifact not found")
        path = service.artifacts.resolve(artifact)
        if service.artifacts.relative(path).split("/", 1)[0] not in {"documents", "videos", "runs"}:
            raise HTTPException(404, "Artifact not found")
        if not path.is_file():
            raise HTTPException(404, "Artifact not found")
        return FileResponse(path)

    frontend = ROOT / "frontend/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
