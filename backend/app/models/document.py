from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.chunk import Chunk
    from app.models.citation import Citation
    from app.models.document_log import DocumentAccessLog, DocumentPipelineLog
    from app.models.graph import GraphDocumentStatus, GraphExtractionLog
    from app.models.knowledge_artifact import KnowledgeArtifact
    from app.models.knowledge_base import KnowledgeBase
    from app.models.organization import Organization
    from app.models.user import User

DOCUMENT_STATUSES = ("uploaded", "parsing", "parsed", "chunked", "indexed", "failed")
DOCUMENT_VISIBILITIES = ("private", "organization", "subtree", "global")


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'parsing', 'parsed', 'chunked', 'indexed', 'failed')",
            name="status",
        ),
        CheckConstraint(
            "visibility IN ('private', 'organization', 'subtree', 'global')",
            name="documents_visibility",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    uploaded_by_user_id: Mapped[UUID | None] = mapped_column(
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
    knowledge_base_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="organization",
        server_default="organization",
        index=True,
    )
    document_profile: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="auto",
        server_default="auto",
    )

    uploaded_by: Mapped[User | None] = relationship(back_populates="uploaded_documents")
    organization: Mapped[Organization | None] = relationship(back_populates="documents")
    knowledge_base: Mapped[KnowledgeBase | None] = relationship(back_populates="documents")
    files: Mapped[list[DocumentFile]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    citations: Mapped[list[Citation]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    knowledge_artifacts: Mapped[list[KnowledgeArtifact]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    pipeline_logs: Mapped[list[DocumentPipelineLog]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    access_logs: Mapped[list[DocumentAccessLog]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    graph_extraction_logs: Mapped[list[GraphExtractionLog]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    graph_document_status: Mapped[GraphDocumentStatus | None] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class DocumentFile(Base, CreatedAtMixin):
    __tablename__ = "document_files"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="files")
