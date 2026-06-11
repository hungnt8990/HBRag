from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from app.repositories.retrieval_logs import RetrievalLogRepository
from app.schemas.documents import (
    HybridSearchResponse,
    HybridSearchResult,
    KeywordSearchResponse,
    KeywordSearchResult,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.keyword_search import KeywordSearchService
from app.services.segment_router import schema_or_procedure_metadata_boost
from app.services.table_relationships import (
    analyze_person_area_membership_query,
    score_person_area_membership_match,
)
from app.services.vector_indexing_service import VectorIndexingService

DEFAULT_RRF_K = 60
HYBRID_DEPTH_MULTIPLIER = 3
SourceFlag = Literal["vector", "keyword", "lexical_exact"]


class HybridSearchError(RuntimeError):
    pass


@dataclass
class _FusedResult:
    chunk_id: object
    document_id: object
    fused_score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    content_preview: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    source_flags: list[SourceFlag] = field(default_factory=list)


@dataclass(frozen=True)
class HybridSearchRun:
    vector_response: VectorSearchResponse
    keyword_response: KeywordSearchResponse
    hybrid_response: HybridSearchResponse


class HybridSearchService:
    def __init__(
        self,
        *,
        vector_search_service: VectorIndexingService,
        keyword_search_service: KeywordSearchService,
        retrieval_log_repository: RetrievalLogRepository,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than 0.")

        self._vector_search_service = vector_search_service
        self._keyword_search_service = keyword_search_service
        self._retrieval_log_repository = retrieval_log_repository
        self._rrf_k = rrf_k

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        save_log: bool = True,
        document_ids: set[UUID] | None = None,
    ) -> HybridSearchResponse:
        run = await self.run_search(
            query=query,
            top_k=top_k,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            save_log=save_log,
            document_ids=document_ids,
        )
        return run.hybrid_response

    async def run_search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        save_log: bool = True,
        document_ids: set[UUID] | None = None,
    ) -> HybridSearchRun:
        depth = top_k * HYBRID_DEPTH_MULTIPLIER

        try:
            if document_ids is None:
                vector_response = await self._vector_search_service.search(
                    query=query,
                    top_k=depth,
                )
                keyword_response = await self._keyword_search_service.search(
                    query=query,
                    top_k=depth,
                )
            else:
                vector_response = await self._vector_search_service.search(
                    query=query,
                    top_k=depth,
                    document_ids={str(document_id) for document_id in document_ids},
                )
                keyword_response = await self._keyword_search_service.search(
                    query=query,
                    top_k=depth,
                    document_ids=document_ids,
                )
            hybrid_results = self.fuse_results(
                query=query,
                vector_results=vector_response.results,
                keyword_results=keyword_response.results,
                top_k=top_k,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight,
                rrf_k=self._rrf_k,
            )
            response = HybridSearchResponse(
                query=query,
                top_k=top_k,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight,
                results=hybrid_results,
            )

            run = HybridSearchRun(
                vector_response=vector_response,
                keyword_response=keyword_response,
                hybrid_response=response,
            )
            if save_log:
                await self._retrieval_log_repository.save_log(
                    query=query,
                    vector_results=vector_response.model_dump(mode="json"),
                    keyword_results=keyword_response.model_dump(mode="json"),
                    hybrid_results=response.model_dump(mode="json"),
                )
                await self._retrieval_log_repository.commit()
        except Exception as exc:
            await self._retrieval_log_repository.rollback()
            raise HybridSearchError("Failed to run hybrid search.") from exc

        return run

    @staticmethod
    def fuse_results(
        *,
        query: str | None = None,
        vector_results: list[VectorSearchResult],
        keyword_results: list[KeywordSearchResult],
        top_k: int,
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> list[HybridSearchResult]:
        fused: dict[str, _FusedResult] = {}

        for rank, result in enumerate(vector_results, start=1):
            key = str(result.chunk_id)
            item = fused.get(key)
            if item is None:
                item = _FusedResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content_preview=result.content_preview,
                    metadata=dict(result.metadata),
                )
                fused[key] = item

            item.vector_score = result.score
            item.fused_score += vector_weight * HybridSearchService._rrf_score(
                rank=rank,
                rrf_k=rrf_k,
            )
            HybridSearchService._append_source_flag(item, "vector")

        for rank, result in enumerate(keyword_results, start=1):
            key = str(result.chunk_id)
            item = fused.get(key)
            if item is None:
                item = _FusedResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content_preview=result.content_preview,
                    metadata=dict(result.metadata),
                )
                fused[key] = item

            item.keyword_score = result.score
            item.fused_score += keyword_weight * HybridSearchService._rrf_score(
                rank=rank,
                rrf_k=rrf_k,
            )
            HybridSearchService._append_source_flag(item, "keyword")
            if result.metadata.get("exact_match_terms"):
                HybridSearchService._append_source_flag(item, "lexical_exact")

        membership_query = analyze_person_area_membership_query(query or "")
        for item in fused.values():
            metadata_boost = schema_or_procedure_metadata_boost(query or "", item.metadata)
            if metadata_boost > 0:
                item.fused_score += metadata_boost
                item.metadata = {**item.metadata, "metadata_exact_boost": metadata_boost}

            if membership_query is not None:
                boost = score_person_area_membership_match(
                    membership_query,
                    content=item.content_preview,
                    metadata=item.metadata,
                )
                if boost <= 0:
                    continue
                item.fused_score += boost
                item.metadata = {**item.metadata, "membership_boost": boost}

        ranked = sorted(
            fused.values(),
            key=lambda item: (-item.fused_score, str(item.chunk_id)),
        )

        return [
            HybridSearchResult(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                fused_score=item.fused_score,
                vector_score=item.vector_score,
                keyword_score=item.keyword_score,
                content_preview=item.content_preview,
                metadata=item.metadata,
                source_flags=item.source_flags,
            )
            for item in ranked[:top_k]
        ]

    @staticmethod
    def _rrf_score(*, rank: int, rrf_k: int) -> float:
        return 1.0 / (rrf_k + rank)

    @staticmethod
    def _append_source_flag(item: _FusedResult, source: SourceFlag) -> None:
        if source not in item.source_flags:
            item.source_flags.append(source)
