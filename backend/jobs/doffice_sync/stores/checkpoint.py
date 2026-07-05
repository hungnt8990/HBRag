"""Lưu/đọc checkpoint search_after để resume incremental scroll."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from jobs.doffice_sync.models import JobSyncCheckpoint


@dataclass
class Checkpoint:
    search_after: list
    updated_after: str | None


class CheckpointStore:
    async def load(self, job_name: str) -> Checkpoint | None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(JobSyncCheckpoint).where(JobSyncCheckpoint.job_name == job_name)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return Checkpoint(search_after=row.search_after or [], updated_after=row.updated_after)

    async def save(
        self,
        job_name: str,
        search_after: list,
        updated_after: str | None,
        *,
        batch_count: int = 0,
        doc_count: int = 0,
    ) -> None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(JobSyncCheckpoint).where(JobSyncCheckpoint.job_name == job_name)
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if row is None:
                session.add(
                    JobSyncCheckpoint(
                        job_name=job_name,
                        search_after=search_after,
                        updated_after=updated_after,
                        last_batch_at=now,
                        batch_count=batch_count,
                        doc_count=doc_count,
                    )
                )
            else:
                row.search_after = search_after
                row.updated_after = updated_after
                row.last_batch_at = now
                row.batch_count = batch_count
                row.doc_count = doc_count
            await session.commit()

    async def clear(self, job_name: str) -> None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(JobSyncCheckpoint).where(JobSyncCheckpoint.job_name == job_name)
                )
            ).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def clear_prefix(self, prefix: str) -> int:
        """Xoá MỌI checkpoint có ``job_name`` bắt đầu bằng ``prefix`` (mọi phạm vi của 1 job).

        Dùng khi reset toàn bộ: job chunk kho AI tách checkpoint theo phạm vi issuer_org
        (``kho_ai_chunk``, ``kho_ai_chunk_org256``...) -> xoá hết để quét lại từ đầu. Trả số dòng đã xoá.
        """
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(JobSyncCheckpoint).where(JobSyncCheckpoint.job_name.like(f"{prefix}%"))
                )
            ).scalars().all()
            for row in rows:
                await session.delete(row)
            if rows:
                await session.commit()
            return len(rows)
