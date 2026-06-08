from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.organization import Organization
    from app.models.user import User

PIPELINE_ACTIONS = (
    "upload",
    "parse",
    "chunk",
    "index_vector",
    "keyword_index",
    "hybrid_search",
    "rerank",
    "chat",
    "error",
)
PIPELINE_STATUSES = ("success", "failed", "running")
ACCESS_ACTIONS = ("view", "download", "chat", "search")


class DocumentPipelineLog(Base, CreatedAtMixin):
    __tablename__ = "document_pipeline_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('upload', 'parse', 'chunk', 'index_vector', 'keyword_index', "
            "'hybrid_search', 'rerank', 'chat', 'error')",
            name="document_pipeline_logs_action",
        ),
        CheckConstraint(
            "status IN ('success', 'failed', 'running')",
            name="document_pipeline_logs_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    document: Mapped[Document] = relationship(back_populates="pipeline_logs")
    user: Mapped[User | None] = relationship()
    organization: Mapped[Organization | None] = relationship()


class DocumentAccessLog(Base, CreatedAtMixin):
    __tablename__ = "document_access_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('view', 'download', 'chat', 'search')",
            name="document_access_logs_action",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    log_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    document: Mapped[Document] = relationship(back_populates="access_logs")
    user: Mapped[User] = relationship()
    organization: Mapped[Organization] = relationship()
