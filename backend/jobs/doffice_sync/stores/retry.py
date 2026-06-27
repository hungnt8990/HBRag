"""Quản lý hàng đợi retry (job_sync_retries) — VB chưa có ACL / lỗi tạm thời."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from jobs.doffice_sync.models import JobSyncRetry


async def upsert_retry(
    session: AsyncSession,
    id_vb: str,
    *,
    reason: str,
    delay_minutes: int,
    last_error: str | None = None,
) -> None:
    """Tạo/cập nhật 1 bản ghi retry trong session đang xử lý (tăng retry_count)."""
    next_at = datetime.now(UTC) + timedelta(minutes=delay_minutes)
    existing = (
        await session.execute(select(JobSyncRetry).where(JobSyncRetry.id_vb == str(id_vb)))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            JobSyncRetry(
                id_vb=str(id_vb),
                reason=reason,
                retry_count=0,
                next_retry_at=next_at,
                last_error=last_error,
            )
        )
    else:
        existing.reason = reason
        existing.retry_count = (existing.retry_count or 0) + 1
        existing.next_retry_at = next_at
        existing.last_error = last_error
    await session.flush()


async def clear_retry(session: AsyncSession, id_vb: str) -> None:
    existing = (
        await session.execute(select(JobSyncRetry).where(JobSyncRetry.id_vb == str(id_vb)))
    ).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()


async def due_id_vbs(*, max_retry_count: int = 5, limit: int | None = None) -> list[str]:
    """Các id_vb đến hạn retry (own session)."""
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(JobSyncRetry.id_vb)
            .where(JobSyncRetry.next_retry_at <= now, JobSyncRetry.retry_count < max_retry_count)
            .order_by(JobSyncRetry.next_retry_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [str(v) for v in (await session.execute(stmt)).scalars().all()]
