from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_add_api_request_logs"
down_revision = "0015_add_job_sync_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard: startup của app tự tạo bảng này (checkfirst) để không phải alembic upgrade mù trên
    # DB chia sẻ. Nếu bảng đã tồn tại -> bỏ qua để migration không vỡ.
    bind = op.get_bind()
    if "api_request_logs" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "api_request_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("actor_id_nv", sa.Integer(), nullable=True),
        sa.Column("actor_id_pb", sa.Integer(), nullable=True),
        sa.Column("actor_id_dv", sa.Integer(), nullable=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("search_type", sa.String(length=32), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("used_vector", sa.Boolean(), nullable=True),
        sa.Column("result_total", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="success", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_request_logs"),
    )
    op.create_index("ix_api_request_logs_endpoint", "api_request_logs", ["endpoint"])
    op.create_index("ix_api_request_logs_actor_id_nv", "api_request_logs", ["actor_id_nv"])
    op.create_index("ix_api_request_logs_search_type", "api_request_logs", ["search_type"])
    op.create_index("ix_api_request_logs_status", "api_request_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_api_request_logs_status", table_name="api_request_logs")
    op.drop_index("ix_api_request_logs_search_type", table_name="api_request_logs")
    op.drop_index("ix_api_request_logs_actor_id_nv", table_name="api_request_logs")
    op.drop_index("ix_api_request_logs_endpoint", table_name="api_request_logs")
    op.drop_table("api_request_logs")
