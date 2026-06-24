from __future__ import annotations

import logging
from typing import Any

from app.services.memory.memory_base import MemoryProvider, MemoryResult, MemoryUser
from app.services.memory.memory_local_memory_provider import LocalMemoryProvider
from app.services.memory.memory_mem0_provider import Mem0Provider

logger = logging.getLogger(__name__)


class HybridMemoryProvider(MemoryProvider):
    def __init__(
        self,
        *,
        local_provider: LocalMemoryProvider,
        mem0_provider: Mem0Provider | None,
        top_k: int = 5,
    ) -> None:
        self._local = local_provider
        self._mem0 = mem0_provider
        self._top_k = top_k

    async def add_memory(
        self,
        *,
        user: MemoryUser,
        content: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryResult:
        result = await self._local.add_memory(
            user=user,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
        )
        if self._mem0 is not None:
            try:
                await self._mem0.add_memory(
                    user=user,
                    content=content,
                    memory_type=memory_type,
                    metadata=metadata,
                )
            except Exception:
                logger.warning(
                    "Mem0 add_memory failed; local memory was still saved.",
                    exc_info=True,
                )
        return result

    async def search_memory(
        self,
        *,
        user: MemoryUser,
        query: str,
        limit: int,
    ) -> list[MemoryResult]:
        merged: list[MemoryResult] = list(
            await self._local.search_memory(user=user, query=query, limit=limit)
        )
        if self._mem0 is not None:
            try:
                merged.extend(
                    await self._mem0.search_memory(user=user, query=query, limit=limit)
                )
            except Exception:
                logger.warning("Mem0 search_memory failed; using local results.", exc_info=True)

        deduplicated = self._deduplicate(merged)
        effective_limit = limit or self._top_k
        return deduplicated[:effective_limit]

    async def list_memory(
        self,
        *,
        user: MemoryUser,
        limit: int,
        offset: int,
    ) -> list[MemoryResult]:
        return await self._local.list_memory(user=user, limit=limit, offset=offset)

    async def delete_memory(
        self,
        *,
        user: MemoryUser,
        memory_id: str,
    ) -> bool:
        return await self._local.delete_memory(user=user, memory_id=memory_id)

    @staticmethod
    def _deduplicate(results: list[MemoryResult]) -> list[MemoryResult]:
        seen: set[str] = set()
        unique: list[MemoryResult] = []
        for result in results:
            key = result.content.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(result)
        return unique
