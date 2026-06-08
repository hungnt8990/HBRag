from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.documents import DocumentRepository
from app.repositories.retrieval_logs import RetrievalLogRepository
from app.schemas.documents import (
    HybridSearchRequest,
    HybridSearchResponse,
    KeywordSearchRequest,
    KeywordSearchResponse,
    RerankSearchRequest,
    RerankSearchResponse,
    VectorSearchRequest,
    VectorSearchResponse,
)
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.factory import get_embedding_provider
from app.services.graph import GraphRetrievalService, Neo4jClient, get_neo4j_client
from app.services.graph.extractors.factory import build_graph_extractor
from app.services.hybrid_search import HybridSearchError, HybridSearchService
from app.services.keyword_search import KeywordSearchError, KeywordSearchService
from app.services.llms import LLMProvider
from app.services.llms.factory import get_llm_provider
from app.services.permissions import can_view_document
from app.services.rerankers import Reranker
from app.services.rerankers.factory import get_reranker
from app.services.reranking_service import RerankingError, RerankingService
from app.services.vector_indexing_service import VectorIndexingService, VectorSearchError
from app.services.vector_store import QdrantVectorStore, get_vector_store

router = APIRouter(prefix="/api/search", tags=["search"])


def get_search_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


def get_vector_search_service(
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> VectorIndexingService:
    return VectorIndexingService(
        repository=repository,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )


def get_keyword_search_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KeywordSearchService:
    return KeywordSearchService(session)


def get_retrieval_log_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RetrievalLogRepository:
    return RetrievalLogRepository(session)


def get_graph_retrieval_service(
    neo4j_client: Annotated[Neo4jClient, Depends(get_neo4j_client)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> GraphRetrievalService:
    extractor = build_graph_extractor(llm_provider=llm_provider)
    return GraphRetrievalService(neo4j_client=neo4j_client, extractor=extractor)


def get_hybrid_search_service(
    vector_search_service: Annotated[
        VectorIndexingService,
        Depends(get_vector_search_service),
    ],
    keyword_search_service: Annotated[
        KeywordSearchService,
        Depends(get_keyword_search_service),
    ],
    retrieval_log_repository: Annotated[
        RetrievalLogRepository,
        Depends(get_retrieval_log_repository),
    ],
) -> HybridSearchService:
    return HybridSearchService(
        vector_search_service=vector_search_service,
        keyword_search_service=keyword_search_service,
        retrieval_log_repository=retrieval_log_repository,
    )


def get_reranking_service(
    hybrid_search_service: Annotated[
        HybridSearchService,
        Depends(get_hybrid_search_service),
    ],
    reranker: Annotated[Reranker, Depends(get_reranker)],
    retrieval_log_repository: Annotated[
        RetrievalLogRepository,
        Depends(get_retrieval_log_repository),
    ],
    chunk_repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    graph_retrieval_service: Annotated[
        GraphRetrievalService,
        Depends(get_graph_retrieval_service),
    ],
) -> RerankingService:
    return RerankingService(
        hybrid_search_service=hybrid_search_service,
        reranker=reranker,
        retrieval_log_repository=retrieval_log_repository,
        chunk_repository=chunk_repository,
        graph_retrieval_service=graph_retrieval_service,
    )


@router.post("/vector", response_model=VectorSearchResponse)
async def vector_search(
    request: VectorSearchRequest,
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[VectorIndexingService, Depends(get_vector_search_service)],
) -> VectorSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            current_user=current_user,
        )
        try:
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                document_ids={str(document_id) for document_id in visible_ids},
            )
        except TypeError as exc:
            if "document_ids" not in str(exc):
                raise
            return await service.search(query=request.query, top_k=request.top_k)
    except VectorSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/hybrid", response_model=HybridSearchResponse)
async def hybrid_search(
    request: HybridSearchRequest,
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[HybridSearchService, Depends(get_hybrid_search_service)],
) -> HybridSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            current_user=current_user,
        )
        try:
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                vector_weight=request.vector_weight,
                keyword_weight=request.keyword_weight,
                document_ids=visible_ids,
            )
        except TypeError as exc:
            if "document_ids" not in str(exc):
                raise
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                vector_weight=request.vector_weight,
                keyword_weight=request.keyword_weight,
            )
    except HybridSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/rerank", response_model=RerankSearchResponse)
async def rerank_search(
    request: RerankSearchRequest,
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RerankingService, Depends(get_reranking_service)],
) -> RerankSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            current_user=current_user,
        )
        try:
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                candidate_k=request.candidate_k,
                document_ids=visible_ids,
            )
        except TypeError as exc:
            if "document_ids" not in str(exc):
                raise
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                candidate_k=request.candidate_k,
            )
    except RerankingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post("/keyword", response_model=KeywordSearchResponse)
async def keyword_search(
    request: KeywordSearchRequest,
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[KeywordSearchService, Depends(get_keyword_search_service)],
) -> KeywordSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            current_user=current_user,
        )
        try:
            return await service.search(
                query=request.query,
                top_k=request.top_k,
                document_ids=visible_ids,
            )
        except TypeError as exc:
            if "document_ids" not in str(exc):
                raise
            return await service.search(query=request.query, top_k=request.top_k)
    except KeywordSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


async def _visible_document_ids(
    *,
    repository: DocumentRepository,
    auth_repository: AuthRepository,
    current_user: User,
) -> set:
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    documents = await repository.list_documents_for_permission_check()
    return {
        document.id
        for document in documents
        if can_view_document(
            current_user,
            document,
            descendant_organization_ids=descendant_ids,
        )
    }
