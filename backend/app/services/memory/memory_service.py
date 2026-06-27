from __future__ import annotations

import logging

from app.services.memory.memory_base import MemoryProvider, MemoryResult, MemoryUser

logger = logging.getLogger(__name__)

# Conservative trigger phrases (Vietnamese + intent) for auto-saving preferences.
AUTO_SAVE_PHRASES = (
    "nhớ rằng",
    "từ nay",
    "tôi thích",
    "hãy luôn",
    "ưu tiên",
)


def detect_auto_save_memory(message: str) -> tuple[str, str] | None:
    """Return (memory_type, content) if a message should be saved as memory."""
    lowered = message.lower()
    for phrase in AUTO_SAVE_PHRASES:
        if phrase in lowered:
            memory_type = "instruction" if phrase in ("từ nay", "hãy luôn") else "preference"
            return memory_type, message.strip()
    return None


async def gather_memory_context(
    *,
    provider: MemoryProvider,
    user: MemoryUser,
    query: str,
    limit: int,
) -> list[MemoryResult]:
    try:
        return await provider.search_memory(user=user, query=query, limit=limit)
    except Exception:
        logger.warning("Memory search failed; continuing without memory context.", exc_info=True)
        return []


async def maybe_auto_save_memory(
    *,
    provider: MemoryProvider,
    user: MemoryUser,
    message: str,
    session_id: str | None = None,
) -> MemoryResult | None:
    detected = detect_auto_save_memory(message)
    if detected is None:
        return None

    memory_type, content = detected
    metadata: dict[str, object] = {"source": "chat_extracted"}
    if session_id is not None:
        metadata["session_id"] = session_id

    try:
        return await provider.add_memory(
            user=user,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
        )
    except Exception:
        logger.warning("Auto-save memory failed; skipping.", exc_info=True)
        return None
