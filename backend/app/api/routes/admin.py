import inspect
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.documents import DocumentRepository
from app.repositories.ingestion_profiles import IngestionProfileRepository
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository
from app.services.document_profiles import DEFAULT_PROFILE
from app.services.document_service import DocumentService
from app.services.graph import Neo4jClient, get_neo4j_client
from app.services.heading_rule_engine import detect_headings, heading_rules_from_config
from app.services.ingestion_profiles import (
    get_profile_names,
    load_profile_configs,
    save_profile_config_to_database,
)
from app.services.ingestion_queue import IngestionJob, IngestionQueue, get_ingestion_queue
from app.services.rag_runtime_config import DEFAULT_RAG_CONFIG_NAME, default_rag_runtime_config, load_rag_runtime_config, save_rag_runtime_config
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
    chunk_enrichment_enabled: bool
    retrieval_enrichment_enabled: bool
    enrichment_force_on_reingest: bool
    enrichment_update_keyword_search_vector: bool
    chunk_enrichment_provider: str | None
    chunk_enrichment_base_url: str | None
    chunk_enrichment_model: str | None
    chunk_enrichment_max_chars: int
    chunk_enrichment_version: str
    embedding_enrichment_provider: str | None
    embedding_enrichment_base_url: str | None
    embedding_enrichment_model: str | None
    embedding_enrichment_max_chars: int
    embedding_enrichment_version: str
    reingest_enrichment_provider: str | None
    reingest_enrichment_base_url: str | None
    reingest_enrichment_model: str | None
    reingest_enrichment_max_chars: int
    reingest_enrichment_version: str
    enable_offline_enrichment: bool
    enable_query_enrichment: bool
    enable_context_expansion: bool
    enable_completeness_check: bool
    enable_second_retrieval: bool
    max_second_retrieval_rounds: int
    overview_top_k: int
    raw_top_k: int
    summary_top_k: int
    table_top_k: int
    max_context_chars: int
    enable_chunk_enrichment_at_ingest: bool
    enable_chunk_enrichment_at_retrieval: bool
    enable_knowledge_artifact_compilation: bool
    enable_llm_artifact_extraction: bool
    enable_artifact_first_retrieval: bool
    enable_chunk_fallback: bool
    enable_neighbor_expansion: bool
    enable_graph_expansion: bool
    artifact_confidence_threshold: float
    retrieval_token_budget: int
    max_artifacts: int
    max_chunks: int
    rag_runtime_config_source: str
    chunk_enrichment_enablement_source: str
    vector_collection_name: str
    artifact_vector_collection_name: str
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
    ingestion_profile: str = "auto"
    resolved_ingestion_profile: str | None = None
    steps: list[IngestionStepResponse]
    logs: list[IngestionLogResponse]


class IngestionJobDeleteResponse(BaseModel):
    job_id: UUID
    deleted: bool


class ReingestDocumentRequest(BaseModel):
    ingestion_profile: str = "auto"
    profile: str | None = None


router = APIRouter(prefix="/api/admin", tags=["admin"])

def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)

def get_ingestion_profile_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> IngestionProfileRepository:
    return IngestionProfileRepository(session)

def get_rag_runtime_config_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RagRuntimeConfigRepository:
    return RagRuntimeConfigRepository(session)


class ProfilesResponse(BaseModel):
    default_profile: str
    profiles: list[str]
    configs: dict[str, dict[str, object]]


class HeadingRuleTestRequest(BaseModel):
    profile: str
    sample_text: str
    config: dict[str, Any] | None = None


class HeadingRuleMatchResponse(BaseModel):
    start: int
    end: int
    level: int
    name: str
    label: str
    number: str
    title: str
    display_text: str
    boundary: bool


class HeadingRuleTestResponse(BaseModel):
    matches: list[HeadingRuleMatchResponse]


class ProfileUpdateRequest(BaseModel):
    config: dict[str, Any]

class RagRuntimeConfigResponse(BaseModel):
    config_name: str
    config: dict[str, object]
    source: str

class RagRuntimeConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


@router.get("/profiles", response_model=ProfilesResponse)
async def list_profiles(
    repository: Annotated[
        IngestionProfileRepository,
        Depends(get_ingestion_profile_repository),
    ],
) -> ProfilesResponse:
    configs = await load_profile_configs(repository)
    await repository.commit()
    return ProfilesResponse(
        default_profile=DEFAULT_PROFILE,
        profiles=list(get_profile_names()),
        configs={name: dict(config) for name, config in configs.items()},
    )


@router.put("/profiles/{profile_name}", response_model=ProfilesResponse)
async def update_profile_config(
    profile_name: str,
    payload: ProfileUpdateRequest,
    repository: Annotated[
        IngestionProfileRepository,
        Depends(get_ingestion_profile_repository),
    ],
) -> ProfilesResponse:
    try:
        await save_profile_config_to_database(repository, profile_name, payload.config)
        await repository.commit()
    except ValueError as exc:
        await repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    configs = await load_profile_configs(repository)
    await repository.commit()
    return ProfilesResponse(
        default_profile=DEFAULT_PROFILE,
        profiles=list(get_profile_names()),
        configs={name: dict(config) for name, config in configs.items()},
    )


@router.post("/profiles/test-heading-rules", response_model=HeadingRuleTestResponse)
async def test_heading_rules(
    payload: HeadingRuleTestRequest,
    repository: Annotated[
        IngestionProfileRepository,
        Depends(get_ingestion_profile_repository),
    ],
) -> HeadingRuleTestResponse:
    configs = await load_profile_configs(repository)
    await repository.commit()
    config = payload.config or configs.get(payload.profile) or {}
    rules = heading_rules_from_config(config)
    matches = detect_headings(payload.sample_text or "", rules)
    return HeadingRuleTestResponse(
        matches=[
            HeadingRuleMatchResponse(
                start=match.start,
                end=match.end,
                level=match.level,
                name=match.name,
                label=match.label,
                number=match.number,
                title=match.title,
                display_text=match.display_text,
                boundary=match.boundary,
            )
            for match in matches
        ]
    )


@router.get("/rag-runtime-config", response_model=RagRuntimeConfigResponse)
async def get_rag_runtime_config(
    repository: Annotated[
        RagRuntimeConfigRepository,
        Depends(get_rag_runtime_config_repository),
    ],
) -> RagRuntimeConfigResponse:
    try:
        config = await load_rag_runtime_config(repository)
        await repository.commit()
        source = "PostgreSQL"
    except Exception:
        await repository.rollback()
        config = default_rag_runtime_config()
        source = "settings_fallback"
    return RagRuntimeConfigResponse(
        config_name=DEFAULT_RAG_CONFIG_NAME,
        config=config.model_dump(),
        source=source,
    )

@router.put("/rag-runtime-config", response_model=RagRuntimeConfigResponse)
async def update_rag_runtime_config(
    payload: RagRuntimeConfigUpdateRequest,
    repository: Annotated[
        RagRuntimeConfigRepository,
        Depends(get_rag_runtime_config_repository),
    ],
) -> RagRuntimeConfigResponse:
    try:
        config = await save_rag_runtime_config(repository, payload.config)
        await repository.commit()
    except Exception as exc:
        await repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save RAG runtime config.",
        ) from exc
    return RagRuntimeConfigResponse(
        config_name=DEFAULT_RAG_CONFIG_NAME,
        config=config.model_dump(),
        source="PostgreSQL",
    )

@router.get("/runtime-config", response_model=RuntimeConfigResponse)
async def runtime_config(
    repository: Annotated[
        RagRuntimeConfigRepository,
        Depends(get_rag_runtime_config_repository),
    ],
) -> RuntimeConfigResponse:
    rag_config_source = "PostgreSQL"
    try:
        rag_config = await load_rag_runtime_config(repository)
        await repository.commit()
    except Exception:
        await repository.rollback()
        rag_config = default_rag_runtime_config()
        rag_config_source = "settings_fallback"
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
        chunk_enrichment_enabled=settings.chunk_enrichment_enabled,
        retrieval_enrichment_enabled=settings.retrieval_enrichment_enabled,
        enrichment_force_on_reingest=settings.enrichment_force_on_reingest,
        enrichment_update_keyword_search_vector=settings.enrichment_update_keyword_search_vector,
        chunk_enrichment_provider=settings.chunk_enrichment_provider,
        chunk_enrichment_base_url=settings.chunk_enrichment_base_url,
        chunk_enrichment_model=settings.chunk_enrichment_model,
        chunk_enrichment_max_chars=settings.chunk_enrichment_max_chars,
        chunk_enrichment_version=settings.chunk_enrichment_version,
        embedding_enrichment_provider=settings.embedding_enrichment_provider,
        embedding_enrichment_base_url=settings.embedding_enrichment_base_url,
        embedding_enrichment_model=settings.embedding_enrichment_model,
        embedding_enrichment_max_chars=settings.embedding_enrichment_max_chars,
        embedding_enrichment_version=settings.embedding_enrichment_version,
        reingest_enrichment_provider=settings.reingest_enrichment_provider,
        reingest_enrichment_base_url=settings.reingest_enrichment_base_url,
        reingest_enrichment_model=settings.reingest_enrichment_model,
        reingest_enrichment_max_chars=settings.reingest_enrichment_max_chars,
        reingest_enrichment_version=settings.reingest_enrichment_version,
        enable_offline_enrichment=settings.enable_offline_enrichment,
        enable_query_enrichment=settings.enable_query_enrichment,
        enable_context_expansion=settings.enable_context_expansion,
        enable_completeness_check=settings.enable_completeness_check,
        enable_second_retrieval=settings.enable_second_retrieval,
        max_second_retrieval_rounds=settings.max_second_retrieval_rounds,
        overview_top_k=settings.overview_top_k,
        raw_top_k=settings.raw_top_k,
        summary_top_k=settings.summary_top_k,
        table_top_k=settings.table_top_k,
        max_context_chars=settings.max_context_chars,
        enable_chunk_enrichment_at_ingest=rag_config.enable_chunk_enrichment_at_ingest,
        enable_chunk_enrichment_at_retrieval=rag_config.enable_chunk_enrichment_at_retrieval,
        enable_knowledge_artifact_compilation=rag_config.enable_knowledge_artifact_compilation,
        enable_llm_artifact_extraction=rag_config.enable_llm_artifact_extraction,
        enable_artifact_first_retrieval=rag_config.enable_artifact_first_retrieval,
        enable_chunk_fallback=rag_config.enable_chunk_fallback,
        enable_neighbor_expansion=rag_config.enable_neighbor_expansion,
        enable_graph_expansion=rag_config.enable_graph_expansion,
        artifact_confidence_threshold=rag_config.artifact_confidence_threshold,
        retrieval_token_budget=rag_config.retrieval_token_budget,
        max_artifacts=rag_config.max_artifacts,
        max_chunks=rag_config.max_chunks,
        rag_runtime_config_source=rag_config_source,
        chunk_enrichment_enablement_source=rag_config_source,
        vector_collection_name=settings.qdrant_collection_name,
        artifact_vector_collection_name=settings.qdrant_artifact_collection_name,
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


def _enqueue_upload_with_optional_profile(
    queue: IngestionQueue,
    *,
    filename: str,
    content_type: str | None,
    content: bytes,
    ingestion_profile: str,
    organization_id: UUID | None = None,
    access: dict[str, object] | None = None,
) -> IngestionJob:
    """Call queue.enqueue_upload while supporting older test fakes.

    Some tests and external integrations provide a lightweight queue object that
    has not yet added the optional ingestion_profile keyword.  The production
    queue accepts the keyword, but this compatibility layer keeps the route from
    failing when a fake queue only implements the original upload signature.
    """

    enqueue_upload = queue.enqueue_upload
    try:
        parameters = inspect.signature(enqueue_upload).parameters
    except (TypeError, ValueError):
        parameters = {}

    upload_kwargs: dict[str, Any] = {
        "filename": filename,
        "content_type": content_type,
        "content": content,
    }
    if "organization_id" in parameters:
        upload_kwargs["organization_id"] = organization_id
    if "ingestion_profile" in parameters:
        upload_kwargs["ingestion_profile"] = ingestion_profile
    if "access" in parameters:
        upload_kwargs["access"] = access
    return enqueue_upload(**upload_kwargs)


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
    ingestion_profile: Annotated[str, Form()] = "auto",
    organization_id: Annotated[UUID | None, Form()] = None,
    access_scope: Annotated[str | None, Form()] = None,
    classification: Annotated[str | None, Form()] = None,
    allowed_org_ids: Annotated[str | None, Form()] = None,
    allowed_org_paths: Annotated[str | None, Form()] = None,
    allowed_role_names: Annotated[str | None, Form()] = None,
    allowed_group_codes: Annotated[str | None, Form()] = None,
    allowed_user_ids: Annotated[str | None, Form()] = None,
    denied_org_ids: Annotated[str | None, Form()] = None,
    denied_org_paths: Annotated[str | None, Form()] = None,
    denied_role_names: Annotated[str | None, Form()] = None,
    denied_group_codes: Annotated[str | None, Form()] = None,
    denied_user_ids: Annotated[str | None, Form()] = None,
    inherit_permission: Annotated[bool | None, Form()] = None,
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
    job = _enqueue_upload_with_optional_profile(
        queue,
        filename=filename,
        content_type=file.content_type,
        content=content,
        ingestion_profile=ingestion_profile,
        organization_id=organization_id,
        access=_upload_access_payload(
            organization_id=organization_id,
            access_scope=access_scope,
            classification=classification,
            allowed_org_ids=allowed_org_ids,
            allowed_org_paths=allowed_org_paths,
            allowed_role_names=allowed_role_names,
            allowed_group_codes=allowed_group_codes,
            allowed_user_ids=allowed_user_ids,
            denied_org_ids=denied_org_ids,
            denied_org_paths=denied_org_paths,
            denied_role_names=denied_role_names,
            denied_group_codes=denied_group_codes,
            denied_user_ids=denied_user_ids,
            inherit_permission=inherit_permission,
        ),
    )
    background_tasks.add_task(queue.run_job, job.job_id)
    return _to_ingestion_job_response(job)

def _upload_access_payload(
    *,
    organization_id: UUID | None,
    access_scope: str | None,
    classification: str | None,
    allowed_org_ids: str | None,
    allowed_org_paths: str | None,
    allowed_role_names: str | None,
    allowed_group_codes: str | None,
    allowed_user_ids: str | None,
    denied_org_ids: str | None,
    denied_org_paths: str | None,
    denied_role_names: str | None,
    denied_group_codes: str | None,
    denied_user_ids: str | None,
    inherit_permission: bool | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "owner_org_id": str(organization_id) if organization_id else None,
        "scope": access_scope,
        "classification": classification,
        "allowed_org_ids": _split_form_list(allowed_org_ids),
        "allowed_org_paths": _split_form_list(allowed_org_paths),
        "allowed_role_names": _split_form_list(allowed_role_names),
        "allowed_group_codes": _split_form_list(allowed_group_codes),
        "allowed_user_ids": _split_form_list(allowed_user_ids),
        "denied_org_ids": _split_form_list(denied_org_ids),
        "denied_org_paths": _split_form_list(denied_org_paths),
        "denied_role_names": _split_form_list(denied_role_names),
        "denied_group_codes": _split_form_list(denied_group_codes),
        "denied_user_ids": _split_form_list(denied_user_ids),
    }
    if inherit_permission is not None:
        payload["inherit_permission"] = inherit_permission
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != []
    }

def _split_form_list(value: str | None) -> list[str]:
    if value is None:
        return []
    normalized = value.replace(";", ",").replace("|", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


@router.post(
    "/documents/{document_id}/reingest",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reingest_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    payload: ReingestDocumentRequest | None = None,
) -> IngestionJobResponse:
    document = await repository.get_document(document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    document_file = await repository.get_primary_document_file(document_id)
    filename = (
        getattr(document_file, "filename", None)
        or getattr(document, "title", None)
        or str(document_id)
    )
    job = queue.enqueue_document_reingestion(
        document_id=document_id,
        filename=filename,
        content_type=getattr(document_file, "mime_type", None),
        ingestion_profile=(
            (payload.profile or payload.ingestion_profile) if payload else "auto"
        ),
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
        ingestion_profile=job.ingestion_profile,
        resolved_ingestion_profile=job.resolved_ingestion_profile,
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
