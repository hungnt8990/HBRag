from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.db.session import AsyncSessionLocal
from app.repositories.documents import DocumentRepository
from app.services.chunking_service import ChunkingService
from app.services.document_parser_service import DocumentParserService
from app.services.document_service import DocumentService
from app.services.embeddings.factory import get_embedding_provider
from app.services.storage import get_storage_client
from app.services.vector_indexing_service import VectorIndexingService
from app.services.vector_store import get_vector_store

JobStatus = Literal["queued", "running", "succeeded", "failed"]
StepState = Literal["idle", "running", "succeeded", "failed"]
LogLevel = Literal["info", "success", "error"]

PIPELINE_STEPS = ("upload", "parse", "chunk", "index")


@dataclass
class IngestionStep:
    name: str
    state: StepState = "idle"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class IngestionLog:
    timestamp: datetime
    step: str
    level: LogLevel
    message: str
    duration_ms: int | None = None


@dataclass
class IngestionJob:
    job_id: UUID
    filename: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    content_type: str | None = None
    document_id: UUID | None = None
    error: str | None = None
    steps: dict[str, IngestionStep] = field(
        default_factory=lambda: {step: IngestionStep(name=step) for step in PIPELINE_STEPS}
    )
    logs: list[IngestionLog] = field(default_factory=list)


@dataclass(frozen=True)
class QueuedUpload:
    filename: str
    content_type: str | None
    content: bytes


class IngestionQueue:
    def __init__(self) -> None:
        self._jobs: dict[UUID, IngestionJob] = {}
        self._payloads: dict[UUID, QueuedUpload] = {}

    def enqueue_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> IngestionJob:
        now = datetime.now(UTC)
        job = IngestionJob(
            job_id=uuid4(),
            filename=filename,
            content_type=content_type,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.job_id] = job
        self._payloads[job.job_id] = QueuedUpload(
            filename=filename,
            content_type=content_type,
            content=content,
        )
        self._log(job, step="queue", level="info", message="Job queued for automatic ingestion.")
        return job

    def get_job(self, job_id: UUID) -> IngestionJob | None:
        return self._jobs.get(job_id)

    def remove_job(self, job_id: UUID) -> bool:
        self._payloads.pop(job_id, None)
        return self._jobs.pop(job_id, None) is not None

    def list_jobs(self) -> list[IngestionJob]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    async def run_job(self, job_id: UUID) -> None:
        job = self._jobs.get(job_id)
        payload = self._payloads.pop(job_id, None)
        if job is None or payload is None:
            return

        job.status = "running"
        self._touch(job)
        self._log(job, step="queue", level="info", message="Automatic ingestion started.")

        try:
            async with AsyncSessionLocal() as session:
                repository = DocumentRepository(session)
                storage = get_storage_client()

                upload_response = await self._run_step(
                    job,
                    "upload",
                    lambda: DocumentService(
                        repository=repository,
                        storage=storage,
                    ).upload_document(self._to_upload_file(payload)),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "filename": response.filename,
                        "status": response.status,
                        "storage_path": response.storage_path,
                    },
                )
                job.document_id = upload_response.document_id

                await self._run_step(
                    job,
                    "parse",
                    lambda: DocumentParserService(
                        repository=repository,
                        storage=storage,
                    ).parse_document(upload_response.document_id),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "character_count": response.character_count,
                        "preview": response.preview,
                    },
                )
                await self._run_step(
                    job,
                    "chunk",
                    lambda: ChunkingService(repository=repository).chunk_document(
                        upload_response.document_id
                    ),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "chunk_count": response.chunk_count,
                        "preview": [chunk.model_dump(mode="json") for chunk in response.preview],
                    },
                )
                await self._run_step(
                    job,
                    "index",
                    lambda: VectorIndexingService(
                        repository=repository,
                        embedding_provider=get_embedding_provider(),
                        vector_store=get_vector_store(),
                    ).index_document(upload_response.document_id),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "indexed_chunk_count": response.indexed_chunk_count,
                    },
                )
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            self._touch(job)
            self._log(job, step="queue", level="error", message=str(exc))
            return

        job.status = "succeeded"
        self._touch(job)
        self._log(job, step="queue", level="success", message="Automatic ingestion completed.")

    async def _run_step(
        self,
        job: IngestionJob,
        step_name: str,
        action,
        serialize_output,
    ):
        step = job.steps[step_name]
        step.state = "running"
        step.started_at = datetime.now(UTC)
        step.completed_at = None
        step.duration_ms = None
        step.error = None
        self._touch(job)
        self._log(job, step=step_name, level="info", message=f"{step_name} started.")

        started = time.perf_counter()
        try:
            response = await action()
        except Exception as exc:
            duration_ms = self._duration_ms(started)
            step.state = "failed"
            step.completed_at = datetime.now(UTC)
            step.duration_ms = duration_ms
            step.error = str(exc)
            self._touch(job)
            self._log(
                job,
                step=step_name,
                level="error",
                message=f"{step_name} failed: {exc}",
                duration_ms=duration_ms,
            )
            raise

        duration_ms = self._duration_ms(started)
        step.state = "succeeded"
        step.completed_at = datetime.now(UTC)
        step.duration_ms = duration_ms
        step.output = serialize_output(response)
        self._touch(job)
        self._log(
            job,
            step=step_name,
            level="success",
            message=f"{step_name} completed.",
            duration_ms=duration_ms,
        )
        return response

    @staticmethod
    def _to_upload_file(payload: QueuedUpload) -> UploadFile:
        headers = Headers({"content-type": payload.content_type or "application/octet-stream"})
        return UploadFile(
            file=BytesIO(payload.content),
            size=len(payload.content),
            filename=payload.filename,
            headers=headers,
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

    def _log(
        self,
        job: IngestionJob,
        *,
        step: str,
        level: LogLevel,
        message: str,
        duration_ms: int | None = None,
    ) -> None:
        job.logs.append(
            IngestionLog(
                timestamp=datetime.now(UTC),
                step=step,
                level=level,
                message=message,
                duration_ms=duration_ms,
            )
        )
        self._touch(job)

    @staticmethod
    def _touch(job: IngestionJob) -> None:
        job.updated_at = datetime.now(UTC)


ingestion_queue = IngestionQueue()


def get_ingestion_queue() -> IngestionQueue:
    return ingestion_queue
