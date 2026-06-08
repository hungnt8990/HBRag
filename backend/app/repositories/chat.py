from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat import ChatMessage, ChatSession
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document


@dataclass(frozen=True)
class CitationCreate:
    chunk_id: UUID
    document_id: UUID
    quote: str | None = None
    page_number: int | None = None


class ChatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(self, *, title: str) -> ChatSession:
        session = ChatSession(title=title)
        self._session.add(session)
        await self._session.flush()
        return session

    async def get_session(self, session_id: UUID) -> ChatSession | None:
        return await self._session.get(ChatSession, session_id)

    async def create_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str,
    ) -> ChatMessage:
        message = ChatMessage(session_id=session_id, role=role, content=content)
        self._session.add(message)
        await self._session.flush()
        return message

    async def get_chunks_by_ids(self, chunk_ids: Sequence[UUID]) -> list[Chunk]:
        if not chunk_ids:
            return []

        statement = (
            select(Chunk)
            .options(selectinload(Chunk.document).selectinload(Document.files))
            .where(Chunk.id.in_(chunk_ids))
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_neighbor_chunks(
        self,
        *,
        document_id: UUID,
        article_number: str,
        exclude_ids: Sequence[UUID] = (),
    ) -> list[Chunk]:
        statement = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_metadata["article_number"].astext == article_number,
            )
            .order_by(Chunk.chunk_index)
        )
        if exclude_ids:
            statement = statement.where(Chunk.id.notin_(list(exclude_ids)))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_table_chunks(
        self,
        *,
        document_id: UUID,
        table_id: str,
        exclude_ids: Sequence[UUID] = (),
    ) -> list[Chunk]:
        statement = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_metadata["table_id"].astext == table_id,
            )
            .order_by(Chunk.chunk_index)
        )
        if exclude_ids:
            statement = statement.where(Chunk.id.notin_(list(exclude_ids)))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def create_citations(
        self,
        *,
        message_id: UUID,
        citations: Sequence[CitationCreate],
    ) -> list[Citation]:
        citation_models = [
            Citation(
                message_id=message_id,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                quote=citation.quote,
                page_number=citation.page_number,
            )
            for citation in citations
        ]
        self._session.add_all(citation_models)
        await self._session.flush()
        return citation_models

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


def metadata_to_dict(metadata: dict[str, Any] | None) -> dict[str, object]:
    return dict(metadata or {})
