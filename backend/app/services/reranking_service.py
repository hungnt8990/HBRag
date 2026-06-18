from __future__ import annotations

import inspect
import logging
from typing import Any
from uuid import UUID

from app.repositories.documents import DocumentRepository
from app.repositories.retrieval_logs import RetrievalLogRepository
from app.schemas.documents import (
    HybridSearchResult,
    RerankSearchResponse,
    RerankSearchResult,
)
from app.services.access_control import (
    AccessAction,
    AccessFilter,
    SubjectContext,
    build_resource_context,
    can_access_resource,
)
from app.services.graph.graph_retrieval_service import GraphRetrievalService
from app.services.graph.models import GraphChunkCandidate
from app.services.hybrid_search import HybridSearchService, is_identifier_lookup_query
from app.services.rerankers import RerankCandidate, Reranker

DEFAULT_VECTOR_WEIGHT = 1.0
DEFAULT_KEYWORD_WEIGHT = 1.0

logger = logging.getLogger(__name__)


class RerankingError(RuntimeError):
    pass


class RerankingService:
    def __init__(
        self,
        *,
        hybrid_search_service: HybridSearchService,
        reranker: Reranker,
        retrieval_log_repository: RetrievalLogRepository,
        chunk_repository: DocumentRepository,
        graph_retrieval_service: GraphRetrievalService | None = None,
    ) -> None:
        self._hybrid_search_service = hybrid_search_service
        self._reranker = reranker
        self._retrieval_log_repository = retrieval_log_repository
        self._chunk_repository = chunk_repository
        self._graph_retrieval_service = graph_retrieval_service

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        candidate_k: int,
        session_id: UUID | None = None,
        document_ids: set[UUID] | None = None,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
        access_filter: AccessFilter | None = None,
        subject_context: SubjectContext | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> RerankSearchResponse:
        try:
            enrichment_kwargs = (
                {"retrieval_enrichment_enabled": True}
                if retrieval_enrichment_enabled
                else {}
            )
            intent_kwargs = (
                {"query_intent_rules": query_intent_rules}
                if query_intent_rules is not None
                else {}
            )
            if document_ids is None:
                hybrid_run = await self._run_hybrid_search(
                    query=query,
                    top_k=candidate_k,
                    vector_weight=DEFAULT_VECTOR_WEIGHT,
                    keyword_weight=DEFAULT_KEYWORD_WEIGHT,
                    save_log=False,
                    access_filter=access_filter,
                    **enrichment_kwargs,
                    **intent_kwargs,
                )
            else:
                hybrid_run = await self._run_hybrid_search(
                    query=query,
                    top_k=candidate_k,
                    vector_weight=DEFAULT_VECTOR_WEIGHT,
                    keyword_weight=DEFAULT_KEYWORD_WEIGHT,
                    save_log=False,
                    document_ids=document_ids,
                    access_filter=access_filter,
                    **enrichment_kwargs,
                    **intent_kwargs,
                )
            hybrid_results = list(hybrid_run.hybrid_response.results)
            if use_graph and self._graph_retrieval_service is not None:
                try:
                    graph_candidates = await self._graph_retrieval_service.expand(
                        query=query,
                        initial_results=hybrid_results,
                        document_ids=document_ids,
                        depth=graph_expansion_depth,
                        limit=graph_expansion_limit,
                    )
                    hybrid_results = self._merge_graph_candidates(
                        hybrid_results=hybrid_results,
                        graph_candidates=graph_candidates,
                    )
                except Exception:
                    pass
            full_content_by_chunk_id, allowed_chunk_ids = await self._load_full_content(
                hybrid_results,
                subject_context=subject_context,
            )
            if subject_context is not None:
                hybrid_results = [
                    result for result in hybrid_results if str(result.chunk_id) in allowed_chunk_ids
                ]
            candidates = [
                RerankCandidate(
                    chunk_id=str(result.chunk_id),
                    content=full_content_by_chunk_id.get(
                        str(result.chunk_id),
                        result.content_preview,
                    ),
                )
                for result in hybrid_results
            ]
            try:
                scores = await self._reranker.rerank(query=query, candidates=candidates)
                score_by_chunk_id = {score.chunk_id: score.score for score in scores}
            except Exception:
                logger.exception(
                    "Reranker failed; continuing with fused hybrid ranking."
                )
                score_by_chunk_id = {}

            reranked_results = self._build_results(
                query=query,
                hybrid_results=hybrid_results,
                score_by_chunk_id=score_by_chunk_id,
                top_k=top_k,
            )
            response = RerankSearchResponse(
                query=query,
                top_k=top_k,
                candidate_k=candidate_k,
                results=reranked_results,
            )

            await self._retrieval_log_repository.save_log(
                session_id=session_id,
                query=query,
                vector_results=hybrid_run.vector_response.model_dump(mode="json"),
                keyword_results=hybrid_run.keyword_response.model_dump(mode="json"),
                hybrid_results={
                    **hybrid_run.hybrid_response.model_dump(mode="json"),
                    "results": [result.model_dump(mode="json") for result in hybrid_results],
                },
                reranked_results=response.model_dump(mode="json"),
            )
            await self._retrieval_log_repository.commit()
        except Exception as exc:
            await self._retrieval_log_repository.rollback()
            raise RerankingError("Failed to run reranking search.") from exc

        return response

    async def _run_hybrid_search(self, **kwargs):
        if kwargs.get("access_filter") is None:
            kwargs.pop("access_filter", None)
        parameters = inspect.signature(self._hybrid_search_service.run_search).parameters
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        supported = (
            kwargs
            if accepts_var_kwargs
            else {key: value for key, value in kwargs.items() if key in parameters}
        )
        return await self._hybrid_search_service.run_search(**supported)

    async def _load_full_content(
        self,
        hybrid_results: list[HybridSearchResult],
        *,
        subject_context: SubjectContext | None = None,
    ) -> tuple[dict[str, str], set[str]]:
        chunk_ids = [UUID(str(result.chunk_id)) for result in hybrid_results]
        chunks = await self._chunk_repository.get_chunks_by_ids(chunk_ids)
        allowed: dict[str, str] = {}
        for chunk in chunks:
            if subject_context is not None:
                metadata = getattr(chunk, "chunk_metadata", None) or {}
                if not isinstance(metadata, dict) or not isinstance(metadata.get("access"), dict):
                    allowed[str(chunk.id)] = chunk.content
                    continue
                decision = can_access_resource(
                    subject_context,
                    build_resource_context(getattr(chunk, "document", None), chunk),
                    AccessAction.READ_ANSWER,
                )
                if not decision.allowed:
                    continue
            allowed[str(chunk.id)] = chunk.content
        return allowed, set(allowed)

    @staticmethod
    def _merge_graph_candidates(
        *,
        hybrid_results: list[HybridSearchResult],
        graph_candidates: list[GraphChunkCandidate],
    ) -> list[HybridSearchResult]:
        existing = {str(result.chunk_id) for result in hybrid_results}
        merged = list(hybrid_results)
        for candidate in graph_candidates:
            if candidate.chunk_id in existing:
                continue
            merged.append(
                HybridSearchResult(
                    chunk_id=candidate.chunk_id,
                    document_id=candidate.document_id,
                    fused_score=candidate.score,
                    vector_score=None,
                    keyword_score=None,
                    content_preview=candidate.content_preview,
                    metadata={
                        **candidate.metadata,
                        "matched_entities": candidate.matched_entities,
                        "relations": candidate.relations,
                    },
                    source_flags=["graph"],
                )
            )
            existing.add(candidate.chunk_id)
        return merged

    @staticmethod
    def _build_results(
        *,
        query: str,
        hybrid_results: list[HybridSearchResult],
        score_by_chunk_id: dict[str, float],
        top_k: int,
    ) -> list[RerankSearchResult]:
        identifier_lookup = is_identifier_lookup_query(query)

        def sort_key(result: HybridSearchResult) -> tuple[float, float, float, str]:
            metadata = result.metadata or {}
            identifier_boost = float(metadata.get("identifier_exact_boost") or 0.0)
            exact_priority = identifier_boost if identifier_lookup else 0.0
            return (
                -exact_priority,
                -score_by_chunk_id.get(str(result.chunk_id), 0.0),
                -result.fused_score,
                str(result.chunk_id),
            )

        ranked = sorted(hybrid_results, key=sort_key)

        return [
            RerankSearchResult(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                rerank_score=score_by_chunk_id.get(str(result.chunk_id), 0.0),
                fused_score=result.fused_score,
                vector_score=result.vector_score,
                keyword_score=result.keyword_score,
                content_preview=result.content_preview,
                metadata=result.metadata,
                source_flags=result.source_flags,
            )
            for result in ranked[:top_k]
        ]
