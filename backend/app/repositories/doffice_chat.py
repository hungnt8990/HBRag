"""Ghi/đọc lịch sử hội thoại ``/chat`` (1 bảng ``doffice_chat_messages``, nhóm theo ``session_id``)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doffice_chat import DofficeChatMessage


class DofficeChatRepository:
    """Thao tác message của luồng chat DOffice (không có bảng session riêng)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recent_messages(
        self, session_id: UUID, *, limit: int
    ) -> list[DofficeChatMessage]:
        """``limit`` message MỚI nhất (theo ``seq``), trả về theo thứ tự TĂNG dần (cũ -> mới).

        Vì lấy nhóm mới nhất, phần tử CUỐI = lượt mới nhất của hội thoại (dùng để check ngưỡng 4h
        và xác định chủ hội thoại).
        """
        stmt = (
            select(DofficeChatMessage)
            .where(DofficeChatMessage.session_id == session_id)
            .order_by(DofficeChatMessage.seq.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        rows.reverse()
        return rows

    async def next_seq(self, session_id: UUID) -> int:
        """``seq`` kế tiếp để ghi (MAX(seq)+1; hội thoại rỗng -> 0)."""
        stmt = select(func.max(DofficeChatMessage.seq)).where(
            DofficeChatMessage.session_id == session_id
        )
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return 0 if current is None else int(current) + 1

    async def append_message(
        self,
        *,
        session_id: UUID,
        seq: int,
        role: str,
        content: str,
        actor_id_nv: int | None = None,
        actor_id_pb: int | None = None,
        actor_id_dv: int | None = None,
        document_ids: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> DofficeChatMessage:
        msg = DofficeChatMessage(
            session_id=session_id,
            seq=seq,
            role=role,
            content=content,
            actor_id_nv=actor_id_nv,
            actor_id_pb=actor_id_pb,
            actor_id_dv=actor_id_dv,
            document_ids=document_ids,
            meta=meta,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def commit(self) -> None:
        await self._session.commit()
