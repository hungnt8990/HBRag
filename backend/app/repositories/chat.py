from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, or_, select
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

    async def get_entity_coverage_chunks(
        self,
        *,
        document_id: UUID,
        search_terms: Sequence[str],
        exclude_ids: Sequence[UUID] = (),
        max_matches: int = 50,
    ) -> list[Chunk]:
        normalized_terms = [
            " ".join(term.split()).strip()
            for term in search_terms
            if " ".join(term.split()).strip()
        ]
        if not normalized_terms:
            return []

        content_clauses = [
            Chunk.content.ilike(f"%{term}%")
            for term in normalized_terms
        ]

        # Person names extracted from PDFs/tables may be split by newlines,
        # duplicated spaces, or OCR layout artifacts (for example
        # "Nguyễn\nQuang Lâm"). A single ILIKE('%Nguyễn Quang Lâm%') then
        # misses the exact row even though every name token is present in the
        # same TABLE_ROW/table_block. Add conservative token-AND fallbacks for
        # multi-word terms so entity coverage can recover those rows without
        # relying on neighboring-row inference.
        token_group_clauses = []
        for term in normalized_terms:
            tokens = [
                token
                for token in term.replace(".", " ").split()
                if len(token.strip()) >= 2
            ]
            if len(tokens) >= 2:
                token_group_clauses.append(
                    and_(*(Chunk.content.ilike(f"%{token}%") for token in tokens))
                )
        content_clauses.extend(token_group_clauses)
        chunk_type = Chunk.chunk_metadata["chunk_type"].astext
        chunk_type_priority = case(
            # Narrative/docling chunks often contain the actual objective/description
            # sections (Mục tiêu, Công nghệ, Tính năng...), so keep them searchable
            # and do not let staff table rows hide them for technology-area detail QA.
            (chunk_type == "docling_hybrid_repaired", 0),
            (chunk_type == "text", 1),
            (chunk_type == "docling_text", 2),
            (chunk_type == "docling_section", 3),
            (chunk_type == "table_block", 4),
            (chunk_type == "table_complete", 5),
            (chunk_type == "table_rows", 6),
            (chunk_type == "table_row", 7),
            (chunk_type == "legal_table_row", 8),
            (chunk_type == "structured_fact_row", 9),
            (chunk_type == "entity_profile", 10),
            (chunk_type == "entity_summary", 11),
            else_=12,
        )
        statement = (
            select(Chunk)
            .where(
                Chunk.document_id == document_id,
                chunk_type.in_([
                    "docling_hybrid_repaired",
                    "docling_text",
                    "docling_section",
                    "text",
                    "table_block",
                    "table_complete",
                    "table_rows",
                    "table_row",
                    "legal_table_row",
                    "structured_fact_row",
                    "entity_profile",
                    "entity_summary",
                ]),
                or_(*content_clauses),
            )
            .order_by(chunk_type_priority, Chunk.chunk_index)
            .limit(max_matches)
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
