# ruff: noqa: E501
"""add memory tables

Revision ID: 0005_add_memory_tables
Revises: 0004_add_auth_org_document_logs
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_add_memory_tables"
down_revision: str | None = "0004_add_auth_org_document_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), server_default="local", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "memory_type IN ('preference', 'task', 'entity', 'instruction', 'fact')",
            name="user_memories_memory_type",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'chat_extracted', 'system', 'mem0')",
            name="user_memories_source",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_memories_user_id"), "user_memories", ["user_id"])
    op.create_index(op.f("ix_user_memories_organization_id"), "user_memories", ["organization_id"])
    op.create_index(op.f("ix_user_memories_memory_type"), "user_memories", ["memory_type"])

    op.create_table(
        "session_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("last_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_session_summaries_session_id"), "session_summaries", ["session_id"])
    op.create_index(op.f("ix_session_summaries_user_id"), "session_summaries", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_session_summaries_user_id"), table_name="session_summaries")
    op.drop_index(op.f("ix_session_summaries_session_id"), table_name="session_summaries")
    op.drop_table("session_summaries")

    op.drop_index(op.f("ix_user_memories_memory_type"), table_name="user_memories")
    op.drop_index(op.f("ix_user_memories_organization_id"), table_name="user_memories")
    op.drop_index(op.f("ix_user_memories_user_id"), table_name="user_memories")
    op.drop_table("user_memories")
