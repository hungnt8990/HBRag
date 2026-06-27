from __future__ import annotations

import re
from uuid import UUID

from app.core.config import settings
from app.schemas.documents import HybridSearchResult
from app.services.graph.extractors.extractor_base import GraphExtractor
from app.services.graph.graph_models import GraphChunkCandidate
from app.services.graph.graph_neo4j_client import Neo4jClient

TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


class GraphRetrievalService:
    def __init__(
        self,
        *,
        neo4j_client: Neo4jClient,
        extractor: GraphExtractor,
    ) -> None:
        self._neo4j_client = neo4j_client
        self._extractor = extractor

    async def expand(
        self,
        *,
        query: str,
        initial_results: list[HybridSearchResult],
        document_ids: set[UUID] | None,
        depth: int,
        limit: int,
    ) -> list[GraphChunkCandidate]:
        if not settings.graph_enabled or not settings.graph_expansion_enabled:
            return []

        terms = await self._query_terms(query)
        visible_document_ids = [str(item) for item in (document_ids or set())]
        if document_ids is not None and not visible_document_ids:
            return []
        seed_chunk_ids = [str(result.chunk_id) for result in initial_results]
        candidates = await self._neo4j_client.expand_related_chunks(
            query_terms=terms,
            seed_chunk_ids=seed_chunk_ids,
            visible_document_ids=visible_document_ids,
            depth=depth,
            limit=limit,
        )
        existing_ids = {str(result.chunk_id) for result in initial_results}
        return [candidate for candidate in candidates if candidate.chunk_id not in existing_ids]

    async def _query_terms(self, query: str) -> list[str]:
        terms = {
            token.lower()
            for token in TOKEN_PATTERN.findall(query)
            if len(token.strip()) >= 3
        }
        try:
            extracted = await self._extractor.extract(
                content=query,
                max_entities=min(10, settings.graph_max_entities_per_chunk),
                max_relations=0,
            )
        except Exception:
            extracted = None
        if extracted is not None:
            for entity in extracted.entities:
                terms.add(entity.normalized_name.lower())
        return sorted(terms)
