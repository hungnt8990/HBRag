from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import SessionSummary, UserMemory


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_memory(
        self,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        content: str,
        memory_type: str,
        source: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> UserMemory:
        memory = UserMemory(
            user_id=user_id,
            organization_id=organization_id,
            content=content,
            memory_type=memory_type,
            source=source,
            confidence=confidence,
            is_active=True,
            memory_metadata=metadata,
        )
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def list_memories(
        self,
        *,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[UserMemory]:
        statement = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.is_active.is_(True))
            .order_by(UserMemory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def search_memories(
        self,
        *,
        user_id: UUID,
        query: str,
        limit: int = 5,
    ) -> list[UserMemory]:
        statement = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.is_active.is_(True))
            .order_by(UserMemory.created_at.desc())
        )
        if query.strip():
            statement = statement.where(UserMemory.content.ilike(f"%{query.strip()}%"))
        statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_memory_for_user(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
    ) -> UserMemory | None:
        statement = select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def deactivate_memory(self, memory: UserMemory) -> None:
        memory.is_active = False
        await self._session.flush()

    async def get_session_summary(self, *, session_id: UUID) -> SessionSummary | None:
        statement = select(SessionSummary).where(SessionSummary.session_id == session_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert_session_summary(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        summary: str,
        last_message_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionSummary:
        existing = await self.get_session_summary(session_id=session_id)
        if existing is None:
            existing = SessionSummary(
                session_id=session_id,
                user_id=user_id,
                summary=summary,
                last_message_id=last_message_id,
                summary_metadata=metadata,
            )
            self._session.add(existing)
        else:
            existing.summary = summary
            existing.last_message_id = last_message_id
            existing.summary_metadata = metadata
        await self._session.flush()
        return existing

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
