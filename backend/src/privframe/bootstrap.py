import importlib.metadata
import os
import platform
from functools import lru_cache

from dotenv import load_dotenv

from privframe.application.pipeline import Adapters, Pipeline
from privframe.application.service import Service
from privframe.config import ROOT, Settings, fingerprint
from privframe.infrastructure.storage import ArtifactStore, Repository, sha256_file


def runtime_metadata() -> dict:
    packages = ["privframe", "langchain-core", "langchain-openai", "langchain-chroma", "chromadb",
                "opencv-python-headless", "numpy", "pdfplumber", "celery"]
    versions = {}
    for name in packages:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    source = {str(path.relative_to(ROOT)): sha256_file(path) for path in sorted(
        (ROOT / "backend/src/privframe").rglob("*.py"))}
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": versions,
            "source_fingerprint": fingerprint(source),
            "lock_hash": sha256_file(ROOT / "uv.lock") if (ROOT / "uv.lock").exists() else None}


class CeleryDispatcher:
    def send(self, task: str, *args: str) -> str:
        from privframe.worker import celery_app
        return celery_app.send_task(f"privframe.{task}", args=args).id


def create_service(settings: Settings | None = None, dispatcher=None) -> Service:
    load_dotenv(ROOT / ".env", override=False)
    settings = settings or Settings()
    artifacts = ArtifactStore(settings.data_dir)
    repository = Repository(settings.data_dir / "state.sqlite3")
    return Service(settings, repository, artifacts, dispatcher or CeleryDispatcher(), runtime_metadata)


def create_pipeline(service: Service) -> Pipeline:
    from privframe.adapters.decision import create_decider
    from privframe.adapters.rag import DocumentExtractor, LangChainChunker, create_reranker, create_retriever
    from privframe.adapters.report import SnapshotReporter
    from privframe.adapters.vision import OpenCVRenderer, OpenCVVideoAnalyzer
    adapters = Adapters(
        extractor=DocumentExtractor(), chunker=LangChainChunker, retriever=create_retriever,
        decider=create_decider,
        analyzer=OpenCVVideoAnalyzer(service.artifacts, service.settings.model_path),
        renderer=OpenCVRenderer(service.artifacts), reporter=SnapshotReporter(service.artifacts).generate,
        reranker=create_reranker,
    )
    return Pipeline(service.repository, service.artifacts, adapters)


@lru_cache
def get_service() -> Service:
    return create_service()


def openai_configured() -> bool:
    # Credential values never enter a run, artifact, response, or log.
    return bool(os.environ.get("OPENAI_API_KEY"))
