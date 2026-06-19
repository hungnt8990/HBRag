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

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.documents import DocumentRepository
from app.repositories.ingestion_profiles import IngestionProfileRepository
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository
from app.services.chunk_enrichment_service import ChunkEnrichmentService
from app.services.chunking_service import ChunkingService
from app.services.document_parser_service import DocumentParserService
from app.services.document_profiles import profile_config, resolve_profile_with_evidence
from app.services.document_service import DocumentService
from app.services.embeddings.factory import get_embedding_provider
from app.services.embeddings.sparse_factory import get_sparse_embedding_provider
from app.services.ingestion_profiles import load_profile_configs
from app.services.knowledge_artifact_compiler import KnowledgeArtifactCompiler, KnowledgeArtifactCompilerConfig
from app.services.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.llms.factory import build_llm_provider_or_error
from app.services.rag_runtime_config import RagRuntimeConfigValues, load_rag_runtime_config
from app.services.storage import get_storage_client
from app.services.vector_indexing_service import VectorIndexingService
from app.services.vector_store import get_artifact_vector_store, get_vector_store

JobStatus = Literal["queued", "running", "succeeded", "failed"]
StepState = Literal["idle", "running", "succeeded", "failed"]
LogLevel = Literal["info", "success", "error"]

PIPELINE_STEPS = ("upload", "parse", "chunk", "compile_artifacts", "enrich", "index")
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


@dataclass(frozen=True)
class EnrichmentRuntimeConfig:
    step: str
    provider: str | None
    base_url: str | None
    model: str | None
    max_chars: int
    version: str


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
                profile_repository = IngestionProfileRepository(session)
                await load_profile_configs(profile_repository)
                await profile_repository.commit()

            rag_config = await self._load_rag_runtime_config()

            async with AsyncSessionLocal() as session:
                artifact_compile_response = await self._run_step(
                    job,
                    "compile_artifacts",
                    lambda: self._compile_knowledge_artifacts(
                        document_id=document_id,
                        session=session,
                        rag_config=rag_config,
                    ),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "artifact_count": response.artifact_count,
                        "failed_count": response.failed_count,
                        "skipped_count": response.skipped_count,
                        "config_source": response.config_source,
                        "enable_knowledge_artifact_compilation": rag_config.enable_knowledge_artifact_compilation,
                        "enable_llm_artifact_extraction": rag_config.enable_llm_artifact_extraction,
                        "error": response.error,
                    },
                )
                self._log(
                    job,
                    step="compile_artifacts",
                    level="success" if artifact_compile_response.status in {"compiled", "skipped"} else "error",
                    message=(
                        "knowledge artifact compilation summary: "
                        f"status={artifact_compile_response.status}, "
                        f"artifact_count={artifact_compile_response.artifact_count}, "
                        f"failed_count={artifact_compile_response.failed_count}, "
                        f"skipped_count={artifact_compile_response.skipped_count}."
                    ),
                )

            offline_enrichment_enabled = bool(
                getattr(settings, "enable_offline_enrichment", True)
            )
            chunk_enrichment_enabled = bool(
                offline_enrichment_enabled and rag_config.enable_chunk_enrichment_at_ingest
            )
            use_enriched_content_for_embedding = bool(
                getattr(settings, "use_enriched_content_for_embedding", True)
                and chunk_enrichment_enabled
            )
            retrieval_enrichment_enabled = bool(rag_config.enable_chunk_enrichment_at_retrieval)
            enrichment_force_on_reingest = bool(settings.enrichment_force_on_reingest)
            enrichment_update_keyword_search_vector = bool(
                settings.enrichment_update_keyword_search_vector
            )
            force_enrichment = bool(is_reingestion and enrichment_force_on_reingest)
            enrichment_runtime = self._enrichment_runtime_config(force_enrichment=force_enrichment)
            self._log(
                job,
                step="profile",
                level="info",
                message=(
                    "RAG profile config loaded: "
                    f"profile={resolved_profile}, config_source=PostgreSQL, "
                    "enrichment_runtime_config_source=PostgreSQL/.env fallback, "
                    f"enable_offline_enrichment={offline_enrichment_enabled}, "
                    f"chunk_enrichment_enabled={chunk_enrichment_enabled}, "
                    "use_enriched_content_for_embedding="
                    f"{use_enriched_content_for_embedding}, "
                    f"retrieval_enrichment_enabled={retrieval_enrichment_enabled}, "
                    f"enrichment_force_on_reingest={enrichment_force_on_reingest}, "
                    "enrichment_update_keyword_search_vector="
                    f"{enrichment_update_keyword_search_vector}, "
                    f"enrichment_step={enrichment_runtime.step}, "
                    "enrichment_provider="
                    f"{enrichment_runtime.provider or 'runtime_default'}, "
                    f"enrichment_base_url={enrichment_runtime.base_url or 'runtime_default'}, "
                    f"enrichment_model={enrichment_runtime.model or 'runtime_default'}, "
                    f"enrichment_max_chars={enrichment_runtime.max_chars}, "
                    f"enrichment_version={enrichment_runtime.version}."
                ),
            )

            async with AsyncSessionLocal() as session:
                enrich_response = await self._run_step(
                    job,
                    "enrich",
                    lambda: ChunkEnrichmentService(
                        repository=DocumentRepository(session),
                        llm_provider=build_llm_provider_or_error(
                            provider=enrichment_runtime.provider,
                            base_url=enrichment_runtime.base_url,
                            model=enrichment_runtime.model,
                        ),
                        enabled=chunk_enrichment_enabled,
                        provider=enrichment_runtime.provider,
                        model=enrichment_runtime.model,
                        max_chars=enrichment_runtime.max_chars,
                        version=enrichment_runtime.version,
                    ).enrich_document(
                        document_id,
                        force=force_enrichment,
                        update_keyword_search_vector=enrichment_update_keyword_search_vector,
                        provider=enrichment_runtime.provider,
                        model=enrichment_runtime.model,
                        max_chars=enrichment_runtime.max_chars,
                        version=enrichment_runtime.version,
                    ),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "profile": resolved_profile,
                        "config_source": "PostgreSQL",
                        "enrichment_runtime_config_source": "PostgreSQL/.env fallback",
                        "enable_offline_enrichment": offline_enrichment_enabled,
                        "chunk_enrichment_enabled": chunk_enrichment_enabled,
                        "embedding_enrichment_enabled": chunk_enrichment_enabled,
                        "use_enriched_content_for_embedding": (
                            use_enriched_content_for_embedding
                        ),
                        "retrieval_enrichment_enabled": retrieval_enrichment_enabled,
                        "enrichment_force_on_reingest": enrichment_force_on_reingest,
                        "enrichment_update_keyword_search_vector": (
                            enrichment_update_keyword_search_vector
                        ),
                        "enrichment_step": enrichment_runtime.step,
                        "chunk_enrichment_provider": enrichment_runtime.provider,
                        "chunk_enrichment_base_url": enrichment_runtime.base_url,
                        "chunk_enrichment_model": enrichment_runtime.model,
                        "chunk_enrichment_max_chars": enrichment_runtime.max_chars,
                        "chunk_enrichment_version": enrichment_runtime.version,
                        "force": force_enrichment,
                        "enriched_count": response.enriched_count,
                        "failed_count": response.failed_count,
                        "skipped_count": response.skipped_count,
                        "preview": [item.model_dump(mode="json") for item in response.preview],
                    },
                )
                self._log_enrichment_summary(job, enrich_response)

            async with AsyncSessionLocal() as session:
                await self._run_step(
                    job,
                    "index",
                    lambda: self._index_document_and_artifacts(
                        document_id=document_id,
                        session=session,
                        rag_config=rag_config,
                        use_enriched_content_for_embedding=use_enriched_content_for_embedding,
                    ),
                    lambda response: {
                        "document_id": str(response.document_id),
                        "status": response.status,
                        "indexed_chunk_count": response.indexed_chunk_count,
                        "indexed_artifact_count": response.indexed_artifact_count,
                        "artifact_index_status": response.artifact_index_status,
                        "artifact_index_error": response.artifact_index_error,
                        "profile": resolved_profile,
                        "use_enriched_content_for_embedding": (
                            use_enriched_content_for_embedding
                        ),
                    },
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

    async def _load_rag_runtime_config(self) -> RagRuntimeConfigValues:
        try:
            async with AsyncSessionLocal() as session:
                repository = RagRuntimeConfigRepository(session)
                config = await load_rag_runtime_config(repository)
                await repository.commit()
                return config
        except Exception:
            logger.exception(
                "Failed to load RAG runtime config from Postgres; using settings fallback."
            )
            return self._settings_rag_runtime_config_fallback()

    @staticmethod
    def _settings_rag_runtime_config_fallback() -> RagRuntimeConfigValues:
        return RagRuntimeConfigValues(
            enable_chunk_enrichment_at_ingest=bool(
                getattr(settings, "enable_chunk_enrichment_at_ingest", getattr(settings, "chunk_enrichment_enabled", False))
                or getattr(settings, "chunk_enrichment_enabled", False)
            ),
            enable_chunk_enrichment_at_retrieval=bool(
                getattr(settings, "enable_chunk_enrichment_at_retrieval", getattr(settings, "retrieval_enrichment_enabled", False))
                or getattr(settings, "retrieval_enrichment_enabled", False)
            ),
            enable_knowledge_artifact_compilation=bool(getattr(settings, "enable_knowledge_artifact_compilation", True)),
            enable_llm_artifact_extraction=bool(getattr(settings, "enable_llm_artifact_extraction", False)),
            enable_artifact_first_retrieval=bool(getattr(settings, "enable_artifact_first_retrieval", True)),
            enable_chunk_fallback=bool(getattr(settings, "enable_chunk_fallback", True)),
            enable_neighbor_expansion=bool(getattr(settings, "enable_neighbor_expansion", getattr(settings, "enable_context_expansion", True))),
            enable_graph_expansion=bool(getattr(settings, "enable_graph_expansion", getattr(settings, "graph_expansion_enabled", True))),
            artifact_confidence_threshold=IngestionQueue._float_config_value(
                getattr(settings, "artifact_confidence_threshold", 0.45),
                default=0.45,
            ),
            retrieval_token_budget=IngestionQueue._positive_int(getattr(settings, "retrieval_token_budget", 6000), default=6000),
            max_artifacts=IngestionQueue._positive_int(getattr(settings, "max_artifacts", 6), default=6),
            max_chunks=IngestionQueue._positive_int(getattr(settings, "max_chunks", 8), default=8),
        )

    async def _compile_knowledge_artifacts(
        self,
        *,
        document_id: UUID,
        session: Any,
        rag_config: RagRuntimeConfigValues,
    ) -> SimpleNamespace:
        if not rag_config.enable_knowledge_artifact_compilation:
            return SimpleNamespace(
                document_id=document_id,
                status="skipped",
                artifact_count=0,
                failed_count=0,
                skipped_count=1,
                config_source="PostgreSQL/.env fallback",
                error=None,
            )

        document_repository = DocumentRepository(session)
        artifact_repository = KnowledgeArtifactRepository(session)
        compiler = KnowledgeArtifactCompiler(
            config=KnowledgeArtifactCompilerConfig(
                enable_llm_extraction=rag_config.enable_llm_artifact_extraction,
            )
        )
        try:
            document = await document_repository.get_document(document_id)
            if document is None:
                raise ValueError("Document not found for knowledge artifact compilation.")
            chunks = await document_repository.list_chunks_for_document(document_id)
            artifacts = compiler.compile_document(
                document=document,
                chunks=chunks,
                docling_metadata=dict((document.document_metadata or {}).get("parsed_metadata") or {}),
            )
            await artifact_repository.replace_for_document(document_id, artifacts)
            await artifact_repository.commit()
            return SimpleNamespace(
                document_id=document_id,
                status="compiled",
                artifact_count=len(artifacts),
                failed_count=sum(1 for artifact in artifacts if artifact.status == "failed"),
                skipped_count=sum(1 for artifact in artifacts if artifact.status == "skipped"),
                config_source="PostgreSQL/.env fallback",
                error=None,
            )
        except Exception as exc:
            logger.exception("Knowledge artifact compilation failed for document=%s", document_id)
            try:
                await artifact_repository.rollback()
            except Exception:
                pass
            try:
                failed_artifact = compiler.failed_artifact(document_id=document_id, error=str(exc))
                await artifact_repository.replace_for_document(document_id, [failed_artifact])
                await artifact_repository.commit()
            except Exception:
                logger.exception(
                    "Failed to persist failed knowledge artifact marker for document=%s",
                    document_id,
                )
                try:
                    await artifact_repository.rollback()
                except Exception:
                    pass
            return SimpleNamespace(
                document_id=document_id,
                status="failed",
                artifact_count=0,
                failed_count=1,
                skipped_count=0,
                config_source="PostgreSQL/.env fallback",
                error=str(exc),
            )

    async def _index_document_and_artifacts(
        self,
        *,
        document_id: UUID,
        session: Any,
        rag_config: RagRuntimeConfigValues,
        use_enriched_content_for_embedding: bool,
    ) -> SimpleNamespace:
        document_repository = DocumentRepository(session)
        chunk_response = await VectorIndexingService(
            repository=document_repository,
            embedding_provider=get_embedding_provider(),
            vector_store=get_vector_store(),
            sparse_embedding_provider=get_sparse_embedding_provider(),
        ).index_document(
            document_id,
            use_enriched_content_for_embedding=use_enriched_content_for_embedding,
        )

        artifact_index_status = "skipped"
        artifact_index_error = None
        indexed_artifact_count = 0
        if rag_config.enable_knowledge_artifact_compilation:
            try:
                artifact_response = await KnowledgeArtifactIndexingService(
                    repository=KnowledgeArtifactRepository(session),
                    embedding_provider=get_embedding_provider(),
                    vector_store=get_artifact_vector_store(),
                    sparse_embedding_provider=get_sparse_embedding_provider(),
                ).index_document(document_id)
                artifact_index_status = artifact_response.status
                indexed_artifact_count = artifact_response.indexed_artifact_count
            except Exception as exc:
                logger.exception("Knowledge artifact indexing failed for document=%s", document_id)
                artifact_index_status = "failed"
                artifact_index_error = str(exc)

        return SimpleNamespace(
            document_id=chunk_response.document_id,
            status=chunk_response.status,
            indexed_chunk_count=chunk_response.indexed_chunk_count,
            indexed_artifact_count=indexed_artifact_count,
            artifact_index_status=artifact_index_status,
            artifact_index_error=artifact_index_error,
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

    def _enrichment_runtime_config(
        self,
        *,
        force_enrichment: bool,
    ) -> EnrichmentRuntimeConfig:
        chunk_provider = self._optional_config_string(settings.chunk_enrichment_provider)
        chunk_base_url = self._optional_config_string(settings.chunk_enrichment_base_url)
        chunk_model = self._optional_config_string(settings.chunk_enrichment_model)
        chunk_max_chars = self._positive_int(settings.chunk_enrichment_max_chars, default=6000)
        chunk_version = self._optional_config_string(settings.chunk_enrichment_version) or "v1"

        embedding_provider = self._optional_config_string(settings.embedding_enrichment_provider)
        embedding_base_url = self._optional_config_string(settings.embedding_enrichment_base_url)
        embedding_model = self._optional_config_string(settings.embedding_enrichment_model)
        embedding_has_override = bool(embedding_provider or embedding_base_url or embedding_model)
        embedding_max_chars = (
            self._positive_int(settings.embedding_enrichment_max_chars, default=chunk_max_chars)
            if embedding_has_override
            else chunk_max_chars
        )
        embedding_version = (
            self._optional_config_string(settings.embedding_enrichment_version)
            if embedding_has_override
            else chunk_version
        ) or chunk_version

        if force_enrichment:
            reingest_provider = self._optional_config_string(settings.reingest_enrichment_provider)
            reingest_base_url = self._optional_config_string(settings.reingest_enrichment_base_url)
            reingest_model = self._optional_config_string(settings.reingest_enrichment_model)
            reingest_has_override = bool(reingest_provider or reingest_base_url or reingest_model)
            return EnrichmentRuntimeConfig(
                step="reingest",
                provider=reingest_provider or embedding_provider or chunk_provider,
                base_url=reingest_base_url or embedding_base_url or chunk_base_url,
                model=reingest_model or embedding_model or chunk_model,
                max_chars=(
                    self._positive_int(
                        settings.reingest_enrichment_max_chars,
                        default=embedding_max_chars,
                    )
                    if reingest_has_override
                    else embedding_max_chars
                ),
                version=(
                    self._optional_config_string(settings.reingest_enrichment_version)
                    if reingest_has_override
                    else embedding_version
                ) or embedding_version,
            )

        return EnrichmentRuntimeConfig(
            step="embedding",
            provider=embedding_provider or chunk_provider,
            base_url=embedding_base_url or chunk_base_url,
            model=embedding_model or chunk_model,
            max_chars=embedding_max_chars,
            version=embedding_version,
        )

    @staticmethod
    def _positive_int(value: Any, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _float_config_value(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_config_string(value: Any) -> str | None:
        if value is None:
            return None
        clean = " ".join(str(value).split()).strip()
        return clean or None

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
