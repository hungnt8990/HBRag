from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, bindparam, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult

CONTENT_PREVIEW_LIMIT = 300
KEYWORD_QUERY_PARAM = "keyword_query"
TS_CONFIG = literal_column("'simple'")


class KeywordSearchError(RuntimeError):
    pass


class KeywordSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
    ) -> KeywordSearchResponse:
        try:
            if document_ids is not None and not document_ids:
                return KeywordSearchResponse(query=query, top_k=top_k, results=[])
            statement = self.build_statement(query=query, top_k=top_k, document_ids=document_ids)
            result = await self._session.execute(statement)
            rows = result.mappings().all()
        except Exception as exc:
            raise KeywordSearchError("Failed to run keyword search.") from exc

        return KeywordSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                KeywordSearchResult(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    score=float(row["score"] or 0.0),
                    content_preview=self._preview(row["content"]),
                    metadata=self._metadata(row["metadata"]),
                )
                for row in rows
            ],
        )

    @staticmethod
    def build_statement(
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
    ) -> Select[tuple[Any, ...]]:
        query_param = bindparam(KEYWORD_QUERY_PARAM, value=query)
        ts_query = func.plainto_tsquery(TS_CONFIG, query_param)
        rank = func.ts_rank_cd(Chunk.search_vector, ts_query).label("score")

        statement = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.document_id.label("document_id"),
                rank,
                Chunk.content.label("content"),
                Chunk.chunk_metadata.label("metadata"),
            )
            .where(
                Chunk.search_vector.is_not(None),
                Chunk.search_vector.op("@@")(ts_query),
            )
            .order_by(rank.desc(), Chunk.document_id.asc(), Chunk.chunk_index.asc())
            .limit(top_k)
        )
        if document_ids is not None:
            statement = statement.where(Chunk.document_id.in_(document_ids))
        return statement

    @staticmethod
    def _preview(content: str) -> str:
        return content[:CONTENT_PREVIEW_LIMIT]

    @staticmethod
    def _metadata(metadata: dict[str, Any] | None) -> dict[str, object]:
        return dict(metadata or {})
