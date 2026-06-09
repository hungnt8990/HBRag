from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.document_logs import DocumentLogRepository
from app.repositories.documents import DocumentRepository
from app.repositories.graph import GraphRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.schemas.documents import (
    DocumentBatchUploadItem,
    DocumentBatchUploadResponse,
    DocumentChunkDetailResponse,
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
    DocumentVectorIndexResponse,
    GraphDocumentStatusResponse,
    GraphExtractionLogResponse,
    GraphIndexRequest,
    GraphIndexResponse,
)
from app.services.chunking_service import (
    ChunkingService,
    DocumentChunkingError,
    DocumentChunkStatusError,
    EmptyParsedTextError,
)
from app.services.chunking_service import (
    DocumentNotFoundError as ChunkDocumentNotFoundError,
)
from app.services.document_parser_service import (
    DocumentFileNotFoundError,
    DocumentNotFoundError,
    DocumentParserService,
    DocumentParseStatusError,
    DocumentParsingError,
    UnsupportedDocumentParserError,
)
from app.services.document_service import (
    DocumentService,
    DocumentUploadError,
    DuplicateDocumentUploadError,
    EmptyDocumentUploadError,
    UnsupportedDocumentTypeError,
)
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.factory import get_embedding_provider
from app.services.graph import (
    GraphDocumentChunksMissingError,
    GraphIndexingDisabledError,
    GraphIndexingError,
    GraphIndexingService,
    GraphMergeService,
    Neo4jClient,
    get_neo4j_client,
)
from app.services.graph.extractors.factory import build_graph_extractor
from app.services.llms import LLMProvider
from app.services.llms.factory import get_llm_provider
from app.services.permissions import (
    can_assign_upload_organization,
    can_manage_document,
    can_upload_document,
    can_upload_to_knowledge_base,
    can_view_document,
)
from app.services.storage import StorageClient, get_storage_client
from app.services.vector_indexing_service import (
    DocumentChunksNotFoundError,
    DocumentVectorIndexStatusError,
    VectorIndexingError,
    VectorIndexingService,
)
from app.services.vector_indexing_service import (
    DocumentNotFoundError as VectorDocumentNotFoundError,
)
from app.services.vector_store import QdrantVectorStore, get_vector_store

router = APIRouter(prefix="/api/documents", tags=["documents"])


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
) -> ChunkingService:
    return ChunkingService(repository=repository)


def get_vector_indexing_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> VectorIndexingService:
    return VectorIndexingService(
        repository=repository,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )


def get_graph_indexing_service(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    graph_repository: Annotated[GraphRepository, Depends(get_graph_repository)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
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

    try:
        response = await service.upload_document(
            file,
            uploaded_by_user_id=current_user.id,
            organization_id=target_organization_id,
            knowledge_base_id=knowledge_base.id,
            visibility=visibility,
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

    results: list[DocumentBatchUploadItem] = []
    for upload_file in files:
        try:
            response = await service.upload_document(
                upload_file,
                uploaded_by_user_id=current_user.id,
                organization_id=target_organization_id,
                knowledge_base_id=knowledge_base.id,
                visibility=visibility,
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


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    organization_id: UUID | None = None,
    include_descendants: bool = False,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    uploaded_by: UUID | None = None,
    search: str | None = None,
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
    return DocumentListResponse(
        items=[_to_document_list_item_from_row(row) for row in paged_rows],
        total=len(visible_rows),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{document_id}/chunk",
    response_model=DocumentChunkResponse,
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
    "/{document_id}/index-vector",
    response_model=DocumentVectorIndexResponse,
)
async def index_document_vectors(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    log_repository: Annotated[DocumentLogRepository, Depends(get_document_log_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[VectorIndexingService, Depends(get_vector_indexing_service)],
) -> DocumentVectorIndexResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    try:
        response = await service.index_document(document_id)
        await log_repository.create_pipeline_log(
            document_id=document_id,
            user_id=current_user.id,
            organization_id=getattr(document, "organization_id", None),
            action="index_vector",
            status="success",
            message=f"Indexed {response.indexed_chunk_count} chunks.",
            metadata={"indexed_chunk_count": response.indexed_chunk_count},
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


@router.get("/{document_id}", response_model=DocumentDetailResponse)
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document is not visible.",
        )

    await log_repository.create_access_log(
        document_id=document.id,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        action="view",
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
        preview_text=(document.parsed_text or "")[:1000] or None,
        files=[
            DocumentFileResponse(
                id=str(file.id),
                filename=file.filename,
                mime_type=file.mime_type,
                storage_path=file.storage_path,
                file_size=file.file_size,
                created_at=file.created_at.isoformat(),
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


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
async def delete_document(
    document_id: UUID,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage_client)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> DocumentDeleteResponse:
    document = await _require_manageable_document(
        document_id=document_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    storage_paths = [file.storage_path for file in document.files]

    try:
        await vector_store.delete_points_for_document(document_id)
        for storage_path in storage_paths:
            await storage.delete_file(object_name=storage_path)
        await repository.delete_document(document)
        await repository.commit()
    except Exception as exc:
        await repository.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document data from MinIO, Qdrant, and database.",
        ) from exc

    return DocumentDeleteResponse(
        document_id=document_id,
        deleted=True,
        deleted_files=len(storage_paths),
        vector_points_deleted=True,
    )


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


def _to_document_list_item_from_row(row) -> DocumentListItem:
    return _to_document_list_item_from_values(
        document=row.document,
        filename=row.filename,
        chunk_count=row.chunk_count,
        parsed_character_count=row.parsed_character_count,
        vector_indexed_count=row.vector_indexed_count,
        pipeline_logs_count=row.pipeline_logs_count,
        graph_indexed=row.graph_indexed,
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
) -> DocumentListItem:
    effective_vector_indexed_count = vector_indexed_count
    if effective_vector_indexed_count is None and getattr(document, "status", None) == "indexed":
        effective_vector_indexed_count = chunk_count

    return DocumentListItem(
        document_id=document.id,
        title=document.title,
        status=document.status,
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
