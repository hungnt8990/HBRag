from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class MemoryConfigError(RuntimeError):
    """Raised when a memory provider is used without valid configuration."""


@dataclass(frozen=True)
class MemoryResult:
    content: str
    memory_type: str
    source: str
    id: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryUser(Protocol):
    id: Any
    organization_id: Any


class MemoryProvider(Protocol):
    async def add_memory(
        self,
        *,
        user: MemoryUser,
        content: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryResult:
        """Persist a memory item for the given user."""

    async def search_memory(
        self,
        *,
        user: MemoryUser,
        query: str,
        limit: int,
    ) -> list[MemoryResult]:
        """Return memories relevant to a query for the given user."""

    async def list_memory(
        self,
        *,
        user: MemoryUser,
        limit: int,
        offset: int,
    ) -> list[MemoryResult]:
        """Return stored memories for the given user."""

    async def delete_memory(
        self,
        *,
        user: MemoryUser,
        memory_id: str,
    ) -> bool:
        """Delete or deactivate a memory item owned by the user."""
