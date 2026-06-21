from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_add_doffice_raw_documents"
down_revision = "0012_knowledge_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doffice_raw_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_vb", sa.String(length=64), nullable=False),
        sa.Column("ky_hieu", sa.String(length=255), nullable=True),
        sa.Column("trich_yeu", sa.Text(), nullable=True),
        sa.Column("noi_ban_hanh", sa.String(length=255), nullable=True),
        sa.Column("nguoi_ky", sa.String(length=255), nullable=True),
        sa.Column("ten_file", sa.String(length=512), nullable=True),
        sa.Column("duong_dan", sa.Text(), nullable=True),
        sa.Column("ngay_vb", sa.String(length=64), nullable=True),
        sa.Column("ngay_tao", sa.String(length=64), nullable=True),
        sa.Column("ngay_capnhat", sa.String(length=64), nullable=True),
        sa.Column("nam", sa.Integer(), nullable=True),
        sa.Column("thang", sa.Integer(), nullable=True),
        sa.Column("tom_tat", sa.Text(), nullable=True),
        sa.Column("noi_dung_raw", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_type", sa.String(length=64), server_default="doffice_elasticsearch", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_hash", sa.String(length=64), nullable=False),
        sa.Column("sync_status", sa.String(length=32), server_default="fetched", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("clean_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("chunk_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("embedding_status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_vb", "content_hash", name="uq_doffice_raw_documents_id_vb_content_hash"),
    )
    for column in (
        "id_vb",
        "ky_hieu",
        "ngay_vb",
        "noi_ban_hanh",
        "trich_yeu",
        "ten_file",
        "nam",
        "thang",
        "source_type",
        "content_hash",
    ):
        op.create_index(f"ix_doffice_raw_documents_{column}", "doffice_raw_documents", [column])


def downgrade() -> None:
    for column in reversed(
        (
            "id_vb",
            "ky_hieu",
            "ngay_vb",
            "noi_ban_hanh",
            "trich_yeu",
            "ten_file",
            "nam",
            "thang",
            "source_type",
            "content_hash",
        )
    ):
        op.drop_index(f"ix_doffice_raw_documents_{column}", table_name="doffice_raw_documents")
    op.drop_table("doffice_raw_documents")
