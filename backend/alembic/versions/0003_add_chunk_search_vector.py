"""Add chunk full-text search vector.

Revision ID: 0003_add_chunk_search_vector
Revises: 0002_add_parsed_document_fields
Create Date: 2026-06-07 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_add_chunk_search_vector"
down_revision: str | None = "0002_add_parsed_document_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.execute(
        """
        UPDATE chunks
        SET search_vector = to_tsvector('simple', coalesce(content, ''))
        """
    )
    op.create_index(
        "ix_chunks_search_vector",
        "chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_column("chunks", "search_vector")
