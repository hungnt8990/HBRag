from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.memory import UserMemory
from app.repositories.memory import MemoryRepository
from app.services.memory.base import MemoryProvider, MemoryResult, MemoryUser


class LocalMemoryProvider(MemoryProvider):
    source = "manual"

    def __init__(self, *, repository: MemoryRepository) -> None:
        self._repository = repository

    async def add_memory(
        self,
        *,
        user: MemoryUser,
        content: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
        source: str = "manual",
    ) -> MemoryResult:
        memory = await self._repository.create_memory(
            user_id=user.id,
            organization_id=getattr(user, "organization_id", None),
            content=content,
            memory_type=memory_type,
            source=source,
            metadata=metadata,
        )
        await self._repository.commit()
        return self._to_result(memory)

    async def search_memory(
        self,
        *,
        user: MemoryUser,
        query: str,
        limit: int,
    ) -> list[MemoryResult]:
        memories = await self._repository.search_memories(
            user_id=user.id,
            query=query,
            limit=limit,
        )
        return [self._to_result(memory) for memory in memories]

    async def list_memory(
        self,
        *,
        user: MemoryUser,
        limit: int,
        offset: int,
    ) -> list[MemoryResult]:
        memories = await self._repository.list_memories(
            user_id=user.id,
            limit=limit,
            offset=offset,
        )
        return [self._to_result(memory) for memory in memories]

    async def delete_memory(
        self,
        *,
        user: MemoryUser,
        memory_id: str,
    ) -> bool:
        try:
            parsed_id = UUID(str(memory_id))
        except ValueError:
            return False

        memory = await self._repository.get_memory_for_user(
            user_id=user.id,
            memory_id=parsed_id,
        )
        if memory is None:
            return False
        await self._repository.deactivate_memory(memory)
        await self._repository.commit()
        return True

    @staticmethod
    def _to_result(memory: UserMemory) -> MemoryResult:
        return MemoryResult(
            id=str(memory.id),
            content=memory.content,
            memory_type=memory.memory_type,
            source=memory.source,
            score=memory.confidence,
            metadata=dict(memory.memory_metadata or {}),
        )
