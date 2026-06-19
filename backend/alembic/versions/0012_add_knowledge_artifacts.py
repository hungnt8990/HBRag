"""Add knowledge artifacts and DB-backed RAG runtime config."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_knowledge_artifacts"
down_revision = "0011_chunk_enrichment"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_chunk_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("context_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column(
            "structured_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "normalized_identifiers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "citation_map",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "extraction_method",
            sa.String(length=32),
            server_default="deterministic",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default="ready", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_artifacts")),
    )
    op.create_index(op.f("ix_knowledge_artifacts_document_id"), "knowledge_artifacts", ["document_id"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_artifact_type"), "knowledge_artifacts", ["artifact_type"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_context_type"), "knowledge_artifacts", ["context_type"], unique=False)
    op.create_index(op.f("ix_knowledge_artifacts_status"), "knowledge_artifacts", ["status"], unique=False)
    op.create_index(
        "ix_knowledge_artifacts_normalized_identifiers_gin",
        "knowledge_artifacts",
        ["normalized_identifiers"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_knowledge_artifacts_structured_data_gin",
        "knowledge_artifacts",
        ["structured_data"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "rag_runtime_configs",
        sa.Column("config_name", sa.String(length=64), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("config_name", name=op.f("pk_rag_runtime_configs")),
    )


def downgrade() -> None:
    op.drop_table("rag_runtime_configs")
    op.drop_index("ix_knowledge_artifacts_structured_data_gin", table_name="knowledge_artifacts")
    op.drop_index("ix_knowledge_artifacts_normalized_identifiers_gin", table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_status"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_context_type"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_artifact_type"), table_name="knowledge_artifacts")
    op.drop_index(op.f("ix_knowledge_artifacts_document_id"), table_name="knowledge_artifacts")
    op.drop_table("knowledge_artifacts")

