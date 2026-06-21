from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.db.session import AsyncSessionLocal
from app.repositories.documents import DocumentRepository
from app.services.chunk_enrichment_service import ChunkEnrichmentService
from app.services.chunking_service import ChunkingService
from app.services.document_parser_service import DocumentParserService
from app.services.document_profiles import profile_config, resolve_profile_with_evidence
from app.services.document_service import DocumentService
from app.services.elasticsearch_indexing_service import ElasticsearchIndexingService
from app.services.elasticsearch_store import get_elasticsearch_store
from app.services.embeddings.factory import get_embedding_provider
from app.services.embeddings.sparse_factory import get_sparse_embedding_provider
from app.services.llms.factory import get_llm_provider
from app.services.storage import get_storage_client
from app.services.vector_indexing_service import VectorIndexingService
from app.services.vector_store import get_vector_store

JobStatus = Literal["queued", "running", "succeeded", "failed"]
StepState = Literal["idle", "running", "succeeded", "failed"]
LogLevel = Literal["info", "success", "error"]

PIPELINE_STEPS = ("upload", "parse", "chunk", "enrich", "index")
logger = logging.getLogger(__name__)


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
    ingestion_profile: str = "auto"
    resolved_ingestion_profile: str | None = None
    steps: dict[str, IngestionStep] = field(
        default_factory=lambda: {step: IngestionStep(name=step) for step in PIPELINE_STEPS}
    )
    logs: list[IngestionLog] = field(default_factory=list)


@dataclass(frozen=True)
class QueuedUpload:
    filename: str
    content_type: str | None
    content: bytes
    organization_id: UUID | None = None
    ingestion_profile: str = "auto"
    access: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueuedDocumentReingestion:
    document_id: UUID
    filename: str
    content_type: str | None = None
    ingestion_profile: str = "auto"


class IngestionQueue:
    def __init__(self) -> None:
        self._jobs: dict[UUID, IngestionJob] = {}
        self._payloads: dict[UUID, QueuedUpload | QueuedDocumentReingestion] = {}

    def enqueue_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        organization_id: UUID | None = None,
        ingestion_profile: str = "auto",
        access: dict[str, Any] | None = None,
    ) -> IngestionJob:
        now = datetime.now(UTC)
        job = IngestionJob(
            job_id=uuid4(),
            filename=filename,
            content_type=content_type,
            status="queued",
            created_at=now,
            updated_at=now,
            ingestion_profile=self._normalize_profile(ingestion_profile),
        )
        self._jobs[job.job_id] = job
        self._payloads[job.job_id] = QueuedUpload(
            filename=filename,
            content_type=content_type,
            content=content,
            organization_id=organization_id,
            ingestion_profile=job.ingestion_profile,
            access=dict(access or {}),
        )
        self._log(
            job,
            step="queue",
            level="info",
            message=(
                "Job queued for automatic ingestion "
                f"with profile={job.ingestion_profile}."
            ),
        )
        return job

    def enqueue_document_reingestion(
        self,
        *,
        document_id: UUID,
        filename: str,
        content_type: str | None = None,
        ingestion_profile: str = "auto",
    ) -> IngestionJob:
        now = datetime.now(UTC)
        normalized_profile = self._normalize_profile(ingestion_profile)
        job = IngestionJob(
            job_id=uuid4(),
            filename=filename,
            content_type=content_type,
            status="queued",
            created_at=now,
            updated_at=now,
            document_id=document_id,
            ingestion_profile=normalized_profile,
        )
        self._jobs[job.job_id] = job
        self._payloads[job.job_id] = QueuedDocumentReingestion(
            document_id=document_id,
            filename=filename,
            content_type=content_type,
            ingestion_profile=normalized_profile,
        )
        self._log(
            job,
            step="queue",
            level="info",
            message=(
                "Job queued to re-run document ingestion from parse "
                f"with profile={normalized_profile}."
            ),
        )
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
        is_reingestion = isinstance(payload, QueuedDocumentReingestion)

        try:
            storage = get_storage_client()

            # Each pipeline stage owns a fresh AsyncSession. Reusing one session
            # across multiple services that commit independently can leave ORM
            # instances expired/stale and trigger SQLAlchemy MissingGreenlet when
            # a later stage reads an attribute outside the greenlet bridge.
            if isinstance(payload, QueuedUpload):
                async with AsyncSessionLocal() as session:
                    upload_response = await self._run_step(
                        job,
                        "upload",
                        lambda: DocumentService(
                            repository=DocumentRepository(session),
                            storage=storage,
                        ).upload_document(
                            self._to_upload_file(payload),
                            organization_id=payload.organization_id,
                            access=payload.access,
                        ),
                        lambda response: {
                            "document_id": str(response.document_id),
                            "filename": response.filename,
                            "status": response.status,
                            "storage_path": response.storage_path,
                            "ingestion_profile": job.ingestion_profile,
                        },
                    )
                document_id = upload_response.document_id
            else:
                document_id = payload.document_id
                async with AsyncSessionLocal() as session:
                    await self._run_step(
                        job,
                        "upload",
                        lambda: self._reuse_existing_document(
                            document_id=document_id,
                            repository=DocumentRepository(session),
                        ),
                        lambda response: {
                            "document_id": str(response.document_id),
                            "filename": response.filename,
                            "status": response.status,
                            "storage_path": response.storage_path,
                            "reingest_existing_document": True,
                            "ingestion_profile": job.ingestion_profile,
                        },
                    )
            job.document_id = document_id

            async with AsyncSessionLocal() as session:
                await self._run_step(
                    job,
                    "parse",
                    lambda: DocumentParserService(
                        repository=DocumentRepository(session),
                        storage=storage,
                    ).parse_document(document_id, force_reparse=is_reingestion),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "character_count": response.character_count,
                        "preview": response.preview,
                    },
                )

            async with AsyncSessionLocal() as session:
                chunk_repository = DocumentRepository(session)
                resolved_profile = await self._resolve_profile_for_document(
                    document_id=document_id,
                    repository=chunk_repository,
                    requested_profile=job.ingestion_profile,
                    filename=job.filename,
                    content_type=job.content_type,
                )
                job.resolved_ingestion_profile = resolved_profile
                await self._run_step(
                    job,
                    "chunk",
                    lambda: ChunkingService(
                        repository=chunk_repository,
                        storage=storage,
                    ).chunk_document(document_id, profile=resolved_profile),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "chunk_count": response.chunk_count,
                        "ingestion_profile": job.ingestion_profile,
                        "resolved_ingestion_profile": resolved_profile,
                        "preview": [chunk.model_dump(mode="json") for chunk in response.preview],
                    },
                )

            async with AsyncSessionLocal() as session:
                enrich_response = await self._run_step(
                    job,
                    "enrich",
                    lambda: ChunkEnrichmentService(
                        repository=DocumentRepository(session),
                        llm_provider=get_llm_provider(),
                    ).enrich_document(document_id),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "enriched_count": response.enriched_count,
                        "failed_count": response.failed_count,
                        "skipped_count": response.skipped_count,
                        "preview": [item.model_dump(mode="json") for item in response.preview],
                    },
                )
                self._log_enrichment_summary(job, enrich_response)

            await self._run_step(
                job,
                "index",
                lambda: self._index_document_search_backends(document_id),
                lambda response: response,
            )
        except Exception as exc:
            logger.exception(
                "Automatic ingestion failed job=%s document=%s",
                job.job_id,
                job.document_id,
            )
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

    def _log_enrichment_summary(self, job: IngestionJob, response: Any) -> None:
        failed_count = int(getattr(response, "failed_count", 0) or 0)
        skipped_count = int(getattr(response, "skipped_count", 0) or 0)
        enriched_count = int(getattr(response, "enriched_count", 0) or 0)
        status = str(getattr(response, "status", "unknown") or "unknown")
        if failed_count and not enriched_count:
            level: LogLevel = "error"
        elif failed_count or skipped_count:
            level = "info"
        else:
            level = "success"
        self._log(
            job,
            step="enrich",
            level=level,
            message=(
                "enrich summary: "
                f"status={status}, enriched_count={enriched_count}, "
                f"failed_count={failed_count}, skipped_count={skipped_count}."
            ),
        )

    async def _reuse_existing_document(
        self,
        *,
        document_id: UUID,
        repository: DocumentRepository,
    ) -> SimpleNamespace:
        document = await repository.get_document(document_id)
        if document is None:
            raise ValueError("Document not found for re-ingestion.")
        file = await repository.get_primary_document_file(document_id)
        if file is None:
            raise ValueError("Document file metadata not found for re-ingestion.")
        return SimpleNamespace(
            document_id=document.id,
            filename=getattr(file, "filename", document.title),
            status=document.status,
            storage_path=getattr(file, "storage_path", ""),
        )

    async def _index_document_search_backends(self, document_id: UUID) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            vector_response = await VectorIndexingService(
                repository=DocumentRepository(session),
                embedding_provider=get_embedding_provider(),
                vector_store=get_vector_store(),
                sparse_embedding_provider=get_sparse_embedding_provider(),
            ).index_document(document_id)

        elasticsearch_store = get_elasticsearch_store()
        try:
            async with AsyncSessionLocal() as session:
                elasticsearch_response = await ElasticsearchIndexingService(
                    repository=DocumentRepository(session),
                    store=elasticsearch_store,
                ).index_document(document_id)
            elasticsearch_output = {
                "status": elasticsearch_response.status,
                "indexed_chunk_count": elasticsearch_response.indexed_chunk_count,
                "index_name": elasticsearch_response.index_name,
                "skipped": elasticsearch_response.skipped,
            }
        except Exception as exc:
            logger.warning(
                "Elasticsearch indexing failed for document=%s; Qdrant index result is kept.",
                document_id,
                exc_info=True,
            )
            elasticsearch_output = {
                "status": "failed",
                "indexed_chunk_count": 0,
                "index_name": elasticsearch_store.index_name,
                "skipped": False,
                "error": str(exc),
            }

        return {
            "document_id": str(vector_response.document_id),
            "status": vector_response.status,
            "indexed_chunk_count": vector_response.indexed_chunk_count,
            "qdrant": {
                "status": vector_response.status,
                "indexed_chunk_count": vector_response.indexed_chunk_count,
            },
            "elasticsearch": elasticsearch_output,
        }

    async def _resolve_profile_for_document(
        self,
        *,
        document_id: UUID,
        repository: DocumentRepository,
        requested_profile: str,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        document = await repository.get_document(document_id)
        if document is None:
            raise ValueError("Document not found for profile resolution.")
        detection = resolve_profile_with_evidence(
            requested_profile,
            text=document.parsed_text,
            filename=filename or document.title,
            content_type=content_type,
        )
        resolved = detection["profile"]
        resolved_config = profile_config(resolved)
        job_metadata = {
            "ingestion_profile": requested_profile,
            "resolved_ingestion_profile": resolved,
            "profile_detection_mode": "auto" if requested_profile == "auto" else "explicit",
            "profile_detection_score": detection["score"],
            "profile_detection_evidence": detection["evidence"],
            "profile_detection_candidates": detection["candidates"],
            "ingestion_profile_snapshot": resolved_config,
        }
        await repository.update_document_metadata(document, job_metadata)
        await repository.commit()
        return resolved

    @staticmethod
    def _normalize_profile(profile: str | None) -> str:
        normalized = str(profile or "auto").strip().lower() or "auto"
        return normalized

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
