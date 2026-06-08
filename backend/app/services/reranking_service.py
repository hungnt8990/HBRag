from __future__ import annotations

from uuid import UUID

from app.repositories.retrieval_logs import RetrievalLogRepository
from app.services.graph.graph_retrieval_service import GraphRetrievalService
from app.services.graph.models import GraphChunkCandidate
from app.schemas.documents import (
    HybridSearchResult,
    RerankSearchResponse,
    RerankSearchResult,
)
from app.services.hybrid_search import HybridSearchService
from app.services.rerankers import RerankCandidate, Reranker

DEFAULT_VECTOR_WEIGHT = 1.0
DEFAULT_KEYWORD_WEIGHT = 1.0


class RerankingError(RuntimeError):
    pass


class RerankingService:
    def __init__(
        self,
        *,
        hybrid_search_service: HybridSearchService,
        reranker: Reranker,
        retrieval_log_repository: RetrievalLogRepository,
        graph_retrieval_service: GraphRetrievalService | None = None,
    ) -> None:
        self._hybrid_search_service = hybrid_search_service
        self._reranker = reranker
        self._retrieval_log_repository = retrieval_log_repository
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
    ) -> RerankSearchResponse:
        try:
            if document_ids is None:
                hybrid_run = await self._hybrid_search_service.run_search(
                    query=query,
                    top_k=candidate_k,
                    vector_weight=DEFAULT_VECTOR_WEIGHT,
                    keyword_weight=DEFAULT_KEYWORD_WEIGHT,
                    save_log=False,
                )
            else:
                hybrid_run = await self._hybrid_search_service.run_search(
                    query=query,
                    top_k=candidate_k,
                    vector_weight=DEFAULT_VECTOR_WEIGHT,
                    keyword_weight=DEFAULT_KEYWORD_WEIGHT,
                    save_log=False,
                    document_ids=document_ids,
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
            candidates = [
                RerankCandidate(
                    chunk_id=str(result.chunk_id),
                    content=result.content_preview,
                )
                for result in hybrid_results
            ]
            scores = await self._reranker.rerank(query=query, candidates=candidates)
            score_by_chunk_id = {score.chunk_id: score.score for score in scores}
            reranked_results = self._build_results(
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
        hybrid_results: list[HybridSearchResult],
        score_by_chunk_id: dict[str, float],
        top_k: int,
    ) -> list[RerankSearchResult]:
        ranked = sorted(
            hybrid_results,
            key=lambda result: (
                -score_by_chunk_id.get(str(result.chunk_id), 0.0),
                -result.fused_score,
                str(result.chunk_id),
            ),
        )

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
