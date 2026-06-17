"""Add chunk enrichment content."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0011_chunk_enrichment"
down_revision = "0010_ingestion_profiles"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

def upgrade() -> None:
    op.add_column("chunks", sa.Column("enriched_content", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("chunks", "enriched_content")
