"""Danh mục tổ chức EVNCPC (master data phục vụ phân quyền).

Ba bảng tra cứu được nạp từ file Excel xuất khẩu của hệ thống HRMS/eOffice:

- ``dm_don_vi``   : cây đơn vị (id_dv -> id_dv_cha), gốc là EVNCPC (id_dv=251).
- ``dm_phong_ban``: phòng ban, mỗi phòng ban thuộc một đơn vị (id_dv).
- ``dm_nhan_vien``: nhân viên, mỗi người thuộc đúng một đơn vị và một phòng ban.

Đây là dữ liệu danh mục (reference data), tách biệt với các bảng nghiệp vụ
``organizations`` / ``users`` của ứng dụng. Mục đích: lưu trữ một nơi duy nhất,
phục vụ lớp resolver/compressor phân quyền (xem
``app.services.security.security_acl_compressor``).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DonVi(Base, TimestampMixin):
    """Đơn vị trong cây tổ chức EVNCPC."""

    __tablename__ = "dm_don_vi"

    id_dv: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    id_dv_cha: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ma_dv: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ky_hieu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ten_dv: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Materialized path "/251/122/.../<id_dv>/" để truy vấn subtree bằng prefix.
    org_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_dm_don_vi_org_path", "org_path"),
    )


class PhongBan(Base, TimestampMixin):
    """Phòng ban thuộc một đơn vị."""

    __tablename__ = "dm_phong_ban"

    id_pb: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    id_dv: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    ma_pb: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ky_hieu_pb: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ten_pb: Mapped[str | None] = mapped_column(String(512), nullable=True)


class NhanVien(Base, TimestampMixin):
    """Nhân viên: thuộc đúng một đơn vị (id_dv) và một phòng ban (id_pb)."""

    __tablename__ = "dm_nhan_vien"

    id_nv: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ho_ten: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ten_hien_thi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    id_dv: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ten_dv: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_pb: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ma_pb: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ten_pb: Mapped[str | None] = mapped_column(String(512), nullable=True)
    id_hrms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ma_cv: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ten_cv: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
