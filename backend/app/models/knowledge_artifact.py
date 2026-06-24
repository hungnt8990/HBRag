from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.document import Document

ARTIFACT_TYPES = (
    "document_profile",
    "document_summary_artifact",
    "identifier_lookup",
    "procedure_artifact",
    "policy_rule_artifact",
    "legal_evidence_artifact",
    "table_evidence_artifact",
    "table_row_artifact",
    "assignment_artifact",
    "person_assignment_artifact",
    "training_decision",
    "qa_packet",
)

ARTIFACT_EXTRACTION_METHODS = ("deterministic", "llm", "hybrid")
ARTIFACT_STATUSES = ("ready", "skipped", "failed")


class KnowledgeArtifact(Base, TimestampMixin):
    __tablename__ = "knowledge_artifacts"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_chunk_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    normalized_identifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    citation_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False, default="deterministic", server_default="deterministic")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready", server_default="ready", index=True)

    document: Mapped[Document] = relationship(back_populates="knowledge_artifacts")

