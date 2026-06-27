from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015_add_job_sync_tables"
down_revision = "0014_add_danh_muc_to_chuc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("is_full_scan", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_after", sa.String(length=64), nullable=True),
        sa.Column("total_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_acl_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_emb_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_no_acl", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_no_embedding", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("log_file_path", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_sync_runs_job_name", "job_sync_runs", ["job_name"])

    op.create_table(
        "job_sync_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("search_after", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("updated_after", sa.String(length=64), nullable=True),
        sa.Column("last_batch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_name", name="uq_job_sync_checkpoints_job_name"),
    )

    op.create_table(
        "job_sync_retries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_vb", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_vb", name="uq_job_sync_retries_id_vb"),
    )
    op.create_index("ix_job_sync_retries_next_retry_at", "job_sync_retries", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_job_sync_retries_next_retry_at", table_name="job_sync_retries")
    op.drop_table("job_sync_retries")
    op.drop_table("job_sync_checkpoints")
    op.drop_index("ix_job_sync_runs_job_name", table_name="job_sync_runs")
    op.drop_table("job_sync_runs")
