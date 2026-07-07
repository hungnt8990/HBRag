from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017_add_doffice_chat_sessions"
down_revision = "0016_add_api_request_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guard: startup của app tự tạo bảng này (checkfirst) để không alembic upgrade mù trên DB
    # chia sẻ. Bảng đã tồn tại -> bỏ qua (migration không vỡ).
    bind = op.get_bind()
    if "doffice_chat_messages" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "doffice_chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("actor_id_nv", sa.Integer(), nullable=True),
        sa.Column("actor_id_pb", sa.Integer(), nullable=True),
        sa.Column("actor_id_dv", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_doffice_chat_messages"),
    )
    op.create_index("ix_doffice_chat_messages_session_id", "doffice_chat_messages", ["session_id"])
    op.create_index("ix_doffice_chat_messages_actor_id_nv", "doffice_chat_messages", ["actor_id_nv"])


def downgrade() -> None:
    op.drop_index("ix_doffice_chat_messages_actor_id_nv", table_name="doffice_chat_messages")
    op.drop_index("ix_doffice_chat_messages_session_id", table_name="doffice_chat_messages")
    op.drop_table("doffice_chat_messages")
