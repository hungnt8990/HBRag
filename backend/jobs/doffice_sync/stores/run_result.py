"""Lưu kết quả mỗi lần chạy job (job_sync_runs)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from jobs.doffice_sync.models import JobSyncRun

# action -> cột tổng hợp trong job_sync_runs.
ACTION_TO_COUNTER = {
    "created": "total_created",
    "acl_updated": "total_acl_updated",
    "emb_updated": "total_emb_updated",
    "skipped": "total_skipped",
    "no_acl": "total_no_acl",
    "error": "total_failed",
}


class RunResultStore:
    async def create(
        self, *, config_snapshot: dict, is_full_scan: bool, updated_after: str | None, log_file_path: str | None
    ) -> UUID:
        async with AsyncSessionLocal() as session:
            run = JobSyncRun(
                job_name="doffice_sync",
                started_at=datetime.now(UTC),
                status="running",
                is_full_scan=is_full_scan,
                updated_after=updated_after,
                config_snapshot=config_snapshot,
                log_file_path=log_file_path,
                error_summary={},
            )
            session.add(run)
            await session.flush()
            run_id = run.id
            await session.commit()
            return run_id

    async def finish(
        self, run_id: UUID, *, status: str, stats: dict, error_summary: dict
    ) -> None:
        async with AsyncSessionLocal() as session:
            run = (
                await session.execute(select(JobSyncRun).where(JobSyncRun.id == run_id))
            ).scalar_one_or_none()
            if run is None:
                return
            run.finished_at = datetime.now(UTC)
            run.status = status
            run.total_scanned = stats.get("total_scanned", 0)
            run.total_created = stats.get("total_created", 0)
            run.total_acl_updated = stats.get("total_acl_updated", 0)
            run.total_emb_updated = stats.get("total_emb_updated", 0)
            run.total_skipped = stats.get("total_skipped", 0)
            run.total_no_acl = stats.get("total_no_acl", 0)
            run.total_failed = stats.get("total_failed", 0)
            run.total_no_embedding = stats.get("total_no_embedding", 0)
            run.error_summary = error_summary
            await session.commit()
