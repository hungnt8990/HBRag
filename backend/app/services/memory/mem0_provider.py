from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.memory.base import MemoryConfigError, MemoryProvider, MemoryResult, MemoryUser

logger = logging.getLogger(__name__)


class Mem0Provider(MemoryProvider):
    source = "mem0"

    def __init__(
        self,
        *,
        mode: str = "oss",
        api_key: str | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        user_prefix: str = "hbrag",
        client: Any | None = None,
    ) -> None:
        self._mode = mode
        self._api_key = api_key
        self._org_id = org_id
        self._project_id = project_id
        self._user_prefix = user_prefix
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            if self._mode == "platform":
                from mem0 import MemoryClient
            else:
                from mem0 import Memory
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise MemoryConfigError(
                "mem0ai is not installed. Install mem0ai to use the Mem0 memory provider."
            ) from exc

        if self._mode == "platform":
            if not self._api_key:
                raise MemoryConfigError(
                    "MEM0_API_KEY is required for the Mem0 platform mode."
                )
            kwargs: dict[str, Any] = {"api_key": self._api_key}
            if self._org_id:
                kwargs["org_id"] = self._org_id
            if self._project_id:
                kwargs["project_id"] = self._project_id
            self._client = MemoryClient(**kwargs)
        else:
            self._client = Memory()
        return self._client

    def _mem0_user_id(self, user: MemoryUser) -> str:
        return f"{self._user_prefix}:{user.id}"

    def _build_metadata(
        self,
        *,
        user: MemoryUser,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = dict(metadata or {})
        payload.setdefault("hbrag_user_id", str(user.id))
        organization_id = getattr(user, "organization_id", None)
        if organization_id is not None:
            payload.setdefault("organization_id", str(organization_id))
        payload.setdefault("source", self.source)
        return payload

    async def add_memory(
        self,
        *,
        user: MemoryUser,
        content: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryResult:
        client = self._get_client()
        mem0_user_id = self._mem0_user_id(user)
        payload = self._build_metadata(user=user, metadata=metadata)
        payload.setdefault("memory_type", memory_type)
        messages = [{"role": "user", "content": content}]

        response = await asyncio.to_thread(
            client.add,
            messages,
            user_id=mem0_user_id,
            metadata=payload,
        )
        memory_id = self._extract_first_id(response)
        return MemoryResult(
            id=memory_id,
            content=content,
            memory_type=memory_type,
            source=self.source,
            metadata=payload,
        )

    async def search_memory(
        self,
        *,
        user: MemoryUser,
        query: str,
        limit: int,
    ) -> list[MemoryResult]:
        client = self._get_client()
        response = await asyncio.to_thread(
            client.search,
            query,
            user_id=self._mem0_user_id(user),
            limit=limit,
        )
        return self._to_results(response)

    async def list_memory(
        self,
        *,
        user: MemoryUser,
        limit: int,
        offset: int,
    ) -> list[MemoryResult]:
        client = self._get_client()
        response = await asyncio.to_thread(
            client.get_all,
            user_id=self._mem0_user_id(user),
        )
        return self._to_results(response)[offset : offset + limit]

    async def delete_memory(
        self,
        *,
        user: MemoryUser,
        memory_id: str,
    ) -> bool:
        client = self._get_client()
        try:
            await asyncio.to_thread(client.delete, memory_id=memory_id)
        except Exception:
            logger.warning("Failed to delete Mem0 memory %s", memory_id, exc_info=True)
            return False
        return True

    @staticmethod
    def _iter_items(response: Any) -> list[dict[str, Any]]:
        if isinstance(response, dict):
            results = response.get("results", response.get("memories", []))
        else:
            results = response
        if not isinstance(results, list):
            return []
        return [item for item in results if isinstance(item, dict)]

    @classmethod
    def _extract_first_id(cls, response: Any) -> str | None:
        items = cls._iter_items(response)
        if not items:
            return None
        value = items[0].get("id")
        return str(value) if value is not None else None

    @classmethod
    def _to_results(cls, response: Any) -> list[MemoryResult]:
        results: list[MemoryResult] = []
        for item in cls._iter_items(response):
            content = item.get("memory") or item.get("content") or ""
            if not content:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            memory_type = metadata.get("memory_type", "fact") if metadata else "fact"
            results.append(
                MemoryResult(
                    id=str(item["id"]) if item.get("id") is not None else None,
                    content=str(content),
                    memory_type=str(memory_type),
                    source="mem0",
                    score=item.get("score"),
                    metadata=dict(metadata or {}),
                )
            )
        return results
