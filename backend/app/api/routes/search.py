import inspect
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
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
from app.services.security.security_access_control import build_access_filter, build_subject_context
from app.services.documents.document_profiles import resolve_profile
from app.services.retrieval.retrieval_elasticsearch_keyword_search import (
    ElasticsearchKeywordSearchService,
    get_elasticsearch_keyword_store,
)
from app.services.embeddings.embedding_base import EmbeddingProvider
from app.services.embeddings.embedding_factory import get_embedding_provider
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.graph import GraphRetrievalService, Neo4jClient, get_neo4j_client
from app.services.graph.extractors.extractor_factory import build_graph_extractor
from app.services.retrieval.retrieval_hybrid_search import HybridSearchError, HybridSearchService
from app.services.retrieval.retrieval_keyword_search import KeywordSearchError, KeywordSearchService
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.security.security_permissions import can_view_document, can_view_knowledge_base
from app.services.rerankers import Reranker
from app.services.rerankers.reranker_factory import get_reranker
from app.services.rerankers.reranker_service import RerankingError, RerankingService
from app.services.vector.vector_indexing_service import VectorIndexingService, VectorSearchError
from app.services.vector.vector_store import QdrantVectorStore, get_vector_store

router = APIRouter(prefix="/api/search", tags=["search"])


def get_search_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)

def get_knowledge_base_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(session)


def get_vector_search_service(
    repository: Annotated[DocumentRepository, Depends(get_search_repository)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    vector_store: Annotated[QdrantVectorStore, Depends(get_vector_store)],
) -> VectorIndexingService:
    return VectorIndexingService(
        repository=repository,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        sparse_embedding_provider=get_sparse_embedding_provider(),
        keyword_index_store=(
            get_elasticsearch_keyword_store()
            if settings.elasticsearch_enabled
            else None
        ),
    )


def get_keyword_search_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KeywordSearchService | ElasticsearchKeywordSearchService:
    fallback = KeywordSearchService(session)
    if not settings.elasticsearch_enabled:
        return fallback
    return ElasticsearchKeywordSearchService(
        store=get_elasticsearch_keyword_store(),
        fallback_service=(fallback if settings.elasticsearch_fallback_to_postgres else None),
    )


def get_retrieval_log_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RetrievalLogRepository:
    return RetrievalLogRepository(session)


def get_graph_retrieval_service(
    neo4j_client: Annotated[Neo4jClient, Depends(get_neo4j_client)],
    llm_provider: Annotated[LLMGateway, Depends(get_llm_gateway)],
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
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[VectorIndexingService, Depends(get_vector_search_service)],
) -> VectorSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            knowledge_base_repository=knowledge_base_repository,
            current_user=current_user,
            knowledge_base_ids=request.knowledge_base_ids,
        )
        subject_context = await _subject_context(
            auth_repository=auth_repository,
            current_user=current_user,
        )
        return await _call_search_service(
            service,
            query=request.query,
            top_k=request.top_k,
            document_ids={str(document_id) for document_id in visible_ids},
            access_filter=build_access_filter(subject_context),
        )
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
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[HybridSearchService, Depends(get_hybrid_search_service)],
) -> HybridSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            knowledge_base_repository=knowledge_base_repository,
            current_user=current_user,
            knowledge_base_ids=request.knowledge_base_ids,
        )
        subject_context = await _subject_context(
            auth_repository=auth_repository,
            current_user=current_user,
        )
        return await _call_search_service(
            service,
            query=request.query,
            top_k=request.top_k,
            vector_weight=request.vector_weight,
            keyword_weight=request.keyword_weight,
            document_ids=visible_ids,
            access_filter=build_access_filter(subject_context),
            retrieval_enrichment_enabled=_resolve_retrieval_enrichment_enabled(
                profile=request.profile,
                override=request.retrieval_enrichment_enabled,
            ),
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
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RerankingService, Depends(get_reranking_service)],
) -> RerankSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            knowledge_base_repository=knowledge_base_repository,
            current_user=current_user,
            knowledge_base_ids=request.knowledge_base_ids,
        )
        subject_context = await _subject_context(
            auth_repository=auth_repository,
            current_user=current_user,
        )
        return await _call_search_service(
            service,
            query=request.query,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_ids=visible_ids,
            access_filter=build_access_filter(subject_context),
            subject_context=subject_context,
            retrieval_enrichment_enabled=_resolve_retrieval_enrichment_enabled(
                profile=request.profile,
                override=request.retrieval_enrichment_enabled,
            ),
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
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[KeywordSearchService, Depends(get_keyword_search_service)],
) -> KeywordSearchResponse:
    try:
        visible_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            knowledge_base_repository=knowledge_base_repository,
            current_user=current_user,
            knowledge_base_ids=request.knowledge_base_ids,
        )
        subject_context = await _subject_context(
            auth_repository=auth_repository,
            current_user=current_user,
        )
        return await _call_search_service(
            service,
            query=request.query,
            top_k=request.top_k,
            document_ids=visible_ids,
            access_filter=build_access_filter(subject_context),
            retrieval_enrichment_enabled=_resolve_retrieval_enrichment_enabled(
                profile=request.profile,
                override=request.retrieval_enrichment_enabled,
            ),
        )
    except KeywordSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


async def _visible_document_ids(
    *,
    repository: DocumentRepository,
    auth_repository: AuthRepository,
    knowledge_base_repository: KnowledgeBaseRepository,
    current_user: User,
    knowledge_base_ids: list[UUID] | None,
) -> set[UUID]:
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    requested_knowledge_base_ids = set(knowledge_base_ids or [])
    if knowledge_base_ids is not None and not requested_knowledge_base_ids:
        return set()
    if requested_knowledge_base_ids:
        knowledge_bases = await knowledge_base_repository.get_by_ids(
            list(requested_knowledge_base_ids)
        )
        if len(knowledge_bases) != len(requested_knowledge_base_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found.",
            )
        for knowledge_base in knowledge_bases:
            if not can_view_knowledge_base(
                current_user,
                knowledge_base,
                descendant_organization_ids=descendant_ids,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Knowledge base access is not allowed.",
                )
        documents = await repository.list_documents_for_permission_check(
            knowledge_base_ids=requested_knowledge_base_ids
        )
    else:
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


async def _subject_context(
    *,
    auth_repository: AuthRepository,
    current_user: User,
):
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    return build_subject_context(
        current_user,
        descendant_organization_ids=descendant_ids,
    )


def _resolve_retrieval_enrichment_enabled(
    *,
    profile: str | None,
    override: bool | None,
) -> bool:
    if override is not None:
        return bool(override)
    _ = resolve_profile(profile or "auto")
    return bool(settings.retrieval_enrichment_enabled)

async def _call_search_service(service, **kwargs):
    parameters = inspect.signature(service.search).parameters
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    supported = (
        kwargs
        if accepts_var_kwargs
        else {key: value for key, value in kwargs.items() if key in parameters}
    )
    return await service.search(**supported)
