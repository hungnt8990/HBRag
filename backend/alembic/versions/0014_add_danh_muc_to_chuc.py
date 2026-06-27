from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_add_danh_muc_to_chuc"
down_revision = "0013_add_doffice_raw_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dm_don_vi",
        sa.Column("id_dv", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("id_dv_cha", sa.BigInteger(), nullable=True),
        sa.Column("ma_dv", sa.String(length=64), nullable=True),
        sa.Column("ky_hieu", sa.String(length=128), nullable=True),
        sa.Column("ten_dv", sa.String(length=512), nullable=True),
        sa.Column("org_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id_dv", name="pk_dm_don_vi"),
    )
    op.create_index("ix_dm_don_vi_id_dv_cha", "dm_don_vi", ["id_dv_cha"])
    op.create_index("ix_dm_don_vi_ma_dv", "dm_don_vi", ["ma_dv"])
    op.create_index("ix_dm_don_vi_org_path", "dm_don_vi", ["org_path"])

    op.create_table(
        "dm_phong_ban",
        sa.Column("id_pb", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("id_dv", sa.BigInteger(), nullable=False),
        sa.Column("ma_pb", sa.String(length=128), nullable=True),
        sa.Column("ky_hieu_pb", sa.String(length=128), nullable=True),
        sa.Column("ten_pb", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id_pb", name="pk_dm_phong_ban"),
    )
    op.create_index("ix_dm_phong_ban_id_dv", "dm_phong_ban", ["id_dv"])
    op.create_index("ix_dm_phong_ban_ma_pb", "dm_phong_ban", ["ma_pb"])

    op.create_table(
        "dm_nhan_vien",
        sa.Column("id_nv", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("ho_ten", sa.String(length=255), nullable=True),
        sa.Column("ten_hien_thi", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("id_dv", sa.BigInteger(), nullable=True),
        sa.Column("ten_dv", sa.String(length=512), nullable=True),
        sa.Column("id_pb", sa.BigInteger(), nullable=True),
        sa.Column("ma_pb", sa.String(length=128), nullable=True),
        sa.Column("ten_pb", sa.String(length=512), nullable=True),
        sa.Column("id_hrms", sa.String(length=64), nullable=True),
        sa.Column("ma_cv", sa.String(length=128), nullable=True),
        sa.Column("ten_cv", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id_nv", name="pk_dm_nhan_vien"),
    )
    op.create_index("ix_dm_nhan_vien_username", "dm_nhan_vien", ["username"])
    op.create_index("ix_dm_nhan_vien_email", "dm_nhan_vien", ["email"])
    op.create_index("ix_dm_nhan_vien_id_dv", "dm_nhan_vien", ["id_dv"])
    op.create_index("ix_dm_nhan_vien_id_pb", "dm_nhan_vien", ["id_pb"])


def downgrade() -> None:
    op.drop_index("ix_dm_nhan_vien_id_pb", table_name="dm_nhan_vien")
    op.drop_index("ix_dm_nhan_vien_id_dv", table_name="dm_nhan_vien")
    op.drop_index("ix_dm_nhan_vien_email", table_name="dm_nhan_vien")
    op.drop_index("ix_dm_nhan_vien_username", table_name="dm_nhan_vien")
    op.drop_table("dm_nhan_vien")

    op.drop_index("ix_dm_phong_ban_ma_pb", table_name="dm_phong_ban")
    op.drop_index("ix_dm_phong_ban_id_dv", table_name="dm_phong_ban")
    op.drop_table("dm_phong_ban")

    op.drop_index("ix_dm_don_vi_org_path", table_name="dm_don_vi")
    op.drop_index("ix_dm_don_vi_ma_dv", table_name="dm_don_vi")
    op.drop_index("ix_dm_don_vi_id_dv_cha", table_name="dm_don_vi")
    op.drop_table("dm_don_vi")
