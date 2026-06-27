"""SQLAlchemy models cho job DOffice sync (Base RIÊNG, tạo idempotent).

Dùng Base riêng (KHÔNG ``app.db.base.Base``) để bảng job không lọt vào metadata của
app (tránh sai test_models / alembic autogenerate). Migration ``0015`` mirror 3 bảng.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.mixins import TimestampMixin


class Base(DeclarativeBase):
    pass


class JobSyncRun(Base):
    __tablename__ = "job_sync_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False, default="doffice_sync")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    is_full_scan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_acl_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_emb_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_no_acl: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_no_embedding: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    log_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class JobSyncCheckpoint(Base):
    __tablename__ = "job_sync_checkpoints"
    __table_args__ = (UniqueConstraint("job_name", name="uq_job_sync_checkpoints_job_name"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    search_after: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    updated_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_batch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    batch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class JobSyncRetry(Base, TimestampMixin):
    __tablename__ = "job_sync_retries"
    __table_args__ = (UniqueConstraint("id_vb", name="uq_job_sync_retries_id_vb"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    id_vb: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


async def ensure_job_tables() -> None:
    """Tạo idempotent 3 bảng job (CREATE IF NOT EXISTS) — KHÔNG động alembic_version."""
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
