from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.documents import DocumentRepository
from app.services.graph import Neo4jClient, get_neo4j_client
from app.services.document_profiles import (
    DEFAULT_PROFILE,
    PROFILE_CONFIGS,
    PROFILE_NAMES,
)
from app.services.document_service import DocumentService
from app.services.ingestion_queue import IngestionJob, IngestionQueue, get_ingestion_queue
from app.services.vector_store import QdrantVectorStore, get_vector_store


class RuntimeConfigResponse(BaseModel):
    embedding_provider: str
    embedding_base_url: str | None
    embedding_model: str | None
    embedding_dimension: int
    reranker_provider: str
    reranker_base_url: str | None
    reranker_model: str | None
    llm_provider: str
    llm_base_url: str | None
    llm_model: str | None
    vector_collection_name: str
    auto_recreate_collection: bool
    default_chunk_size: int
    default_chunk_overlap: int
    graph_enabled: bool
    graph_provider: str
    graph_expansion_enabled: bool
    graph_expansion_depth: int
    graph_expansion_limit: int
    streaming_supported: bool = True


class GraphHealthResponse(BaseModel):
    enabled: bool
    provider: str
    healthy: bool
    message: str


class VectorStoreCollectionResponse(BaseModel):
    collection_name: str
    exists: bool
    vector_size: int | None
    expected_vector_size: int
    distance: str | None
    expected_distance: str
    matches_config: bool
    recreated: bool


class IngestionStepResponse(BaseModel):
    name: str
    state: str
    started_at: str | None
    completed_at: str | None
    duration_ms: int | None
    output: dict[str, object]
    error: str | None


class IngestionLogResponse(BaseModel):
    timestamp: str
    step: str
    level: str
    message: str
    duration_ms: int | None


class IngestionJobResponse(BaseModel):
    job_id: UUID
    filename: str
    content_type: str | None
    status: str
    created_at: str
    updated_at: str
    document_id: UUID | None
    error: str | None
    steps: list[IngestionStepResponse]
    logs: list[IngestionLogResponse]


class IngestionJobDeleteResponse(BaseModel):
    job_id: UUID
    deleted: bool


router = APIRouter(prefix="/api/admin", tags=["admin"])

def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


class ProfilesResponse(BaseModel):
    default_profile: str
    profiles: list[str]
    configs: dict[str, dict[str, object]]


@router.get("/profiles", response_model=ProfilesResponse)
async def list_profiles() -> ProfilesResponse:
    return ProfilesResponse(
        default_profile=DEFAULT_PROFILE,
        profiles=list(PROFILE_NAMES),
        configs={name: dict(config) for name, config in PROFILE_CONFIGS.items()},
    )


@router.get("/runtime-config", response_model=RuntimeConfigResponse)
async def runtime_config() -> RuntimeConfigResponse:
    return RuntimeConfigResponse(
        embedding_provider=settings.embedding_provider,
        embedding_base_url=settings.embedding_base_url,
        embedding_model=settings.embedding_model,
        embedding_dimension=settings.embedding_dimension,
        reranker_provider=settings.reranker_provider,
        reranker_base_url=settings.reranker_base_url,
        reranker_model=settings.reranker_model,
        llm_provider=settings.llm_provider,
        llm_base_url=settings.llm_base_url,
        llm_model=settings.llm_model,
        vector_collection_name=settings.qdrant_collection_name,
        auto_recreate_collection=settings.auto_recreate_collection,
        default_chunk_size=settings.default_chunk_size,
        default_chunk_overlap=settings.default_chunk_overlap,
        graph_enabled=settings.graph_enabled,
        graph_provider=settings.graph_provider,
        graph_expansion_enabled=settings.graph_expansion_enabled,
        graph_expansion_depth=settings.graph_expansion_depth,
        graph_expansion_limit=settings.graph_expansion_limit,
        streaming_supported=True,
    )


@router.get("/graph-health", response_model=GraphHealthResponse)
async def graph_health(
    neo4j_client: Annotated[Neo4jClient, Depends(get_neo4j_client)],
) -> GraphHealthResponse:
    if not settings.graph_enabled:
        return GraphHealthResponse(
            enabled=False,
            provider=settings.graph_provider,
            healthy=False,
            message="GraphRAG is disabled.",
        )
    try:
        await neo4j_client.verify_connectivity()
    except Exception as exc:
        return GraphHealthResponse(
            enabled=True,
            provider=settings.graph_provider,
            healthy=False,
            message=str(exc),
        )
    return GraphHealthResponse(
        enabled=True,
        provider=settings.graph_provider,
        healthy=True,
        message="Neo4j connectivity verified.",
    )


@router.post("/recreate-vector-store", response_model=VectorStoreCollectionResponse)
async def recreate_vector_store(
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> VectorStoreCollectionResponse:
    try:
        collection_info = await vector_store.recreate_collection()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to recreate vector store collection.",
        ) from exc

    return VectorStoreCollectionResponse(**asdict(collection_info))


@router.post(
    "/ingestion-jobs",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_ingestion_job(
    background_tasks: BackgroundTasks,
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    file: Annotated[UploadFile, File(...)],
) -> IngestionJobResponse:
    content = await file.read()
    filename = DocumentService._clean_filename(file.filename)
    duplicate_file = await repository.find_document_file_by_signature(
        filename=filename,
        file_size=len(content),
    )
    if duplicate_file is not None:
        duplicate_document = getattr(duplicate_file, "document", None)
        duplicate_id = getattr(duplicate_document, "id", None) or duplicate_file.document_id
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate file already exists for document {duplicate_id}.",
        )
    job = queue.enqueue_upload(
        filename=filename,
        content_type=file.content_type,
        content=content,
    )
    background_tasks.add_task(queue.run_job, job.job_id)
    return _to_ingestion_job_response(job)


@router.get("/ingestion-jobs", response_model=list[IngestionJobResponse])
async def list_ingestion_jobs(
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
) -> list[IngestionJobResponse]:
    return [_to_ingestion_job_response(job) for job in queue.list_jobs()]


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobResponse)
async def get_ingestion_job(
    job_id: UUID,
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
) -> IngestionJobResponse:
    job = queue.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion job not found.",
        )
    return _to_ingestion_job_response(job)


@router.delete("/ingestion-jobs/{job_id}", response_model=IngestionJobDeleteResponse)
async def delete_ingestion_job(
    job_id: UUID,
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
) -> IngestionJobDeleteResponse:
    deleted = queue.remove_job(job_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion job not found.",
        )
    return IngestionJobDeleteResponse(job_id=job_id, deleted=True)


def _to_ingestion_job_response(job: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        job_id=job.job_id,
        filename=job.filename,
        content_type=job.content_type,
        status=job.status,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
        document_id=job.document_id,
        error=job.error,
        steps=[
            IngestionStepResponse(
                name=step.name,
                state=step.state,
                started_at=step.started_at.isoformat() if step.started_at else None,
                completed_at=step.completed_at.isoformat() if step.completed_at else None,
                duration_ms=step.duration_ms,
                output=step.output,
                error=step.error,
            )
            for step in job.steps.values()
        ],
        logs=[
            IngestionLogResponse(
                timestamp=log.timestamp.isoformat(),
                step=log.step,
                level=log.level,
                message=log.message,
                duration_ms=log.duration_ms,
            )
            for log in job.logs
        ],
    )
