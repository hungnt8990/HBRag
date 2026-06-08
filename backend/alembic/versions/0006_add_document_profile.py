# ruff: noqa: E501
"""add document profile

Revision ID: 0006_add_document_profile
Revises: 0005_add_memory_tables
Create Date: 2026-06-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_add_document_profile"
down_revision: str | None = "0005_add_memory_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "document_profile",
            sa.String(length=32),
            server_default="auto",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "document_profile")
