import asyncio
import logging
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.document_logs import DocumentLogRepository
from app.repositories.documents import DocumentRepository
from app.repositories.graph import GraphRepository
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.schemas.documents import (
    DocumentAccessDecisionResponse,
    DocumentAccessResponse,
    DocumentAccessTestRequest,
    DocumentAccessUpdateRequest,
    DocumentBatchUploadItem,
    DocumentBatchUploadResponse,
    DocumentChunkDetailResponse,
    DocumentChunkEnrichmentRequest,
    DocumentChunkEnrichmentResponse,
    DocumentChunkRequest,
    DocumentChunkResponse,
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentFileResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentParseResponse,
    DocumentPipelineLogResponse,
    DocumentUploadResponse,
    DocumentVectorIndexRequest,
    DocumentVectorIndexResponse,
    DofficeIngestRequest,
    DofficeIngestResponse,
    GraphDocumentStatusResponse,
    GraphExtractionLogResponse,
    GraphIndexRequest,
    GraphIndexResponse,
)
from app.services.security.security_access_control import (
    AccessAction,
    build_resource_context,
    build_subject_context,
    can_access_resource,
    normalize_document_access_metadata,
)
from app.services.chunkers.chunker_chunk_enrichment_service import (
    ChunkEnrichmentChunksNotFoundError,
    ChunkEnrichmentDocumentNotFoundError,
    ChunkEnrichmentError,
    ChunkEnrichmentService,
    ChunkEnrichmentStatusError,
)
from app.services.chunkers.chunker_chunking_service import (
    ChunkingService,
    DocumentChunkingError,
    DocumentChunkStatusError,
    EmptyParsedTextError,
)
from app.services.chunkers.chunker_chunking_service import (
    DocumentNotFoundError as ChunkDocumentNotFoundError,
)
from app.services.documents.document_parser_service import (
    DocumentFileNotFoundError,
    DocumentNotFoundError,
    DocumentParserService,
    DocumentParseStatusError,
    DocumentParsingError,
    UnsupportedDocumentParserError,
)
from app.services.documents.document_profiles import resolve_profile
from app.services.documents.document_service import (
    DocumentService,
    DocumentUploadError,
    DuplicateDocumentUploadError,
    EmptyDocumentUploadError,
    UnsupportedDocumentTypeError,
)
from app.services.document_sources import (
    DofficeDocumentNotFoundError,
    DofficeElasticsearchSource,
    DofficeSourceError,
)
from app.services.ingestion.ingestion_doffice_ingestion_service import (
    DofficeIngestionError,
    DofficeIngestionService,
    DofficeIngestOptions,
    EmptyDofficeDocumentError,
)
from app.services.retrieval.retrieval_elasticsearch_keyword_search import (
    ElasticsearchKeywordStore,
    get_elasticsearch_keyword_store,
)
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.graph import (
    GraphDocumentChunksMissingError,
    GraphIndexingDisabledError,
    GraphIndexingError,
    GraphIndexingService,
    GraphMergeService,
    Neo4jClient,
    get_neo4j_client,
)
from app.services.graph.extractors.extractor_factory import build_graph_extractor
from app.services.ingestion.ingestion_queue import IngestionJob, IngestionQueue, get_ingestion_queue
from app.services.knowledge.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.llm_gateway import LLMGateway, build_llm_gateway_or_error, get_llm_gateway
from app.services.security.security_permissions import (
    can_assign_upload_organization,
    can_manage_document,
    can_upload_document,
    can_upload_to_knowledge_base,
    can_view_document,
)
from app.services.documents.document_storage import StorageClient, get_storage_client
from app.services.vector.vector_indexing_service import (
    DocumentChunksNotFoundError,
    DocumentVectorIndexStatusError,
    VectorIndexingError,
    VectorIndexingService,
)
from app.services.vector.vector_indexing_service import (
    DocumentNotFoundError as VectorDocumentNotFoundError,
)
from app.services.vector.vector_store import (
    QdrantVectorStore,
    get_artifact_vector_store,
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
    get_vector_store,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])
DOCUMENT_DETAIL_PREVIEW_LIMIT = 50_000


def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


def get_document_log_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentLogRepository:
    return DocumentLogRepository(session)


def get_graph_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GraphRepository:
    return GraphRepository(session)

def get_knowledge_base_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(session)


def get_knowledge_artifact_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeArtifactRepository:
    return KnowledgeArtifactRepository(session)


def get_document_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> DocumentService:
    return DocumentService(repository=repository, storage=storage)


def get_document_parser_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> DocumentParserService:
    return DocumentParserService(repository=repository, storage=storage)


def get_chunking_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> ChunkingService:
    return ChunkingService(repository=repository, storage=storage)


def get_chunk_enrichment_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    llm_provider: Annotated[LLMGateway, Depends(get_llm_gateway)],
) -> ChunkEnrichmentService:
    return ChunkEnrichmentService(repository=repository, llm_provider=llm_provider)


def get_vector_indexing_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    llm_gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> VectorIndexingService:
    return VectorIndexingService(
        repository=repository,
        llm_gateway=llm_gateway,
        vector_store=vector_store,
        sparse_embedding_provider=get_sparse_embedding_provider(),
        keyword_index_store=(
            get_elasticsearch_keyword_store()
            if settings.elasticsearch_enabled
            else None
        ),
    )


def get_knowledge_artifact_indexing_service(
    repository: Annotated[
        KnowledgeArtifactRepository,
        Depends(get_knowledge_artifact_repository),
    ],
    llm_gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
) -> KnowledgeArtifactIndexingService:
    return KnowledgeArtifactIndexingService(
        repository=repository,
        llm_gateway=llm_gateway,
        vector_store=get_artifact_vector_store(),
        sparse_embedding_provider=get_sparse_embedding_provider(),
    )


def get_doffice_source() -> DofficeElasticsearchSource:
    return DofficeElasticsearchSource()

def get_doffice_ingestion_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    source: Annotated[DofficeElasticsearchSource, Depends(get_doffice_source)],
    chunking_service: Annotated[ChunkingService, Depends(get_chunking_service)],
    vector_indexing_service: Annotated[
        VectorIndexingService,
        Depends(get_vector_indexing_service),
    ],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
    enrichment_service: Annotated[
        ChunkEnrichmentService,
        Depends(get_chunk_enrichment_service),
    ],
    knowledge_artifact_repository: Annotated[
        KnowledgeArtifactRepository,
        Depends(get_knowledge_artifact_repository),
    ],
    artifact_indexing_service: Annotated[
        KnowledgeArtifactIndexingService,
        Depends(get_knowledge_artifact_indexing_service),
    ],
    llm_gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
) -> DofficeIngestionService:
    return DofficeIngestionService(
        repository=repository,
        source=source,
        chunking_service=chunking_service,
        vector_indexing_service=vector_indexing_service,
        vector_store=vector_store,
        enrichment_service=enrichment_service,
        knowledge_artifact_repository=knowledge_artifact_repository,
        artifact_indexing_service=artifact_indexing_service,
        keyword_index_store=(
            get_elasticsearch_keyword_store()
            if settings.elasticsearch_enabled
            else None
        ),
        document_index_store=(
            DocumentIndexStore() if settings.two_stage_retrieval_enabled else None
        ),
        llm_gateway=llm_gateway,
    )

def get_graph_indexing_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    graph_repository: Annotated[GraphRepository, Depends(get_graph_repository)],
    llm_provider: Annotated[LLMGateway, Depends(get_llm_gateway)],
    neo4j_client: Annotated[Neo4jClient, Depends(get_neo4j_client)],
) -> GraphIndexingService:
    extractor = build_graph_extractor(llm_provider=llm_provider)
    return GraphIndexingService(
        document_repository=repository,
        graph_repository=graph_repository,
        neo4j_client=neo4j_client,
        extractor=extractor,
        merge_service=GraphMergeService(),
    )


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tải lên một văn bản mới",
)
async def upload_document(
    service: Annotated[DocumentService, Depends(get_document_service)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(...)],
    visibility: Annotated[str, Form()] = "organization",
    organization_id: Annotated[UUID | None, Form()] = None,
    knowledge_base_id: Annotated[UUID | None, Form()] = None,
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
) -> DocumentUploadResponse:
    if not can_upload_document(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload is not allowed.")
    if visibility not in {"private", "organization", "subtree", "global"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid visibility.",
        )

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    knowledge_base, target_organization_id = await _resolve_upload_knowledge_base(
        knowledge_base_repository=knowledge_base_repository,
        current_user=current_user,
        requested_organization_id=organization_id,
        requested_knowledge_base_id=knowledge_base_id,
        descendant_ids=descendant_ids,
    )
    if not can_assign_upload_organization(
        current_user,
        target_organization_id,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload to the selected organization.",
        )
    access = _upload_access_payload(
        target_organization_id=target_organization_id,
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
    )

    try:
        response = await service.upload_document(
            file,
            uploaded_by_user_id=current_user.id,
            organization_id=target_organization_id,
            knowledge_base_id=knowledge_base.id,
            visibility=visibility,
            access=access,
        )
        await log_repository.create_pipeline_log(
            document_id=response.document_id,
            user_id=current_user.id,
            organization_id=target_organization_id,
            action="upload",
            status="success",
            message=f"Uploaded {response.filename}.",
            metadata={
                "storage_path": response.storage_path,
                "knowledge_base_id": str(knowledge_base.id),
                "access": access,
            },
        )
        await repository.commit()
        return response
    except EmptyDocumentUploadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except DuplicateDocumentUploadError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DocumentUploadError as exc:
        await log_repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/upload-batch",
    response_model=DocumentBatchUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tải lên nhiều văn bản cùng lúc (theo lô)",
)
async def upload_documents_batch(
    service: Annotated[DocumentService, Depends(get_document_service)],
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    files: Annotated[list[UploadFile], File(...)],
    visibility: Annotated[str, Form()] = "organization",
    organization_id: Annotated[UUID | None, Form()] = None,
    knowledge_base_id: Annotated[UUID | None, Form()] = None,
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
) -> DocumentBatchUploadResponse:
    if not can_upload_document(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload is not allowed.")
    if visibility not in {"private", "organization", "subtree", "global"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid visibility.",
        )

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    knowledge_base, target_organization_id = await _resolve_upload_knowledge_base(
        knowledge_base_repository=knowledge_base_repository,
        current_user=current_user,
        requested_organization_id=organization_id,
        requested_knowledge_base_id=knowledge_base_id,
        descendant_ids=descendant_ids,
    )
    if not can_assign_upload_organization(
        current_user,
        target_organization_id,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload to the selected organization.",
        )
    access = _upload_access_payload(
        target_organization_id=target_organization_id,
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
    )

    results: list[DocumentBatchUploadItem] = []
    for upload_file in files:
        try:
            response = await service.upload_document(
                upload_file,
                uploaded_by_user_id=current_user.id,
                organization_id=target_organization_id,
                knowledge_base_id=knowledge_base.id,
                visibility=visibility,
                access=access,
            )
            await log_repository.create_pipeline_log(
                document_id=response.document_id,
                user_id=current_user.id,
                organization_id=target_organization_id,
                action="upload",
                status="success",
                message=f"Uploaded {response.filename}.",
                metadata={
                    "storage_path": response.storage_path,
                    "knowledge_base_id": str(knowledge_base.id),
                    "access": access,
                },
            )
            await repository.commit()
            results.append(
                DocumentBatchUploadItem(
                    filename=response.filename,
                    document_id=response.document_id,
                    status=response.status,
                    success=True,
                    error=None,
                )
            )
        except (
            EmptyDocumentUploadError,
            UnsupportedDocumentTypeError,
            DuplicateDocumentUploadError,
            DocumentUploadError,
        ) as exc:
            await log_repository.rollback()
            results.append(
                DocumentBatchUploadItem(
                    filename=upload_file.filename or "unknown",
                    document_id=None,
                    status="failed",
                    success=False,
                    error=str(exc),
                )
            )

    success_count = sum(1 for item in results if item.success)
    return DocumentBatchUploadResponse(
        items=results,
        success_count=success_count,
        failed_count=len(results) - success_count,
    )


def _to_ingestion_job_payload(job: IngestionJob) -> dict[str, object]:
    return {
        "job_id": str(job.job_id),
        "filename": job.filename,
        "content_type": job.content_type,
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "document_id": str(job.document_id) if job.document_id else None,
        "error": job.error,
        "ingestion_profile": job.ingestion_profile,
        "resolved_ingestion_profile": job.resolved_ingestion_profile,
        "steps": [
            {
                "name": step.name,
                "state": step.state,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "duration_ms": step.duration_ms,
                "output": step.output,
                "error": step.error,
            }
            for step in job.steps.values()
        ],
        "logs": [
            {
                "timestamp": log.timestamp.isoformat(),
                "step": log.step,
                "level": log.level,
                "message": log.message,
                "duration_ms": log.duration_ms,
            }
            for log in job.logs
        ],
    }


@router.post(
    "/doffice/ingest-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Tạo job nền nạp văn bản từ DOffice (bất đồng bộ)",
)
async def enqueue_doffice_ingestion_job(
    request: DofficeIngestRequest,
    background_tasks: BackgroundTasks,
    queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    if not can_upload_document(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload is not allowed.")

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    knowledge_base, target_organization_id = await _resolve_upload_knowledge_base(
        knowledge_base_repository=knowledge_base_repository,
        current_user=current_user,
        requested_organization_id=None,
        requested_knowledge_base_id=None,
        descendant_ids=descendant_ids,
    )
    if not can_assign_upload_organization(
        current_user,
        target_organization_id,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload to the selected organization.",
        )

    access = _upload_access_payload(
        target_organization_id=target_organization_id,
        access_scope=settings.access_default_scope,
        classification=settings.access_default_classification,
        allowed_org_ids=None,
        allowed_org_paths=None,
        allowed_role_names=None,
        allowed_group_codes=None,
        allowed_user_ids=None,
        denied_org_ids=None,
        denied_org_paths=None,
        denied_role_names=None,
        denied_group_codes=None,
        denied_user_ids=None,
        inherit_permission=True,
    )
    job = queue.enqueue_doffice_ingestion(
        id_vb=request.id_vb,
        force_refresh=request.force_refresh,
        enable_enrichment=request.enable_enrichment,
        uploaded_by_user_id=current_user.id,
        organization_id=target_organization_id,
        knowledge_base_id=knowledge_base.id,
        access=access,
    )
    background_tasks.add_task(queue.run_job, job.job_id)
    return _to_ingestion_job_payload(job)


@router.post(
    "/doffice/ingest",
    response_model=DofficeIngestResponse,
    summary="Nạp đồng bộ một văn bản từ DOffice (parse + chunk + index)",
)
async def ingest_doffice_document(
    request: DofficeIngestRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DofficeIngestionService, Depends(get_doffice_ingestion_service)],
    graph_service: Annotated[GraphIndexingService, Depends(get_graph_indexing_service)],
) -> DofficeIngestResponse:
    if not can_upload_document(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Upload is not allowed.")

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    knowledge_base, target_organization_id = await _resolve_upload_knowledge_base(
        knowledge_base_repository=knowledge_base_repository,
        current_user=current_user,
        requested_organization_id=None,
        requested_knowledge_base_id=None,
        descendant_ids=descendant_ids,
    )
    if not can_assign_upload_organization(
        current_user,
        target_organization_id,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload to the selected organization.",
        )

    access = _upload_access_payload(
        target_organization_id=target_organization_id,
        access_scope=settings.access_default_scope,
        classification=settings.access_default_classification,
        allowed_org_ids=None,
        allowed_org_paths=None,
        allowed_role_names=None,
        allowed_group_codes=None,
        allowed_user_ids=None,
        denied_org_ids=None,
        denied_org_paths=None,
        denied_role_names=None,
        denied_group_codes=None,
        denied_user_ids=None,
        inherit_permission=True,
    )

    try:
        response = await service.ingest_doffice_document(
            request.id_vb,
            DofficeIngestOptions(
                force_refresh=request.force_refresh,
                enable_enrichment=request.enable_enrichment,
            ),
            uploaded_by_user_id=current_user.id,
            organization_id=target_organization_id,
            knowledge_base_id=knowledge_base.id,
            access=access,
        )
        await log_repository.create_pipeline_log(
            document_id=response.document_id,
            user_id=current_user.id,
            organization_id=target_organization_id,
            action="parse",
            status="success",
            message=response.message,
            metadata={
                **response.model_dump(mode="json"),
                "pipeline_action": "doffice_ingest",
                "knowledge_base_id": str(knowledge_base.id),
                "force_refresh": request.force_refresh,
                "enable_enrichment": request.enable_enrichment,
            },
        )
        if (
            response.status == "success"
            and settings.graph_enabled
            and settings.graph_auto_index_on_ingest
        ):
            try:
                graph_response = await graph_service.index_document(
                    response.document_id,
                    GraphIndexRequest(
                        force_rebuild=True,
                        extractor_provider=settings.graph_extractor_provider,
                        max_entities_per_chunk=settings.graph_max_entities_per_chunk,
                        max_relations_per_chunk=settings.graph_max_relations_per_chunk,
                    ),
                )
                await log_repository.create_pipeline_log(
                    document_id=response.document_id,
                    user_id=current_user.id,
                    organization_id=target_organization_id,
                    action="index_graph",
                    status="success",
                    message="Auto-indexed document graph into Neo4j.",
                    metadata=graph_response.model_dump(mode="json"),
                )
            except Exception as graph_exc:
                await log_repository.create_pipeline_log(
                    document_id=response.document_id,
                    user_id=current_user.id,
                    organization_id=target_organization_id,
                    action="index_graph",
                    status="failed",
                    message=str(graph_exc),
                    metadata={"pipeline_action": "doffice_graph_auto_index"},
                )
        await log_repository.commit()
        return response
    except DofficeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DofficeSourceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except EmptyDofficeDocumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except DofficeIngestionError as exc:
        await repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

def _upload_access_payload(
    *,
    target_organization_id: UUID | None,
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
        "owner_org_id": str(target_organization_id) if target_organization_id else None,
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

async def _resolve_upload_knowledge_base(
    *,
    knowledge_base_repository: KnowledgeBaseRepository,
    current_user: User,
    requested_organization_id: UUID | None,
    requested_knowledge_base_id: UUID | None,
    descendant_ids: set[UUID],
) -> tuple[KnowledgeBase, UUID]:
    target_organization_id = requested_organization_id or current_user.organization_id
    if requested_knowledge_base_id is None:
        knowledge_base = await knowledge_base_repository.get_or_create_default(
            organization_id=target_organization_id,
            owner_user_id=current_user.id,
        )
    else:
        knowledge_base = await knowledge_base_repository.get_by_id(requested_knowledge_base_id)
        if knowledge_base is None or not knowledge_base.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found.",
            )
        knowledge_base_organization_id = getattr(knowledge_base, "organization_id", None)
        if (
            requested_organization_id is not None
            and knowledge_base_organization_id is not None
            and requested_organization_id != knowledge_base_organization_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Knowledge base does not belong to the selected organization.",
            )
        target_organization_id = (
            requested_organization_id
            or knowledge_base_organization_id
            or current_user.organization_id
        )

    if not can_upload_to_knowledge_base(
        current_user,
        knowledge_base,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Upload to the selected knowledge base is not allowed.",
        )
    return knowledge_base, target_organization_id


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="Lấy danh sách văn bản (phân trang, lọc, tìm kiếm)",
)
async def list_documents(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    organization_id: UUID | None = None,
    include_descendants: bool = False,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    uploaded_by: UUID | None = None,
    search: str | None = None,
    qdrant_indexed: Annotated[
        bool | None,
        Query(description="Lọc theo đã embed Qdrant: true=chỉ văn bản CÓ point, false=chưa có."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    user_descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    rows = await repository.list_documents(
        visible_document_ids=None,
        organization_ids=await _requested_organization_ids(
            auth_repository=auth_repository,
            organization_id=organization_id,
            include_descendants=include_descendants,
        ),
        status=status_filter,
        uploaded_by=uploaded_by,
        search=search,
        qdrant_indexed=qdrant_indexed,
        limit=None,
        offset=0,
    )
    visible_rows = [
        row
        for row in rows
        if can_view_document(
            current_user,
            row.document,
            descendant_organization_ids=user_descendant_ids,
        )
    ]
    paged_rows = visible_rows[offset : offset + limit]
    # Đếm số point Qdrant (collection chunks mới) cho từng văn bản của trang — hiển
    # thị "N point" ở FE. Best-effort: Qdrant lỗi -> None (FE hiện "—").
    chunks_store = get_doffice_chunks_vector_store()

    async def _qcount(document) -> int | None:
        try:
            return await chunks_store.count_points_for_document(document.id)
        except Exception:
            return None

    counts = await asyncio.gather(*[_qcount(row.document) for row in paged_rows])
    return DocumentListResponse(
        items=[
            _to_document_list_item_from_row(row, qdrant_point_count=count)
            for row, count in zip(paged_rows, counts, strict=False)
        ],
        total=len(visible_rows),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{document_id}/chunk",
    response_model=DocumentChunkResponse,
    summary="Chia nhỏ (chunk) văn bản đã parse",
)
async def chunk_document(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChunkingService, Depends(get_chunking_service)],
    request: Annotated[DocumentChunkRequest | None, Body()] = None,
) -> DocumentChunkResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    chunk_request = request or DocumentChunkRequest()
    try:
        response = await service.chunk_document(
            document_id,
            chunk_size=chunk_request.chunk_size,
            chunk_overlap=chunk_request.chunk_overlap,
            chunk_mode=chunk_request.chunk_mode,
            profile=chunk_request.profile,
        )
        await log_repository.create_pipeline_log(
            document_id=document_id,
            user_id=current_user.id,
            organization_id=getattr(document, "organization_id", None),
            action="chunk",
            status="success",
            message=f"Created {response.chunk_count} chunks.",
            metadata={"chunk_count": response.chunk_count},
        )
        await log_repository.commit()
        return response
    except ChunkDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DocumentChunkStatusError, EmptyParsedTextError) as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="chunk",
            message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DocumentChunkingError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="chunk",
            message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/{document_id}/enrich",
    response_model=DocumentChunkEnrichmentResponse,
    summary="Làm giàu (enrich) các chunk bằng LLM",
)
async def enrich_document_chunks(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Annotated[DocumentChunkEnrichmentRequest | None, Body()] = None,
) -> DocumentChunkEnrichmentResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    enrichment_request = request or DocumentChunkEnrichmentRequest()
    resolved_profile = _resolve_document_rag_profile(
        document,
        requested_profile=enrichment_request.profile,
    )
    enrichment_enabled = (
        enrichment_request.enabled
        if enrichment_request.enabled is not None
        else bool(settings.chunk_enrichment_enabled)
    )
    update_keyword_search_vector = (
        enrichment_request.update_keyword_search_vector
        if enrichment_request.update_keyword_search_vector is not None
        else bool(settings.enrichment_update_keyword_search_vector)
    )
    base_enrichment_provider = _optional_config_string(settings.chunk_enrichment_provider)
    base_enrichment_base_url = _optional_config_string(settings.chunk_enrichment_base_url)
    base_enrichment_model = _optional_config_string(settings.chunk_enrichment_model)
    base_enrichment_max_chars = _positive_int(
        settings.chunk_enrichment_max_chars,
        default=6000,
    )
    base_enrichment_version = _optional_config_string(settings.chunk_enrichment_version) or "v1"
    embedding_enrichment_provider = _optional_config_string(settings.embedding_enrichment_provider)
    embedding_enrichment_base_url = _optional_config_string(settings.embedding_enrichment_base_url)
    embedding_enrichment_model = _optional_config_string(settings.embedding_enrichment_model)
    embedding_enrichment_has_override = bool(
        embedding_enrichment_provider or embedding_enrichment_base_url or embedding_enrichment_model
    )
    chunk_enrichment_provider = embedding_enrichment_provider or base_enrichment_provider
    chunk_enrichment_base_url = embedding_enrichment_base_url or base_enrichment_base_url
    chunk_enrichment_model = embedding_enrichment_model or base_enrichment_model
    chunk_enrichment_max_chars = (
        _positive_int(settings.embedding_enrichment_max_chars, default=base_enrichment_max_chars)
        if embedding_enrichment_has_override
        else base_enrichment_max_chars
    )
    chunk_enrichment_version = (
        _optional_config_string(settings.embedding_enrichment_version)
        if embedding_enrichment_has_override
        else base_enrichment_version
    ) or base_enrichment_version
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=build_llm_gateway_or_error(
            provider=chunk_enrichment_provider,
            base_url=chunk_enrichment_base_url,
            model=chunk_enrichment_model,
        ),
        enabled=enrichment_enabled,
        provider=chunk_enrichment_provider,
        model=chunk_enrichment_model,
        max_chars=chunk_enrichment_max_chars,
        version=chunk_enrichment_version,
    )
    try:
        response = await service.enrich_document(
            document_id,
            force=enrichment_request.force,
            enabled=enrichment_enabled,
            update_keyword_search_vector=update_keyword_search_vector,
            provider=chunk_enrichment_provider,
            model=chunk_enrichment_model,
            max_chars=chunk_enrichment_max_chars,
            version=chunk_enrichment_version,
        )
        response.needs_reindex = bool(
            getattr(document, "status", None) == "indexed"
            and response.status in {"enriched", "partial"}
        )
        log_status = "success"
        if response.failed_count and response.enriched_count:
            log_status = "partial_success"
        elif response.failed_count:
            log_status = "failed"
        await log_repository.create_pipeline_log(
            document_id=document_id,
            user_id=current_user.id,
            organization_id=getattr(document, "organization_id", None),
            action="enrich",
            status=log_status,
            message=(
                f"Enriched {response.enriched_count} chunks; "
                f"failed {response.failed_count}; skipped {response.skipped_count}."
            ),
            metadata={
                "enriched_count": response.enriched_count,
                "failed_count": response.failed_count,
                "skipped_count": response.skipped_count,
                "status": response.status,
                "profile": resolved_profile,
                "enrichment_runtime_config_source": "backend/.env",
                "embedding_enrichment_enabled": enrichment_enabled,
                "enrichment_update_keyword_search_vector": update_keyword_search_vector,
                "chunk_enrichment_provider": chunk_enrichment_provider,
                "chunk_enrichment_base_url": chunk_enrichment_base_url,
                "chunk_enrichment_model": chunk_enrichment_model,
                "chunk_enrichment_max_chars": chunk_enrichment_max_chars,
                "chunk_enrichment_version": chunk_enrichment_version,
                "embedding_enrichment_provider": chunk_enrichment_provider,
                "embedding_enrichment_model": chunk_enrichment_model,
                "needs_reindex": response.needs_reindex,
            },
        )
        await log_repository.commit()
        return response
    except ChunkEnrichmentDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ChunkEnrichmentStatusError, ChunkEnrichmentChunksNotFoundError) as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="enrich",
            message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChunkEnrichmentError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="enrich",
            message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/{document_id}/index-vector",
    response_model=DocumentVectorIndexResponse,
    summary="Index vector các chunk vào Qdrant (và Elasticsearch nếu bật)",
)
async def index_document_vectors(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[VectorIndexingService, Depends(get_vector_indexing_service)],
    request: Annotated[DocumentVectorIndexRequest | None, Body()] = None,
) -> DocumentVectorIndexResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    index_request = request or DocumentVectorIndexRequest()
    resolved_profile = _resolve_document_rag_profile(
        document,
        requested_profile=index_request.profile,
    )
    use_enriched_content_for_embedding = (
        index_request.use_enriched_content_for_embedding
        if index_request.use_enriched_content_for_embedding is not None
        else bool(settings.chunk_enrichment_enabled)
    )
    try:
        response = await service.index_document(
            document_id,
            use_enriched_content_for_embedding=use_enriched_content_for_embedding,
        )
        await log_repository.create_pipeline_log(
            document_id=document_id,
            user_id=current_user.id,
            organization_id=getattr(document, "organization_id", None),
            action="index_vector",
            status="success",
            message=f"Indexed {response.indexed_chunk_count} chunks.",
            metadata={
                "indexed_chunk_count": response.indexed_chunk_count,
                "profile": resolved_profile,
                "use_enriched_content_for_embedding": use_enriched_content_for_embedding,
            },
        )
        await log_repository.commit()
        return response
    except VectorDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DocumentVectorIndexStatusError, DocumentChunksNotFoundError) as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="index_vector",
            message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except VectorIndexingError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="index_vector",
            message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/{document_id}/index-graph",
    response_model=GraphIndexResponse,
    summary="Index đồ thị tri thức của văn bản vào Neo4j",
)
async def index_document_graph(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GraphIndexingService, Depends(get_graph_indexing_service)],
    request: Annotated[GraphIndexRequest | None, Body()] = None,
) -> GraphIndexResponse:
    await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    graph_request = request or GraphIndexRequest()
    try:
        return await service.index_document(document_id, graph_request)
    except GraphIndexingDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GraphDocumentChunksMissingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GraphIndexingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/{document_id}/parse",
    response_model=DocumentParseResponse,
    summary="Parse (trích xuất văn bản) từ file đã tải lên",
)
async def parse_document(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[DocumentParserService, Depends(get_document_parser_service)],
) -> DocumentParseResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    try:
        response = await service.parse_document(document_id)
        await log_repository.create_pipeline_log(
            document_id=document_id,
            user_id=current_user.id,
            organization_id=getattr(document, "organization_id", None),
            action="parse",
            status="success",
            message=f"Parsed {response.character_count} characters.",
            metadata={"character_count": response.character_count},
        )
        await log_repository.commit()
        return response
    except (DocumentNotFoundError, DocumentFileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentParseStatusError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="parse",
            message=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UnsupportedDocumentParserError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="parse",
            message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except DocumentParsingError as exc:
        await _log_pipeline_failure(
            log_repository=log_repository,
            document_id=document_id,
            user=current_user,
            organization_id=getattr(document, "organization_id", None),
            action="parse",
            message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/{document_id}/access",
    response_model=DocumentAccessResponse,
    summary="Lấy cấu hình phân quyền truy cập của văn bản",
)
async def get_document_access(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentAccessResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    return DocumentAccessResponse(
        document_id=document.id,
        access=normalize_document_access_metadata(document),
    )

@router.patch(
    "/{document_id}/access",
    response_model=DocumentAccessResponse,
    summary="Cập nhật cấu hình phân quyền truy cập của văn bản",
)
async def update_document_access(
    document_id: UUID,
    request: DocumentAccessUpdateRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentAccessResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    document = await repository.update_document_access(
        document,
        request.model_dump(exclude_none=True),
    )
    await log_repository.create_access_log(
        document_id=document.id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        action="manage_acl",
        metadata={"decision": "allowed"},
    )
    await log_repository.commit()
    return DocumentAccessResponse(
        document_id=document.id,
        access=normalize_document_access_metadata(document),
    )

@router.post(
    "/{document_id}/access/test",
    response_model=DocumentAccessDecisionResponse,
    summary="Kiểm tra quyền truy cập văn bản của một người dùng",
)
async def test_document_access(
    document_id: UUID,
    request: DocumentAccessTestRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentAccessDecisionResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    target_user = await auth_repository.get_user_by_id(request.user_id)
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    try:
        action = AccessAction(request.action)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid access action.",
        ) from exc
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        target_user.organization_id
    )
    decision = can_access_resource(
        build_subject_context(target_user, descendant_organization_ids=descendant_ids),
        build_resource_context(document),
        action,
    )
    await log_repository.create_access_log(
        document_id=document.id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        action="manage_acl",
        metadata={
            "tested_user_id": str(target_user.id),
            "tested_action": action.value,
            "decision": "allowed" if decision.allowed else "denied",
            "reason": decision.reason,
        },
    )
    await log_repository.commit()
    return DocumentAccessDecisionResponse(**decision.model_dump())

@router.get(
    "/{document_id}/chunks/qdrant",
    summary="Lấy payload (metadata) các point Qdrant của văn bản",
)
async def get_document_qdrant_payloads(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, object]:
    """Trả payload (metadata) các point Qdrant của 1 văn bản (collection chunks mới).

    FE dùng để hiển thị metadata Qdrant theo từng chunk (nút con mắt): map theo
    ``chunk_index`` (hoặc ``chunk_id``) với chunk lấy từ PG.
    """
    document = await repository.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_view_document(current_user, document, descendant_organization_ids=descendant_ids):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Document is not visible.")
    store = get_doffice_chunks_vector_store()
    try:
        points = await store.retrieve_payloads_for_document(
            document_id, tenant_id=getattr(document, "organization_id", None)
        )
    except Exception as exc:  # noqa: BLE001 - lỗi Qdrant trả 502 cho FE
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Qdrant error: {exc}"
        ) from exc
    return {
        "document_id": str(document_id),
        "collection": store.collection_name,
        "count": len(points),
        "points": points,
    }


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Lấy chi tiết văn bản (file, chunk, log, trạng thái graph)",
)
async def get_document_detail(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    graph_repository: Annotated[GraphRepository, Depends(get_graph_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentDetailResponse:
    document = await repository.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_view_document(current_user, document, descendant_organization_ids=descendant_ids):
        await log_repository.create_access_log(
            document_id=document.id,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            action="denied_access",
            metadata={"attempted_action": "open_document"},
        )
        await log_repository.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document is not visible.",
        )

    await log_repository.create_access_log(
        document_id=document.id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        action="view",
        metadata={"canonical_action": "open_document"},
    )
    pipeline_logs = await log_repository.latest_pipeline_logs(document_id=document.id)
    pipeline_logs_count = await log_repository.count_pipeline_logs(document_id=document.id)
    access_summary = await log_repository.access_log_summary(document_id=document.id)
    graph_status = await graph_repository.get_document_status(document.id)
    graph_logs = await graph_repository.list_extraction_logs(document_id=document.id)
    chunks = await repository.list_chunks_for_document(document.id)
    chunk_count = await repository.count_chunks_for_document(document.id)
    await log_repository.commit()

    item = _to_document_list_item_from_values(
        document=document,
        filename=document.files[0].filename if document.files else None,
        chunk_count=chunk_count,
        parsed_character_count=len(document.parsed_text or ""),
        vector_indexed_count=_vector_indexed_count(
            pipeline_logs=pipeline_logs,
            chunk_count=chunk_count,
            document_status=document.status,
        ),
        pipeline_logs_count=pipeline_logs_count,
        graph_indexed=graph_status.graph_indexed if graph_status is not None else False,
    )
    return DocumentDetailResponse(
        **item.model_dump(),
        preview_text=(document.parsed_text or "")[:DOCUMENT_DETAIL_PREVIEW_LIMIT] or None,
        files=[
            DocumentFileResponse(
                id=str(file.id),
                filename=file.filename,
                mime_type=file.mime_type,
                storage_path=file.storage_path,
                file_size=file.file_size,
                created_at=file.created_at.isoformat(),
                download_url=_document_file_download_url(document.id, file.id),
            )
            for file in document.files
        ],
        chunks=[
            DocumentChunkDetailResponse(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                metadata=chunk.chunk_metadata,
                created_at=chunk.created_at,
            )
            for chunk in chunks
        ],
        pipeline_logs=[
            DocumentPipelineLogResponse(
                action=log.action,
                status=log.status,
                message=log.message,
                metadata=log.log_metadata,
                created_at=log.created_at,
            )
            for log in pipeline_logs
        ],
        access_logs_summary=access_summary,
        latest_retrieval_logs=[],
        graph_status=(
            GraphDocumentStatusResponse(
                graph_indexed=graph_status.graph_indexed,
                chunks_processed=graph_status.chunks_processed,
                entity_count=graph_status.entity_count,
                relation_count=graph_status.relation_count,
                last_indexed_at=graph_status.last_indexed_at,
                error_message=graph_status.error_message,
            )
            if graph_status is not None
            else None
        ),
        graph_extraction_logs=[
            GraphExtractionLogResponse(
                status=log.status,
                entity_count=log.entity_count,
                relation_count=log.relation_count,
                merged_entity_count=log.merged_entity_count,
                merged_relation_count=log.merged_relation_count,
                error_message=log.error_message,
                metadata=log.log_metadata,
                created_at=log.created_at,
            )
            for log in graph_logs
        ],
    )


@router.get(
    "/{document_id}/files/{file_id}/download",
    summary="Tải xuống file gốc của văn bản",
)
async def download_document_file(
    document_id: UUID,
    file_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
) -> StreamingResponse:
    document = await repository.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_view_document(current_user, document, descendant_organization_ids=descendant_ids):
        await log_repository.create_access_log(
            document_id=document.id,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            action="denied_access",
            metadata={"attempted_action": "download", "file_id": str(file_id)},
        )
        await log_repository.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document is not visible.",
        )

    document_file = next((file for file in document.files if file.id == file_id), None)
    if document_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document file not found.",
        )

    try:
        content = await storage.get_file(object_name=document_file.storage_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load document file.",
        ) from exc

    await log_repository.create_access_log(
        document_id=document.id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        action="download",
        metadata={"file_id": str(document_file.id), "filename": document_file.filename},
    )
    await log_repository.commit()

    return StreamingResponse(
        iter([content]),
        media_type=document_file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _download_content_disposition(document_file.filename)},
    )


@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    summary="Xóa văn bản khỏi PostgreSQL + Qdrant + Elasticsearch + storage",
)
async def delete_document(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
    artifact_vector_store: Annotated[QdrantVectorStore, Depends(get_artifact_vector_store)],
    keyword_store: Annotated[ElasticsearchKeywordStore, Depends(get_elasticsearch_keyword_store)],
) -> DocumentDeleteResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    storage_paths = [file.storage_path for file in document.files]
    storage_paths.extend(_document_artifact_paths(document))
    storage_paths = list(dict.fromkeys(storage_paths))

    try:
        # Xóa toàn bộ vector của văn bản khỏi Qdrant (collection chunk + artifact)
        # và khỏi Elasticsearch keyword store. Lọc theo document_id (UUID duy nhất
        # toàn cục); KHÔNG lọc thêm tenant_id, vì dữ liệu index từ phiên bản cũ có
        # thể không gắn tenant_id và sẽ bị sót nếu lọc thêm điều kiện này.
        await vector_store.delete_points_for_document(document_id)
        await artifact_vector_store.delete_points_for_document(document_id)
        await keyword_store.delete_points_for_document(document_id)
        # Thiết kế DOffice 3-DB: xóa thêm 2 collection Qdrant mới + ES BM25 doc-level.
        # BEST-EFFORT: store doffice là singleton thật (không qua Depends), nếu transient
        # lỗi/không cấu hình thì chỉ cảnh báo — không làm hỏng việc xóa PG + store cũ.
        _id_vb = _optional_string((getattr(document, "document_metadata", None) or {}).get("id_vb"))
        try:
            await get_doffice_chunks_vector_store().delete_points_for_document(document_id)
            await get_doffice_docmeta_vector_store().delete_points_for_document(document_id)
            if _id_vb:
                from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore

                await DofficeBm25DocumentStore().delete_by_id_vb(_id_vb)
        except Exception:
            logging.getLogger(__name__).warning(
                "Xóa dấu vết DOffice (Qdrant/ES mới) thất bại document=%s id_vb=%s", document_id, _id_vb,
                exc_info=True,
            )
        for storage_path in storage_paths:
            if storage_path:
                await storage.delete_file(object_name=storage_path)
        await repository.delete_document(document)
        await repository.commit()
    except Exception as exc:
        await repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document data from vector store, storage, and database.",
        ) from exc

    return DocumentDeleteResponse(
        document_id=document_id,
        deleted=True,
        deleted_files=len(storage_paths),
        vector_points_deleted=True,
    )


def _document_artifact_paths(document: object) -> list[str]:
    metadata = dict(getattr(document, "document_metadata", None) or {})
    candidates = [
        metadata.get("artifact_paths"),
        dict(metadata.get("parsed_metadata") or {}).get("artifact_paths"),
    ]
    paths: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        paths.extend(
            str(value)
            for value in candidate.values()
            if isinstance(value, str) and value.strip()
        )
    return paths


async def _require_manageable_document(
    *,
    document_id: UUID,
    repository: DocumentRepository,
    auth_repository: AuthRepository,
    current_user: User,
):
    document = await repository.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_manage_document(current_user, document, descendant_organization_ids=descendant_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document is not manageable.",
        )
    return document


async def _requested_organization_ids(
    *,
    auth_repository: AuthRepository,
    organization_id: UUID | None,
    include_descendants: bool,
) -> set[UUID] | None:
    if organization_id is None:
        return None
    if include_descendants:
        return await auth_repository.get_descendant_organization_ids(organization_id)
    return {organization_id}


async def _log_pipeline_failure(
    *,
    log_repository: DocumentLogRepository,
    document_id: UUID,
    user: User,
    organization_id: UUID | None,
    action: str,
    message: str,
) -> None:
    await log_repository.create_pipeline_log(
        document_id=document_id,
        user_id=user.id,
        organization_id=organization_id,
        action=action,
        status="failed",
        message=message,
    )
    await log_repository.commit()


def _resolve_document_rag_profile(document, *, requested_profile: str | None) -> str:
    requested = str(
        requested_profile or getattr(document, "document_profile", None) or "auto"
    ).strip().lower()
    return resolve_profile(
        requested or "auto",
        text=getattr(document, "parsed_text", None),
        filename=getattr(document, "title", None),
        content_type=None,
    )

def _int_config_value(config: dict[str, object], key: str, *, default: int) -> int:
    try:
        parsed = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default

def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default

def _optional_config_string(value: object) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean or None

def _to_document_list_item_from_document(
    document, filename: str | None, chunk_count: int
) -> DocumentListItem:
    return _to_document_list_item_from_values(
        document=document,
        filename=filename,
        chunk_count=chunk_count,
        parsed_character_count=len(getattr(document, "parsed_text", "") or ""),
        vector_indexed_count=(
            chunk_count if getattr(document, "status", None) == "indexed" else None
        ),
        pipeline_logs_count=0,
        graph_indexed=False,
    )


def _document_file_download_url(document_id: UUID, file_id: UUID) -> str:
    return f"/api/documents/{document_id}/files/{file_id}/download"


def _download_content_disposition(filename: str) -> str:
    encoded_filename = quote(filename)
    return f"attachment; filename*=UTF-8''{encoded_filename}"


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean or None


def _to_document_list_item_from_row(row, *, qdrant_point_count: int | None = None) -> DocumentListItem:
    return _to_document_list_item_from_values(
        document=row.document,
        filename=row.filename,
        chunk_count=row.chunk_count,
        parsed_character_count=row.parsed_character_count,
        vector_indexed_count=row.vector_indexed_count,
        pipeline_logs_count=row.pipeline_logs_count,
        graph_indexed=row.graph_indexed,
        qdrant_point_count=qdrant_point_count,
    )


def _to_document_list_item_from_values(
    *,
    document,
    filename: str | None,
    chunk_count: int,
    parsed_character_count: int,
    vector_indexed_count: int | None,
    pipeline_logs_count: int,
    graph_indexed: bool,
    qdrant_point_count: int | None = None,
) -> DocumentListItem:
    _meta = getattr(document, "document_metadata", None) or {}
    effective_vector_indexed_count = vector_indexed_count
    if effective_vector_indexed_count is None and getattr(document, "status", None) == "indexed":
        effective_vector_indexed_count = chunk_count

    return DocumentListItem(
        document_id=document.id,
        title=document.title,
        status=document.status,
        source_type=getattr(document, "source_type", None) or "unknown",
        filename=filename,
        organization=(
            {
                "id": document.organization.id,
                "ma_dviqly": document.organization.ma_dviqly,
                "ten_dviqly": document.organization.ten_dviqly,
                "dvi_level": document.organization.dvi_level,
            }
            if document.organization
            else None
        ),
        knowledge_base=(
            {
                "id": document.knowledge_base.id,
                "name": document.knowledge_base.name,
                "visibility": document.knowledge_base.visibility,
                "organization": (
                    {
                        "id": document.knowledge_base.organization.id,
                        "ma_dviqly": document.knowledge_base.organization.ma_dviqly,
                        "ten_dviqly": document.knowledge_base.organization.ten_dviqly,
                        "dvi_level": document.knowledge_base.organization.dvi_level,
                    }
                    if getattr(document.knowledge_base, "organization", None)
                    else None
                ),
                "owner": (
                    {
                        "id": document.knowledge_base.owner.id,
                        "username": document.knowledge_base.owner.username,
                        "full_name": document.knowledge_base.owner.full_name,
                    }
                    if getattr(document.knowledge_base, "owner", None)
                    else None
                ),
            }
            if getattr(document, "knowledge_base", None)
            else None
        ),
        uploaded_by=(
            {
                "id": document.uploaded_by.id,
                "username": document.uploaded_by.username,
                "full_name": document.uploaded_by.full_name,
            }
            if document.uploaded_by
            else None
        ),
        visibility=document.visibility,
        parsed_character_count=parsed_character_count,
        created_at=document.created_at,
        updated_at=document.updated_at,
        chunk_count=chunk_count,
        vector_indexed_count=effective_vector_indexed_count,
        pipeline_logs_count=pipeline_logs_count,
        graph_indexed=graph_indexed,
        id_vb=_optional_string(_meta.get("id_vb")),
        ky_hieu=_optional_string(_meta.get("ky_hieu")),
        qdrant_point_count=qdrant_point_count,
    )


def _vector_indexed_count(*, pipeline_logs, chunk_count: int, document_status: str) -> int | None:
    for log in pipeline_logs:
        metadata = log.log_metadata or {}
        if log.action != "index_vector" or log.status != "success":
            continue
        value = metadata.get("indexed_chunk_count")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    if document_status == "indexed":
        return chunk_count
    return None
