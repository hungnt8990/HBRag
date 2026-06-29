"""Retrieval 2 tầng cho thiết kế DOffice mới (Stage-1 ES∪docmeta -> Stage-2 chunks).

Tái dùng nguyên ``TwoStageHybridSearchService`` (retrieval_document_index.py): chỉ thay
``document_index`` bằng :class:`DofficeStage1Resolver`. Stage-1 hợp nhất (RRF) 2 nguồn
cấp văn bản — ES BM25 (`hbrag_doffice_documents_v1`) cho keyword + Qdrant docmeta
(`hbrag_doffice_docmeta_v1`) cho ngữ nghĩa/ký hiệu — ra top-N document_id. Stage-2 search
chunk trong Qdrant (`hbrag_doffice_chunks_v1`, dense+sparse) giới hạn trong N văn bản đó.

ES không còn index chunk -> nửa keyword cấp chunk (NoOp) để giữ contract HybridSearchService.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.schemas.documents import KeywordSearchResponse

if TYPE_CHECKING:
    from app.services.security.security_acl_payload import AclSubject

logger = logging.getLogger(__name__)

_RRF_K = 60


class NoOpKeywordSearchService:
    """Keyword cấp chunk rỗng (ES chỉ còn BM25 cấp văn bản ở Stage-1)."""

    async def search(self, **kwargs: Any) -> KeywordSearchResponse:
        return KeywordSearchResponse(
            query=str(kwargs.get("query") or ""),
            top_k=int(kwargs.get("top_k") or 0),
            results=[],
        )


class DofficeStage1Resolver:
    """Stage-1: hợp nhất ES BM25 (keyword) + Qdrant docmeta (semantic) -> top-N document_id.

    Khớp chữ ký ``search_documents(query, *, top_n, acl_subject, query_vector)`` mà
    :class:`TwoStageHybridSearchService` gọi, nên cắm thẳng vào chỗ ``document_index``.
    """

    def __init__(self, *, bm25_store: Any, docmeta_search_service: Any) -> None:
        self._bm25 = bm25_store
        self._docmeta = docmeta_search_service

    async def search_documents(
        self,
        query: str,
        *,
        top_n: int = 50,
        acl_subject: "AclSubject | None" = None,
        query_vector: list[float] | None = None,  # bỏ qua: docmeta tự embed query
    ) -> list[str]:
        scores: dict[str, float] = {}

        # ES BM25 cấp văn bản (keyword, mạnh cho ký hiệu/từ khóa chính xác).
        try:
            es_docs = await self._bm25.search_documents(query, top_n=top_n, acl_subject=acl_subject)
        except Exception:
            logger.exception("Stage-1 ES BM25 lỗi -> bỏ qua nhánh keyword")
            es_docs = []
        for rank, doc in enumerate(es_docs):
            doc_id = str(doc.get("document_id") or "")
            if doc_id:
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank)

        # Qdrant docmeta (semantic + sparse ký hiệu).
        try:
            docmeta_resp = await self._docmeta.search(query=query, top_k=top_n, acl_subject=acl_subject)
            docmeta_results = docmeta_resp.results
        except Exception:
            logger.exception("Stage-1 Qdrant docmeta lỗi -> bỏ qua nhánh semantic")
            docmeta_results = []
        for rank, result in enumerate(docmeta_results):
            doc_id = str(getattr(result, "document_id", "") or "")
            if doc_id:
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (_RRF_K + rank)

        ranked = sorted(scores, key=lambda d: scores[d], reverse=True)
        logger.info("Stage-1 DOffice: es=%d docmeta=%d -> %d doc", len(es_docs), len(docmeta_results), len(ranked))
        return ranked[:top_n]


def build_doffice_two_stage_search(
    *,
    repository: Any,
    llm_gateway: Any,
    retrieval_log_repository: Any,
) -> Any:
    """Dựng service retrieval 2 tầng DOffice (drop-in cho RerankingService)."""
    from app.core.config import settings
    from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
    from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore
    from app.services.retrieval.retrieval_document_index import TwoStageHybridSearchService
    from app.services.retrieval.retrieval_hybrid_search import HybridSearchService
    from app.services.vector.vector_indexing_service import VectorIndexingService
    from app.services.vector.vector_store import (
        get_doffice_chunks_vector_store,
        get_doffice_docmeta_vector_store,
    )

    sparse = get_sparse_embedding_provider()
    chunk_search = VectorIndexingService(
        repository=repository, llm_gateway=llm_gateway,
        vector_store=get_doffice_chunks_vector_store(),
        sparse_embedding_provider=sparse, keyword_index_store=None,
    )
    docmeta_search = VectorIndexingService(
        repository=repository, llm_gateway=llm_gateway,
        vector_store=get_doffice_docmeta_vector_store(),
        sparse_embedding_provider=sparse, keyword_index_store=None,
    )
    base = HybridSearchService(
        vector_search_service=chunk_search,
        keyword_search_service=NoOpKeywordSearchService(),
        retrieval_log_repository=retrieval_log_repository,
    )
    resolver = DofficeStage1Resolver(
        bm25_store=DofficeBm25DocumentStore(),
        docmeta_search_service=docmeta_search,
    )
    return TwoStageHybridSearchService(
        hybrid_search=base,
        document_index=resolver,
        llm_gateway=None,  # resolver tự embed query ở nhánh docmeta
        stage1_top_n=settings.two_stage_stage1_top_n,
        stage1_min_results=settings.two_stage_stage1_min_results,
        enabled=True,
    )
