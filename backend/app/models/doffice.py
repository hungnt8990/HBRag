from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DofficeRawDocument(Base, TimestampMixin):
    __tablename__ = "doffice_raw_documents"
    __table_args__ = (
        UniqueConstraint("id_vb", "content_hash", name="uq_doffice_raw_documents_id_vb_content_hash"),
        Index("ix_doffice_raw_documents_id_vb", "id_vb"),
        Index("ix_doffice_raw_documents_ky_hieu", "ky_hieu"),
        Index("ix_doffice_raw_documents_ngay_vb", "ngay_vb"),
        Index("ix_doffice_raw_documents_noi_ban_hanh", "noi_ban_hanh"),
        Index("ix_doffice_raw_documents_trich_yeu", "trich_yeu"),
        Index("ix_doffice_raw_documents_ten_file", "ten_file"),
        Index("ix_doffice_raw_documents_nam", "nam"),
        Index("ix_doffice_raw_documents_thang", "thang"),
        Index("ix_doffice_raw_documents_source_type", "source_type"),
        Index("ix_doffice_raw_documents_content_hash", "content_hash"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    id_vb: Mapped[str] = mapped_column(String(64), nullable=False)
    ky_hieu: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trich_yeu: Mapped[str | None] = mapped_column(Text, nullable=True)
    noi_ban_hanh: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nguoi_ky: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ten_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duong_dan: Mapped[str | None] = mapped_column(Text, nullable=True)
    ngay_vb: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ngay_tao: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ngay_capnhat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nam: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thang: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tom_tat: Mapped[str | None] = mapped_column(Text, nullable=True)
    noi_dung_raw: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="doffice_elasticsearch", server_default="doffice_elasticsearch")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="fetched", server_default="fetched")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    clean_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    chunk_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
