"""Lịch sử hội thoại cho API ``/api/document-search/chat`` (luồng DOffice / kho AI) — 1 BẢNG.

Mỗi lượt thoại (user hỏi / assistant trả lời) = 1 dòng; ``session_id`` (UUID) nhóm các dòng thành
một cuộc hội thoại. KHÔNG cần bảng "session" riêng: mọi thông tin session đều suy ra từ chính các
message của nó (chủ hội thoại = ``actor_id_nv``, thời điểm lượt cuối = ``created_at`` message mới
nhất, thứ tự = ``seq``).

TÁCH RIÊNG khỏi ``chat_sessions``/``chat_messages`` legacy (luồng RAG cũ gắn ``users.id`` UUID +
Citation/RetrievalLog). Ở đây người hỏi là ``ID_NV`` (int, decode từ JWT DOffice) — KHÔNG có record
``users``.

Short-term memory (đưa lịch sử vào ngữ cảnh) CHỈ áp khi lượt gần nhất còn "tươi" (cách hiện tại <
ngưỡng ``document_chat_session_short_term_ttl_h``, mặc định 4h). Quá ngưỡng: KHÔNG nạp lịch sử nhưng
lượt mới VẪN được ghi vào cùng ``session_id`` (giữ TOÀN BỘ lịch sử — gồm câu trả lời LLM — để đánh giá).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

DOFFICE_CHAT_ROLES = ("user", "assistant")


class DofficeChatMessage(Base, CreatedAtMixin):
    """Một lượt thoại thuộc 1 hội thoại (``session_id``). ``session_id`` trả về FE ở lượt đầu."""

    __tablename__ = "doffice_chat_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # thứ tự trong hội thoại (0,1,2,...)

    # CHỦ hội thoại (id_nv là nguồn sự thật; pb/dv resolve từ ACL) — bind session theo id_nv để
    # chống rò rỉ lịch sử người khác. Lặp trên mỗi dòng (chi phí nhỏ, đổi lại bỏ được bảng session).
    actor_id_nv: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_id_pb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id_dv: Mapped[int | None] = mapped_column(Integer, nullable=True)

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Phạm vi văn bản của lượt hỏi (document_ids) nếu có — lưu để đối chiếu/đánh giá.
    document_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    # Phần động: citations/evidence_summary/rewritten_query/used_vector... (chỉ dòng assistant).
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
