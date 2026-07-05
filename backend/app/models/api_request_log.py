from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin


class ApiRequestLog(Base, CreatedAtMixin):
    """Log CHUNG (động) cho các API tra cứu — mỗi lần gọi lưu 1 dòng: AI/ai hỏi, tham số vào,
    lọc/kiểu tra cứu, kết quả ra sao, thời gian, trạng thái.

    Thiết kế "động" để dùng lại cho API mới sau này KHÔNG cần đổi schema:
      - Cột CỐ ĐỊNH (endpoint/actor/query/search_type/status/duration...) để lọc + thống kê nhanh.
      - 2 cột JSONB ``request_params`` + ``response_summary`` chứa payload tùy ý của từng API.
    API mới chỉ cần gọi ``log_api_request(...)`` với ``endpoint`` riêng và nhét dữ liệu đặc thù
    vào 2 cột JSONB.
    """

    __tablename__ = "api_request_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # --- Định danh lời gọi ---
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # vd "document-search/search"
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- NGƯỜI hỏi (id_nv là nguồn sự thật; pb/dv resolve từ danh mục) ---
    actor_id_nv: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_id_pb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id_dv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Nội dung tra cứu + cách lọc (cột hay lọc/thống kê nhất) ---
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # exact|ref|bm25|hybrid|fusion
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)  # list|excerpt
    used_vector: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Kết quả + vận hành ---
    result_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="success", index=True)  # success|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Phần ĐỘNG: toàn bộ tham số vào + tóm tắt kết quả (mở rộng tự do cho API mới) ---
    request_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    response_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
