from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_log import DocumentAccessLog, DocumentPipelineLog


class DocumentLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pipeline_log(
        self,
        *,
        document_id: UUID,
        action: str,
        status: str,
        user_id: UUID | None = None,
        organization_id: UUID | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentPipelineLog:
        log = DocumentPipelineLog(
            document_id=document_id,
            user_id=user_id,
            organization_id=organization_id,
            action=action,
            status=status,
            message=message,
            log_metadata=metadata,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def create_access_log(
        self,
        *,
        document_id: UUID,
        user_id: UUID,
        organization_id: UUID,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentAccessLog:
        log = DocumentAccessLog(
            document_id=document_id,
            user_id=user_id,
            organization_id=organization_id,
            action=action,
            log_metadata=metadata,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def latest_pipeline_logs(
        self,
        *,
        document_id: UUID,
        limit: int = 50,
    ) -> list[DocumentPipelineLog]:
        statement = (
            select(DocumentPipelineLog)
            .where(DocumentPipelineLog.document_id == document_id)
            .order_by(DocumentPipelineLog.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def count_pipeline_logs(self, *, document_id: UUID) -> int:
        statement = select(func.count(DocumentPipelineLog.id)).where(
            DocumentPipelineLog.document_id == document_id
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def access_log_summary(self, *, document_id: UUID) -> dict[str, int]:
        statement = (
            select(DocumentAccessLog.action, func.count(DocumentAccessLog.id))
            .where(DocumentAccessLog.document_id == document_id)
            .group_by(DocumentAccessLog.action)
        )
        rows = await self._session.execute(statement)
        return {action: count for action, count in rows.all()}

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
