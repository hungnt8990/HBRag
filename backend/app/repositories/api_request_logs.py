from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_request_log import ApiRequestLog


class ApiRequestLogRepository:
    """Ghi/đọc log lời gọi API (bảng ``api_request_logs``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields: Any) -> ApiRequestLog:
        log = ApiRequestLog(**fields)
        self._session.add(log)
        await self._session.flush()
        return log

    async def list_logs(
        self,
        *,
        endpoint: str | None = None,
        actor_id_nv: int | None = None,
        search_type: str | None = None,
        status: str | None = None,
        query_contains: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApiRequestLog], int]:
        """Trả ``(rows, total)`` — lọc động theo tham số truyền, sắp xếp ``created_at`` giảm dần.

        ``total`` = tổng bản ghi KHỚP bộ lọc (bỏ limit/offset) để client phân trang.
        """
        conditions = []
        if endpoint:
            conditions.append(ApiRequestLog.endpoint == endpoint)
        if actor_id_nv is not None:
            conditions.append(ApiRequestLog.actor_id_nv == actor_id_nv)
        if search_type:
            conditions.append(ApiRequestLog.search_type == search_type)
        if status:
            conditions.append(ApiRequestLog.status == status)
        if query_contains:
            conditions.append(ApiRequestLog.query.ilike(f"%{query_contains}%"))
        if created_from is not None:
            conditions.append(ApiRequestLog.created_at >= created_from)
        if created_to is not None:
            conditions.append(ApiRequestLog.created_at <= created_to)

        total_stmt = select(func.count()).select_from(ApiRequestLog)
        list_stmt = (
            select(ApiRequestLog)
            .order_by(ApiRequestLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        for cond in conditions:
            total_stmt = total_stmt.where(cond)
            list_stmt = list_stmt.where(cond)

        total = int((await self._session.execute(total_stmt)).scalar_one())
        rows = list((await self._session.execute(list_stmt)).scalars().all())
        return rows, total

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
