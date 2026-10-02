import fcntl
import json
from uuid import uuid4

import httpx
from celery import Celery
from celery.concurrency import get_implementation
from celery.concurrency.solo import TaskPool
from celery.signals import worker_init

from privframe.config import Settings

settings = Settings()
celery_app = Celery("privframe", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], task_ignore_result=True,
    worker_prefetch_multiplier=1, task_acks_late=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 7200},
    task_publish_retry=False,
    broker_connection_timeout=2,
    worker_pool="solo", worker_concurrency=1,
)
_guard = None


def worker_service():
    """One process owns local Chroma, even if another worker is accidentally started."""
    global _guard
    from privframe.bootstrap import get_service
    service = get_service()
    if _guard is None:
        handle = (service.settings.data_dir / "worker.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("Another worker owns this data directory; use one solo worker") from None
        _guard = handle
    return service


@worker_init.connect
def own_worker_storage(sender=None, **_):
    if get_implementation(sender.pool_cls) is not TaskPool or sender.concurrency != 1:
        raise SystemExit("PrivFrame local storage requires --pool=solo --concurrency=1")
    try:
        worker_service()
    except Exception as error:
        # Celery signals log/catch ordinary exceptions; SystemExit stops startup.
        raise SystemExit(str(error)) from error


@celery_app.task(name="privframe.stage")
def stage(run_id: str, stage_name: str, attempt_id: str):
    from privframe.bootstrap import create_pipeline
    create_pipeline(worker_service()).run_stage(run_id, stage_name, attempt_id)


@celery_app.task(name="privframe.extract")
def extract(document_id: str):
    from privframe.bootstrap import create_pipeline
    create_pipeline(worker_service()).extract(document_id)


@celery_app.task(name="privframe.index")
def index(index_id: str):
    from privframe.bootstrap import create_pipeline
    create_pipeline(worker_service()).build_index(index_id)


@celery_app.task(name="privframe.seed")
def seed():
    from privframe.bootstrap import create_pipeline
    from privframe.domain.models import DocumentRecord, SourceMetadata
    from privframe.infrastructure.storage import sha256_file
    service = worker_service()
    manifest = json.loads((service.settings.profiles_dir / "seed-manifest.json").read_text())
    pipeline = create_pipeline(service)
    for entry in manifest["documents"]:
        document_id = uuid4().hex
        format_name = entry["format"]
        artifact = f"documents/{document_id}/original.{format_name}"
        path = service.artifacts.resolve(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        error = None
        try:
            with httpx.stream("GET", entry["download_url"], follow_redirects=True,
                              timeout=service.settings.network_timeout_seconds) as response:
                response.raise_for_status()
                total = 0
                with path.open("wb") as handle:
                    for block in response.iter_bytes():
                        total += len(block)
                        if total > service.settings.upload_limit_mb * 1024 * 1024:
                            raise ValueError("Seed document exceeds upload limit")
                        handle.write(block)
        except Exception as caught:
            error = str(caught)
        digest = sha256_file(path) if path.exists() else "unavailable"
        metadata = SourceMetadata.model_validate(entry["metadata"])
        duplicate = next((doc for doc in service.repository.list("document", DocumentRecord)
                          if doc.content_hash == digest and doc.metadata == metadata and doc.status == "READY"), None)
        if duplicate:
            path.unlink(missing_ok=True)
            continue
        document = DocumentRecord(id=document_id, filename=entry["filename"], format=format_name,
                                  content_hash=digest, metadata=metadata,
                                  status="FAILED" if error else "UPLOADED", error=error)
        service.repository.insert("document", document)
        if not error:
            pipeline.extract(document.id)
