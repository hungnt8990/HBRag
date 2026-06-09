from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import GraphDocumentStatus, GraphExtractionLog

logger = logging.getLogger(__name__)


class GraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_document_status(self, document_id: UUID) -> GraphDocumentStatus | None:
        statement = select(GraphDocumentStatus).where(
            GraphDocumentStatus.document_id == document_id
        )
        try:
            return (await self._session.execute(statement)).scalar_one_or_none()
        except ProgrammingError as exc:
            if not self._is_missing_graph_table_error(exc):
                raise
            await self._rollback_missing_graph_table()
            return None

    async def upsert_document_status(
        self,
        *,
        document_id: UUID,
        graph_indexed: bool,
        chunks_processed: int,
        entity_count: int,
        relation_count: int,
        error_message: str | None = None,
    ) -> GraphDocumentStatus:
        status = await self.get_document_status(document_id)
        if status is None:
            status = GraphDocumentStatus(document_id=document_id)
            self._session.add(status)

        status.graph_indexed = graph_indexed
        status.chunks_processed = chunks_processed
        status.entity_count = entity_count
        status.relation_count = relation_count
        status.last_indexed_at = datetime.now(UTC)
        status.error_message = error_message
        await self._session.flush()
        return status

    async def create_extraction_log(
        self,
        *,
        document_id: UUID,
        chunk_id: UUID | None,
        status: str,
        entity_count: int = 0,
        relation_count: int = 0,
        merged_entity_count: int = 0,
        merged_relation_count: int = 0,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphExtractionLog:
        log = GraphExtractionLog(
            document_id=document_id,
            chunk_id=chunk_id,
            status=status,
            entity_count=entity_count,
            relation_count=relation_count,
            merged_entity_count=merged_entity_count,
            merged_relation_count=merged_relation_count,
            error_message=error_message,
            log_metadata=metadata,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def list_extraction_logs(
        self,
        *,
        document_id: UUID,
        limit: int = 100,
    ) -> list[GraphExtractionLog]:
        statement = (
            select(GraphExtractionLog)
            .where(GraphExtractionLog.document_id == document_id)
            .order_by(GraphExtractionLog.created_at.desc())
            .limit(limit)
        )
        try:
            return list((await self._session.execute(statement)).scalars().all())
        except ProgrammingError as exc:
            if not self._is_missing_graph_table_error(exc):
                raise
            await self._rollback_missing_graph_table()
            return []

    async def delete_extraction_logs(self, *, document_id: UUID) -> None:
        await self._session.execute(
            delete(GraphExtractionLog).where(GraphExtractionLog.document_id == document_id)
        )
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @staticmethod
    def _is_missing_graph_table_error(exc: ProgrammingError) -> bool:
        message = str(exc).lower()
        original_name = exc.orig.__class__.__name__.lower() if exc.orig is not None else ""
        return (
            "undefinedtableerror" in original_name
            or "undefined table" in message
            or "relation \"graph_document_status\" does not exist" in message
            or "relation \"graph_extraction_logs\" does not exist" in message
        )

    async def _rollback_missing_graph_table(self) -> None:
        logger.warning(
            "Graph tables are missing; run `alembic upgrade head` to create graph "
            "audit tables. Returning empty graph status for compatibility with stale DBs."
        )
        await self._session.rollback()
