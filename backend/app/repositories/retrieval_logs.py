from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.retrieval import RetrievalLog


class RetrievalLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_log(
        self,
        *,
        query: str,
        vector_results: dict[str, Any] | None = None,
        keyword_results: dict[str, Any] | None = None,
        hybrid_results: dict[str, Any] | None = None,
        reranked_results: dict[str, Any] | None = None,
        session_id: UUID | None = None,
    ) -> RetrievalLog:
        log = RetrievalLog(
            session_id=session_id,
            query=query,
            vector_results=vector_results,
            keyword_results=keyword_results,
            hybrid_results=hybrid_results,
            reranked_results=reranked_results,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
