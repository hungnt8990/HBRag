"""Điều phối session + short-term memory cho ``/api/document-search/chat`` (1 bảng, nhóm session_id).

Hai việc, mỗi việc TỰ mở DB session và TUYỆT ĐỐI không raise ra ngoài (lỗi lưu lịch sử KHÔNG được
làm hỏng luồng chat chính):

  - ``resolve_session``: từ ``session_id`` FE gửi (hoặc rỗng) -> quyết định session_id thực dùng +
    LỊCH SỬ ngắn hạn (nếu lượt gần nhất còn "tươi" < ngưỡng ttl). Lần đầu (không gửi session_id) ->
    sinh session_id mới để FE giữ cho các lượt sau. KHÔNG tạo row nào ở bước này (row chỉ sinh khi
    ``persist_turn`` cuối lượt).
  - ``persist_turn``: ghi lượt user + assistant vào lịch sử (giữ TOÀN BỘ — gồm câu trả lời LLM — để
    đánh giá), gắn ``session_id``/``seq``/``actor``.

Ngưỡng short-term = ``document_chat_session_short_term_ttl_h`` (mặc định 4h). Quá ngưỡng: KHÔNG nạp
lịch sử (short_term_used=False) nhưng lượt mới VẪN ghi vào cùng session (lịch sử liền mạch).
Bind theo chủ: chỉ nhận session khớp ``actor_id_nv`` — lệch chủ -> session mới (chống rò rỉ lịch sử).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.doffice_chat import DofficeChatRepository
from app.services.retrieval.document_chat_service import ChatHistoryMessage

logger = logging.getLogger("document_chat_session")

# ChatHistoryMessage.content bị pydantic giới hạn 8000 ký tự -> cắt khi dựng từ DB.
_MAX_HISTORY_CONTENT = 8000


@dataclass
class ResolvedSession:
    """Kết quả resolve: session_id thực dùng + lịch sử ngắn hạn (đã áp ngưỡng ttl)."""

    session_id: UUID
    history: list[ChatHistoryMessage] = field(default_factory=list)
    is_new: bool = True
    short_term_used: bool = False


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def _is_fresh(last_message_at: datetime | None, ttl_hours: float) -> bool:
    """Lượt gần nhất còn trong ngưỡng ttl (giờ)? Chưa có lượt nào -> coi như KHÔNG có short-term."""
    if last_message_at is None or ttl_hours <= 0:
        return False
    ts = last_message_at
    if ts.tzinfo is None:  # phòng khi DB trả naive datetime
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) <= timedelta(hours=ttl_hours)


def _history_from_rows(rows: list[Any]) -> list[ChatHistoryMessage]:
    out: list[ChatHistoryMessage] = []
    for row in rows:
        role = row.role
        content = " ".join(str(row.content or "").split())
        if role not in ("user", "assistant") or not content:
            continue
        out.append(ChatHistoryMessage(role=role, content=content[:_MAX_HISTORY_CONTENT]))
    return out


async def resolve_session(*, requested_session_id: str | None, id_nv: int) -> ResolvedSession:
    """Quyết định session_id + lịch sử ngắn hạn. Lỗi DB -> session_id tạm (uuid4), lịch sử rỗng."""
    sid = _parse_uuid(requested_session_id)
    if sid is None:
        # Lần đầu (FE chưa có session_id) -> sinh mới, chưa có lịch sử.
        return ResolvedSession(session_id=uuid4(), history=[], is_new=True, short_term_used=False)

    ttl_h = float(settings.document_chat_session_short_term_ttl_h)
    load_limit = max(1, int(settings.document_chat_session_load_messages))
    try:
        async with AsyncSessionLocal() as db:
            rows = await DofficeChatRepository(db).recent_messages(sid, limit=load_limit)
    except Exception:  # noqa: BLE001 — lịch sử phụ, KHÔNG chặn chat
        logger.warning("resolve_session lỗi id_nv=%s -> session_id tạm (không lưu).", id_nv, exc_info=True)
        return ResolvedSession(session_id=uuid4(), history=[], is_new=True, short_term_used=False)

    if not rows:
        # session_id chưa từng dùng (FE tự giữ / bị xoá) -> coi như hội thoại mới với chính id này.
        return ResolvedSession(session_id=sid, history=[], is_new=True, short_term_used=False)
    if rows[-1].actor_id_nv != id_nv:
        # Bind theo chủ: session của người khác -> cấp session mới, KHÔNG lộ lịch sử.
        return ResolvedSession(session_id=uuid4(), history=[], is_new=True, short_term_used=False)

    short_term = _is_fresh(rows[-1].created_at, ttl_h)  # rows[-1] = lượt mới nhất
    history = _history_from_rows(rows) if short_term else []
    return ResolvedSession(
        session_id=sid,
        history=history,
        is_new=False,
        short_term_used=bool(short_term and history),
    )


async def persist_turn(
    *,
    session_id: UUID,
    id_nv: int,
    id_pb: int | None,
    id_dv: int | None,
    user_query: str,
    assistant_answer: str | None,
    assistant_meta: dict[str, Any] | None,
    document_ids: list[str] | None = None,
) -> None:
    """Ghi lượt user (+ assistant nếu có) vào lịch sử. KHÔNG raise (lịch sử phụ)."""
    user_text = " ".join(str(user_query or "").split()).strip()
    if not user_text:
        return
    try:
        async with AsyncSessionLocal() as db:
            repo = DofficeChatRepository(db)
            seq = await repo.next_seq(session_id)
            await repo.append_message(
                session_id=session_id,
                seq=seq,
                role="user",
                content=user_text,
                actor_id_nv=id_nv,
                actor_id_pb=id_pb,
                actor_id_dv=id_dv,
                document_ids=document_ids,
            )
            answer = (assistant_answer or "").strip()
            if answer:
                await repo.append_message(
                    session_id=session_id,
                    seq=seq + 1,
                    role="assistant",
                    content=answer,
                    actor_id_nv=id_nv,
                    actor_id_pb=id_pb,
                    actor_id_dv=id_dv,
                    meta=assistant_meta,
                )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("persist_turn lỗi session_id=%s -> bỏ ghi lịch sử.", session_id, exc_info=True)
