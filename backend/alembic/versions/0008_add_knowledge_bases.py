# ruff: noqa: E501
"""add knowledge bases

Revision ID: 0008_add_knowledge_bases
Revises: 0007_add_graph_rag_audit_tables
Create Date: 2026-06-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_add_knowledge_bases"
down_revision: str | None = "0007_add_graph_rag_audit_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_KNOWLEDGE_BASE_NAME = "Default Knowledge Base"


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visibility", sa.String(length=32), server_default="organization", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "visibility IN ('private', 'organization', 'subtree', 'global')",
            name="knowledge_bases_visibility",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_bases_organization_id"), "knowledge_bases", ["organization_id"])
    op.create_index(op.f("ix_knowledge_bases_owner_user_id"), "knowledge_bases", ["owner_user_id"])
    op.create_index(op.f("ix_knowledge_bases_visibility"), "knowledge_bases", ["visibility"])
    op.create_index(op.f("ix_knowledge_bases_is_active"), "knowledge_bases", ["is_active"])

    op.create_table(
        "knowledge_base_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "permission IN ('owner', 'admin', 'editor', 'viewer')",
            name="knowledge_base_members_permission",
        ),
        sa.CheckConstraint(
            "num_nonnulls(user_id, role_id, organization_id) = 1",
            name="knowledge_base_members_single_target",
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_base_members_knowledge_base_id"), "knowledge_base_members", ["knowledge_base_id"])
    op.create_index(op.f("ix_knowledge_base_members_user_id"), "knowledge_base_members", ["user_id"])
    op.create_index(op.f("ix_knowledge_base_members_role_id"), "knowledge_base_members", ["role_id"])
    op.create_index(op.f("ix_knowledge_base_members_organization_id"), "knowledge_base_members", ["organization_id"])
    op.create_index(op.f("ix_knowledge_base_members_permission"), "knowledge_base_members", ["permission"])

    op.add_column("documents", sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_documents_knowledge_base_id"), "documents", ["knowledge_base_id"])
    op.create_foreign_key(
        "fk_documents_knowledge_base_id_knowledge_bases",
        "documents",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        sa.text(
            """
            INSERT INTO knowledge_bases (
                id, name, description, organization_id, owner_user_id,
                visibility, is_active, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                :default_name,
                'Default knowledge base for existing documents.',
                source.organization_id,
                (
                    SELECT users.id
                    FROM users
                    WHERE users.organization_id = source.organization_id
                    ORDER BY users.created_at ASC
                    LIMIT 1
                ),
                'organization',
                true,
                now(),
                now()
            FROM (
                SELECT DISTINCT documents.organization_id
                FROM documents
                WHERE documents.organization_id IS NOT NULL
            ) AS source
            WHERE NOT EXISTS (
                SELECT 1
                FROM knowledge_bases existing
                WHERE existing.name = :default_name
                  AND existing.organization_id = source.organization_id
            )
            """
        ).bindparams(default_name=DEFAULT_KNOWLEDGE_BASE_NAME)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO knowledge_bases (
                id, name, description, organization_id, owner_user_id,
                visibility, is_active, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                :default_name,
                'Fallback default knowledge base for existing documents without organization.',
                NULL,
                (SELECT users.id FROM users ORDER BY users.created_at ASC LIMIT 1),
                'private',
                true,
                now(),
                now()
            WHERE EXISTS (
                SELECT 1 FROM documents WHERE documents.organization_id IS NULL
            )
            AND NOT EXISTS (
                SELECT 1
                FROM knowledge_bases existing
                WHERE existing.name = :default_name
                  AND existing.organization_id IS NULL
            )
            """
        ).bindparams(default_name=DEFAULT_KNOWLEDGE_BASE_NAME)
    )
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET knowledge_base_id = (
                SELECT knowledge_bases.id
                FROM knowledge_bases
                WHERE knowledge_bases.name = :default_name
                  AND knowledge_bases.organization_id = documents.organization_id
                ORDER BY knowledge_bases.created_at ASC
                LIMIT 1
            )
            WHERE documents.organization_id IS NOT NULL
              AND documents.knowledge_base_id IS NULL
            """
        ).bindparams(default_name=DEFAULT_KNOWLEDGE_BASE_NAME)
    )
    op.execute(
        sa.text(
            """
            UPDATE documents
            SET knowledge_base_id = (
                SELECT knowledge_bases.id
                FROM knowledge_bases
                WHERE knowledge_bases.name = :default_name
                  AND knowledge_bases.organization_id IS NULL
                ORDER BY knowledge_bases.created_at ASC
                LIMIT 1
            )
            WHERE documents.organization_id IS NULL
              AND documents.knowledge_base_id IS NULL
            """
        ).bindparams(default_name=DEFAULT_KNOWLEDGE_BASE_NAME)
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_knowledge_base_id_knowledge_bases",
        "documents",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_documents_knowledge_base_id"), table_name="documents")
    op.drop_column("documents", "knowledge_base_id")

    op.drop_index(op.f("ix_knowledge_base_members_permission"), table_name="knowledge_base_members")
    op.drop_index(op.f("ix_knowledge_base_members_organization_id"), table_name="knowledge_base_members")
    op.drop_index(op.f("ix_knowledge_base_members_role_id"), table_name="knowledge_base_members")
    op.drop_index(op.f("ix_knowledge_base_members_user_id"), table_name="knowledge_base_members")
    op.drop_index(op.f("ix_knowledge_base_members_knowledge_base_id"), table_name="knowledge_base_members")
    op.drop_table("knowledge_base_members")

    op.drop_index(op.f("ix_knowledge_bases_is_active"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_visibility"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_owner_user_id"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_organization_id"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
