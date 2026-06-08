# ruff: noqa: E501
"""add graph rag audit tables

Revision ID: 0007_add_graph_rag_audit_tables
Revises: 0006_add_document_profile
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_add_graph_rag_audit_tables"
down_revision: str | None = "0006_add_document_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_document_status",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_indexed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("chunks_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_document_status")),
        sa.UniqueConstraint("document_id", name=op.f("uq_graph_document_status_document_id")),
    )
    op.create_index(
        op.f("ix_graph_document_status_document_id"),
        "graph_document_status",
        ["document_id"],
        unique=False,
    )

    op.create_table(
        "graph_extraction_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("merged_entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("merged_relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_extraction_logs")),
    )
    op.create_index(
        op.f("ix_graph_extraction_logs_document_id"),
        "graph_extraction_logs",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graph_extraction_logs_chunk_id"),
        "graph_extraction_logs",
        ["chunk_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_graph_extraction_logs_status"),
        "graph_extraction_logs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_graph_extraction_logs_status"), table_name="graph_extraction_logs")
    op.drop_index(op.f("ix_graph_extraction_logs_chunk_id"), table_name="graph_extraction_logs")
    op.drop_index(op.f("ix_graph_extraction_logs_document_id"), table_name="graph_extraction_logs")
    op.drop_table("graph_extraction_logs")
    op.drop_index(op.f("ix_graph_document_status_document_id"), table_name="graph_document_status")
    op.drop_table("graph_document_status")
