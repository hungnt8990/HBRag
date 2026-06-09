from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, cast, delete, func, literal_column, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chunk import Chunk
from app.models.document import Document, DocumentFile
from app.models.document_log import DocumentPipelineLog
from app.models.graph import GraphDocumentStatus
from app.models.knowledge_base import KnowledgeBase


@dataclass(frozen=True)
class ChunkCreate:
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    token_count: int | None = None


@dataclass(frozen=True)
class DocumentListRow:
    document: Document
    filename: str | None
    chunk_count: int
    parsed_character_count: int
    vector_indexed_count: int | None
    pipeline_logs_count: int
    graph_indexed: bool


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_document(
        self,
        *,
        title: str,
        source_type: str,
        status: str = "uploaded",
        uploaded_by_user_id: UUID | None = None,
        organization_id: UUID | None = None,
        knowledge_base_id: UUID | None = None,
        visibility: str = "organization",
    ) -> Document:
        document = Document(
            title=title,
            source_type=source_type,
            status=status,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            visibility=visibility,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def create_document_file(
        self,
        *,
        document_id: UUID,
        filename: str,
        mime_type: str,
        storage_path: str,
        file_size: int,
    ) -> DocumentFile:
        document_file = DocumentFile(
            document_id=document_id,
            filename=filename,
            mime_type=mime_type,
            storage_path=storage_path,
            file_size=file_size,
        )
        self._session.add(document_file)
        await self._session.flush()
        return document_file

    async def get_document(self, document_id: UUID) -> Document | None:
        statement = (
            select(Document)
            .options(
                selectinload(Document.files),
                selectinload(Document.organization),
                selectinload(Document.uploaded_by),
                selectinload(Document.knowledge_base),
                selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owner),
                selectinload(Document.knowledge_base).selectinload(
                    KnowledgeBase.organization
                ),
            )
            .where(Document.id == document_id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_primary_document_file(self, document_id: UUID) -> DocumentFile | None:
        statement = (
            select(DocumentFile)
            .where(DocumentFile.document_id == document_id)
            .order_by(DocumentFile.created_at.asc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def find_document_file_by_signature(
        self,
        *,
        filename: str,
        file_size: int,
    ) -> DocumentFile | None:
        statement = (
            select(DocumentFile)
            .options(selectinload(DocumentFile.document))
            .where(
                DocumentFile.filename == filename,
                DocumentFile.file_size == file_size,
            )
            .order_by(DocumentFile.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def update_document_status(self, document: Document, status: str) -> Document:
        document.status = status
        await self._session.flush()
        return document

    async def update_document_parsed_content(
        self,
        document: Document,
        *,
        parsed_text: str,
        parsed_at: datetime,
        status: str = "parsed",
    ) -> Document:
        document.parsed_text = parsed_text
        document.parsed_at = parsed_at
        document.status = status
        await self._session.flush()
        return document

    async def delete_document(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def delete_chunks_for_document(self, document_id: UUID) -> None:
        await self._session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        await self._session.flush()

    async def create_chunks(
        self,
        *,
        document_id: UUID,
        chunks: Sequence[ChunkCreate],
    ) -> list[Chunk]:
        chunk_models = [
            Chunk(
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                chunk_metadata=chunk.metadata,
            )
            for chunk in chunks
        ]
        self._session.add_all(chunk_models)
        await self._session.flush()

        chunk_ids = [chunk.id for chunk in chunk_models]
        if chunk_ids:
            await self._session.execute(
                update(Chunk)
                .where(Chunk.id.in_(chunk_ids))
                .values(search_vector=func.to_tsvector(literal_column("'simple'"), Chunk.content))
            )
            await self._session.flush()

        return chunk_models

    async def list_chunks_for_document(self, document_id: UUID) -> list[Chunk]:
        statement = (
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index.asc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_chunks_by_ids(self, chunk_ids: Sequence[UUID]) -> list[Chunk]:
        if not chunk_ids:
            return []

        statement = select(Chunk).where(Chunk.id.in_(chunk_ids))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_documents(
        self,
        *,
        visible_document_ids: set[UUID] | None,
        organization_ids: set[UUID] | None = None,
        status: str | None = None,
        uploaded_by: UUID | None = None,
        knowledge_base_ids: set[UUID] | None = None,
        search: str | None = None,
        limit: int | None = 50,
        offset: int = 0,
    ) -> list[DocumentListRow]:
        chunk_counts = (
            select(
                Chunk.document_id.label("document_id"),
                func.count(Chunk.id).label("chunk_count"),
            )
            .group_by(Chunk.document_id)
            .subquery()
        )
        pipeline_counts = (
            select(
                DocumentPipelineLog.document_id.label("document_id"),
                func.count(DocumentPipelineLog.id).label("pipeline_logs_count"),
            )
            .group_by(DocumentPipelineLog.document_id)
            .subquery()
        )
        vector_counts = (
            select(
                DocumentPipelineLog.document_id.label("document_id"),
                func.max(
                    cast(
                        DocumentPipelineLog.log_metadata["indexed_chunk_count"].astext,
                        Integer,
                    )
                ).label("vector_indexed_count"),
            )
            .where(
                DocumentPipelineLog.action == "index_vector",
                DocumentPipelineLog.status == "success",
            )
            .group_by(DocumentPipelineLog.document_id)
            .subquery()
        )
        graph_status = (
            select(
                GraphDocumentStatus.document_id.label("document_id"),
                func.bool_or(GraphDocumentStatus.graph_indexed).label("graph_indexed"),
            )
            .group_by(GraphDocumentStatus.document_id)
            .subquery()
        )
        statement = (
            select(
                Document,
                func.min(DocumentFile.filename).label("filename"),
                func.coalesce(chunk_counts.c.chunk_count, 0).label("chunk_count"),
                func.coalesce(func.length(Document.parsed_text), 0).label(
                    "parsed_character_count"
                ),
                vector_counts.c.vector_indexed_count.label("vector_indexed_count"),
                func.coalesce(pipeline_counts.c.pipeline_logs_count, 0).label(
                    "pipeline_logs_count"
                ),
                func.coalesce(graph_status.c.graph_indexed, False).label("graph_indexed"),
            )
            .outerjoin(DocumentFile, DocumentFile.document_id == Document.id)
            .outerjoin(chunk_counts, chunk_counts.c.document_id == Document.id)
            .outerjoin(pipeline_counts, pipeline_counts.c.document_id == Document.id)
            .outerjoin(vector_counts, vector_counts.c.document_id == Document.id)
            .outerjoin(graph_status, graph_status.c.document_id == Document.id)
            .options(
                selectinload(Document.organization),
                selectinload(Document.uploaded_by),
                selectinload(Document.knowledge_base),
                selectinload(Document.knowledge_base).selectinload(KnowledgeBase.owner),
                selectinload(Document.knowledge_base).selectinload(
                    KnowledgeBase.organization
                ),
            )
            .group_by(
                Document.id,
                chunk_counts.c.chunk_count,
                vector_counts.c.vector_indexed_count,
                pipeline_counts.c.pipeline_logs_count,
                graph_status.c.graph_indexed,
            )
            .order_by(Document.updated_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        if visible_document_ids is not None:
            if not visible_document_ids:
                return []
            statement = statement.where(Document.id.in_(visible_document_ids))
        if organization_ids is not None:
            if not organization_ids:
                return []
            statement = statement.where(Document.organization_id.in_(organization_ids))
        if status:
            statement = statement.where(Document.status == status)
        if uploaded_by:
            statement = statement.where(Document.uploaded_by_user_id == uploaded_by)
        if knowledge_base_ids is not None:
            if not knowledge_base_ids:
                return []
            statement = statement.where(Document.knowledge_base_id.in_(knowledge_base_ids))
        if search:
            search_value = f"%{search}%"
            statement = statement.where(
                or_(Document.title.ilike(search_value), DocumentFile.filename.ilike(search_value))
            )

        result = await self._session.execute(statement)
        return [
            DocumentListRow(
                document=row[0],
                filename=row[1],
                chunk_count=int(row[2] or 0),
                parsed_character_count=int(row[3] or 0),
                vector_indexed_count=(int(row[4]) if row[4] is not None else None),
                pipeline_logs_count=int(row[5] or 0),
                graph_indexed=bool(row[6]),
            )
            for row in result.all()
        ]

    async def count_chunks_for_document(self, document_id: UUID) -> int:
        statement = select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def list_documents_for_permission_check(
        self,
        *,
        knowledge_base_ids: set[UUID] | None = None,
    ) -> list[Document]:
        if knowledge_base_ids is not None and not knowledge_base_ids:
            return []
        statement = select(Document).options(
            selectinload(Document.organization),
            selectinload(Document.uploaded_by),
            selectinload(Document.knowledge_base),
        )
        if knowledge_base_ids is not None:
            statement = statement.where(Document.knowledge_base_id.in_(knowledge_base_ids))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
