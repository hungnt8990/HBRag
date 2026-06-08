from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import GraphDocumentStatus, GraphExtractionLog


class GraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_document_status(self, document_id: UUID) -> GraphDocumentStatus | None:
        statement = select(GraphDocumentStatus).where(GraphDocumentStatus.document_id == document_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

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
        return list((await self._session.execute(statement)).scalars().all())

    async def delete_extraction_logs(self, *, document_id: UUID) -> None:
        await self._session.execute(
            delete(GraphExtractionLog).where(GraphExtractionLog.document_id == document_id)
        )
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
