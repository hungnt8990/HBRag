from __future__ import annotations

import logging
from uuid import UUID

from app.repositories.memory import MemoryRepository
from app.services.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You summarize a chat conversation into a concise paragraph capturing the user's "
    "goals, decisions, and stable preferences. Do not invent facts."
)


class SessionSummaryService:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        llm_provider: LLMGateway,
        every_n_messages: int = 10,
    ) -> None:
        self._repository = repository
        self._llm_provider = llm_provider
        self._every_n_messages = max(1, every_n_messages)

    def should_summarize(self, message_count: int) -> bool:
        return message_count > 0 and message_count % self._every_n_messages == 0

    async def maybe_summarize(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        message_count: int,
        transcript: str,
        last_message_id: UUID | None = None,
    ) -> str | None:
        if not self.should_summarize(message_count):
            return None
        return await self.summarize(
            session_id=session_id,
            user_id=user_id,
            transcript=transcript,
            last_message_id=last_message_id,
        )

    async def summarize(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        transcript: str,
        last_message_id: UUID | None = None,
    ) -> str | None:
        try:
            summary = await self._llm_provider.generate(
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                user_prompt=f"Conversation:\n{transcript}\n\nSummary:",
            )
        except Exception:
            logger.warning("Session summary generation failed; skipping.", exc_info=True)
            return None

        try:
            await self._repository.upsert_session_summary(
                session_id=session_id,
                user_id=user_id,
                summary=summary,
                last_message_id=last_message_id,
            )
            await self._repository.commit()
        except Exception:
            await self._repository.rollback()
            logger.warning("Failed to persist session summary.", exc_info=True)
            return None
        return summary
