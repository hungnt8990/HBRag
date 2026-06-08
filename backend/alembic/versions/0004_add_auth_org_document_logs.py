# ruff: noqa: E501
"""add auth org document logs

Revision ID: 0004_add_auth_org_document_logs
Revises: 0003_add_chunk_search_vector
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_add_auth_org_document_logs"
down_revision: str | None = "0003_add_chunk_search_vector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ma_dviqly", sa.String(length=64), nullable=False),
        sa.Column("ma_dviqly_cha", sa.String(length=64), nullable=True),
        sa.Column("ten_dviqly", sa.String(length=255), nullable=False),
        sa.Column("dvi_level", sa.Integer(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ma_dviqly"),
    )
    op.create_index(op.f("ix_organizations_ma_dviqly"), "organizations", ["ma_dviqly"])
    op.create_index(op.f("ix_organizations_ma_dviqly_cha"), "organizations", ["ma_dviqly_cha"])
    op.create_index(op.f("ix_organizations_parent_id"), "organizations", ["parent_id"])

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"])
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"])
    op.create_index(op.f("ix_users_username"), "users", ["username"])

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    op.add_column("documents", sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "visibility",
            sa.String(length=32),
            server_default="organization",
            nullable=False,
        ),
    )
    op.create_index(op.f("ix_documents_uploaded_by_user_id"), "documents", ["uploaded_by_user_id"])
    op.create_index(op.f("ix_documents_organization_id"), "documents", ["organization_id"])
    op.create_index(op.f("ix_documents_visibility"), "documents", ["visibility"])
    op.create_foreign_key(
        "fk_documents_uploaded_by_user_id_users",
        "documents",
        "users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_documents_organization_id_organizations",
        "documents",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "documents_visibility",
        "documents",
        "visibility IN ('private', 'organization', 'subtree', 'global')",
    )

    op.create_table(
        "document_pipeline_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('upload', 'parse', 'chunk', 'index_vector', 'keyword_index', "
            "'hybrid_search', 'rerank', 'chat', 'error')",
            name="document_pipeline_logs_action",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'failed', 'running')",
            name="document_pipeline_logs_status",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_pipeline_logs_action"), "document_pipeline_logs", ["action"])
    op.create_index(op.f("ix_document_pipeline_logs_document_id"), "document_pipeline_logs", ["document_id"])
    op.create_index(op.f("ix_document_pipeline_logs_organization_id"), "document_pipeline_logs", ["organization_id"])
    op.create_index(op.f("ix_document_pipeline_logs_status"), "document_pipeline_logs", ["status"])
    op.create_index(op.f("ix_document_pipeline_logs_user_id"), "document_pipeline_logs", ["user_id"])

    op.create_table(
        "document_access_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('view', 'download', 'chat', 'search')",
            name="document_access_logs_action",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_access_logs_action"), "document_access_logs", ["action"])
    op.create_index(op.f("ix_document_access_logs_document_id"), "document_access_logs", ["document_id"])
    op.create_index(op.f("ix_document_access_logs_organization_id"), "document_access_logs", ["organization_id"])
    op.create_index(op.f("ix_document_access_logs_user_id"), "document_access_logs", ["user_id"])

    roles_table = sa.table(
        "roles",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(
        roles_table,
        [
            {"id": "00000000-0000-0000-0000-000000000001", "name": "SUPER_ADMIN", "description": "System-wide administrator"},
            {"id": "00000000-0000-0000-0000-000000000002", "name": "CORP_ADMIN", "description": "Corporate organization administrator"},
            {"id": "00000000-0000-0000-0000-000000000003", "name": "COMPANY_ADMIN", "description": "Company-level administrator"},
            {"id": "00000000-0000-0000-0000-000000000004", "name": "UNIT_USER", "description": "Unit user"},
            {"id": "00000000-0000-0000-0000-000000000005", "name": "VIEWER", "description": "Read-only user"},
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_document_access_logs_user_id"), table_name="document_access_logs")
    op.drop_index(op.f("ix_document_access_logs_organization_id"), table_name="document_access_logs")
    op.drop_index(op.f("ix_document_access_logs_document_id"), table_name="document_access_logs")
    op.drop_index(op.f("ix_document_access_logs_action"), table_name="document_access_logs")
    op.drop_table("document_access_logs")

    op.drop_index(op.f("ix_document_pipeline_logs_user_id"), table_name="document_pipeline_logs")
    op.drop_index(op.f("ix_document_pipeline_logs_status"), table_name="document_pipeline_logs")
    op.drop_index(op.f("ix_document_pipeline_logs_organization_id"), table_name="document_pipeline_logs")
    op.drop_index(op.f("ix_document_pipeline_logs_document_id"), table_name="document_pipeline_logs")
    op.drop_index(op.f("ix_document_pipeline_logs_action"), table_name="document_pipeline_logs")
    op.drop_table("document_pipeline_logs")

    op.drop_constraint("documents_visibility", "documents", type_="check")
    op.drop_constraint("fk_documents_organization_id_organizations", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_uploaded_by_user_id_users", "documents", type_="foreignkey")
    op.drop_index(op.f("ix_documents_visibility"), table_name="documents")
    op.drop_index(op.f("ix_documents_organization_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_uploaded_by_user_id"), table_name="documents")
    op.drop_column("documents", "visibility")
    op.drop_column("documents", "organization_id")
    op.drop_column("documents", "uploaded_by_user_id")

    op.drop_table("user_roles")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_organizations_parent_id"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_ma_dviqly_cha"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_ma_dviqly"), table_name="organizations")
    op.drop_table("organizations")
