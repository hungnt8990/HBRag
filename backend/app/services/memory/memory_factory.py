from __future__ import annotations

from app.core.config import settings
from app.repositories.memory import MemoryRepository
from app.services.memory.memory_base import MemoryProvider
from app.services.memory.memory_hybrid_memory_provider import HybridMemoryProvider
from app.services.memory.memory_local_memory_provider import LocalMemoryProvider
from app.services.memory.memory_mem0_provider import Mem0Provider


def build_mem0_provider() -> Mem0Provider:
    return Mem0Provider(
        mode=settings.mem0_mode,
        api_key=settings.mem0_api_key,
        org_id=settings.mem0_org_id,
        project_id=settings.mem0_project_id,
        user_prefix=settings.mem0_user_prefix,
    )


def build_memory_provider(
    repository: MemoryRepository,
    *,
    use_mem0: bool | None = None,
) -> MemoryProvider:
    provider = settings.memory_provider.lower().strip()
    local_provider = LocalMemoryProvider(repository=repository)

    if provider == "local":
        return local_provider

    if provider == "mem0":
        return build_mem0_provider()

    if provider == "hybrid":
        mem0_enabled = settings.mem0_enabled if use_mem0 is None else use_mem0
        mem0_provider = build_mem0_provider() if mem0_enabled else None
        return HybridMemoryProvider(
            local_provider=local_provider,
            mem0_provider=mem0_provider,
            top_k=settings.memory_top_k,
        )

    raise ValueError(f"Unsupported memory provider: {settings.memory_provider}")
