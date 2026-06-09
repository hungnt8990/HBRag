from __future__ import annotations

from typing import Any
from uuid import UUID

from app.repositories.documents import DocumentRepository
from app.schemas.documents import (
    DocumentVectorIndexResponse,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.embeddings import EmbeddingProvider
from app.services.vector_store import QdrantVectorStore

CONTENT_PREVIEW_LIMIT = 300


class DocumentNotFoundError(LookupError):
    pass


class DocumentVectorIndexStatusError(ValueError):
    pass


class DocumentChunksNotFoundError(ValueError):
    pass


class VectorIndexingError(RuntimeError):
    pass


class VectorSearchError(RuntimeError):
    pass


class VectorIndexingService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        embedding_provider: EmbeddingProvider,
        vector_store: QdrantVectorStore,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store

    async def index_document(self, document_id: UUID) -> DocumentVectorIndexResponse:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        if document.status not in {"chunked", "indexed"}:
            raise DocumentVectorIndexStatusError(
                "Only chunked or indexed documents can be vector indexed."
            )

        chunks = await self._repository.list_chunks_for_document(document_id)
        if not chunks:
            raise DocumentChunksNotFoundError("Document has no chunks to index.")

        try:
            embeddings = await self._embedding_provider.embed_texts(
                [chunk.content for chunk in chunks]
            )
            points = [
                self._vector_store.build_point(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    metadata=dict(chunk.chunk_metadata or {}),
                    vector=embedding,
                    organization_id=getattr(document, "organization_id", None),
                    knowledge_base_id=getattr(document, "knowledge_base_id", None),
                    uploaded_by_user_id=getattr(document, "uploaded_by_user_id", None),
                    visibility=getattr(document, "visibility", None),
                )
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
            await self._vector_store.upsert_chunks(points)
            await self._repository.update_document_status(document, "indexed")
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise VectorIndexingError("Failed to vector index document.") from exc

        return DocumentVectorIndexResponse(
            document_id=document.id,
            status=document.status,
            indexed_chunk_count=len(chunks),
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[str] | None = None,
    ) -> VectorSearchResponse:
        try:
            if document_ids is not None and not document_ids:
                return VectorSearchResponse(query=query, top_k=top_k, results=[])
            query_vector = await self._embedding_provider.embed_query(query)
            results = await self._vector_store.search(
                query_vector=query_vector,
                top_k=top_k,
                document_ids=document_ids,
            )
        except Exception as exc:
            raise VectorSearchError("Failed to run vector search.") from exc

        return VectorSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                VectorSearchResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    score=result.score,
                    content_preview=self._preview(result.content),
                    metadata=self._metadata(result.metadata),
                )
                for result in results
            ],
        )

    @staticmethod
    def _preview(content: str) -> str:
        return content[:CONTENT_PREVIEW_LIMIT]

    @staticmethod
    def _metadata(metadata: dict[str, Any]) -> dict[str, object]:
        return dict(metadata)
