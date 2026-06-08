"""Add parsed document fields and chunked status.

Revision ID: 0002_add_parsed_document_fields
Revises: 0001_initial_schema
Create Date: 2026-06-06 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_parsed_document_fields"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("parsed_text", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("ck_documents_status"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status"),
        "documents",
        "status IN ('uploaded', 'parsing', 'parsed', 'chunked', 'indexed', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_status"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status"),
        "documents",
        "status IN ('uploaded', 'parsing', 'parsed', 'indexed', 'failed')",
    )
    op.drop_column("documents", "parsed_at")
    op.drop_column("documents", "parsed_text")
