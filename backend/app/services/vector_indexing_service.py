from __future__ import annotations

import inspect
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.schemas.documents import (
    DocumentVectorIndexResponse,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.embeddings import EmbeddingProvider
from app.services.embeddings.sparse import SparseEmbeddingProvider
from app.services.rag_chunk import (
    RagChunk,
    build_embedding_text,
    build_query_embedding_text,
    qdrant_payload,
    rag_chunk_from_database,
    should_index_chunk,
    stable_point_id,
)
from app.services.vector_store import QdrantVectorStore

CONTENT_PREVIEW_LIMIT = 300
logger = logging.getLogger(__name__)


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
        sparse_embedding_provider: SparseEmbeddingProvider | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._sparse_embedding_provider = sparse_embedding_provider

    async def index_document(self, document_id: UUID) -> DocumentVectorIndexResponse:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")

        # Snapshot scalar values immediately after the explicit SELECT. The
        # indexing flow performs commits/rollbacks and must never depend on lazy
        # ORM attribute loading afterwards, especially in an AsyncSession.
        document_status = str(document.status)
        tenant_id = getattr(document, "organization_id", None)
        response_document_id = document.id

        if document_status not in {"chunked", "indexed"}:
            raise DocumentVectorIndexStatusError(
                "Only chunked or indexed documents can be vector indexed."
            )

        try:
            chunks = await self._repository.list_chunks_for_document(document_id)
            if not chunks:
                raise DocumentChunksNotFoundError("Document has no chunks to index.")

            document_file = await self._get_primary_document_file(document_id)
            source_file = str(getattr(document_file, "filename", "document"))
            source_uri = str(getattr(document_file, "storage_path", "")) or None
            rag_chunks = [
                rag_chunk_from_database(
                    chunk,
                    document=document,
                    source_file=source_file,
                    source_uri=source_uri,
                )
                for chunk in chunks
            ]
            indexable_chunks = [chunk for chunk in rag_chunks if should_index_chunk(chunk)]
            if not indexable_chunks:
                raise DocumentChunksNotFoundError(
                    "Document has no indexable chunks after quality and footer filtering."
                )

            embedding_texts = [build_embedding_text(chunk) for chunk in indexable_chunks]
            self._validate_chunks_for_indexing(indexable_chunks)

            await self._mark_index_started(
                document,
                total_chunks=len(rag_chunks),
                indexable_chunks=len(indexable_chunks),
            )
            dense_vectors = await self._embedding_provider.embed_texts(embedding_texts)
            if len(dense_vectors) != len(indexable_chunks):
                raise ValueError(
                    "Dense embedding count does not match the number of indexable chunks."
                )
            for vector in dense_vectors:
                if len(vector) != self._vector_store.vector_size:
                    raise ValueError(
                        "Dense embedding dimension mismatch: "
                        f"got {len(vector)}, expected {self._vector_store.vector_size}."
                    )

            sparse_vectors = None
            if self._sparse_embedding_provider is not None and indexable_chunks:
                sparse_vectors = await self._sparse_embedding_provider.embed_texts(
                    embedding_texts
                )
                if len(sparse_vectors) != len(indexable_chunks):
                    raise ValueError(
                        "Sparse embedding count does not match the number of indexable chunks."
                    )

            points = []
            for index, (chunk, dense_vector) in enumerate(
                zip(indexable_chunks, dense_vectors, strict=True)
            ):
                payload = qdrant_payload(
                    chunk,
                    store_raw_text=settings.store_raw_text_in_qdrant,
                )
                if settings.store_embedding_text_in_qdrant:
                    payload["embedding_text"] = embedding_texts[index]
                sparse_vector = sparse_vectors[index] if sparse_vectors is not None else None
                points.append(
                    self._build_point(
                        chunk=chunk,
                        dense_vector=dense_vector,
                        sparse_vector=sparse_vector,
                        payload=payload,
                    )
                )

            # Delete by primitive identifiers before upsert so stale points from
            # an older chunking version cannot survive a re-index.
            await self._delete_existing_document_points(
                document_id=response_document_id,
                tenant_id=tenant_id,
            )
            await self._vector_store.upsert_chunks(points)
            await self._update_index_metadata(
                document,
                total_chunks=len(rag_chunks),
                indexed_chunks=len(indexable_chunks),
            )
            await self._repository.update_document_status(document, "indexed")
            await self._repository.commit()
        except (
            DocumentChunksNotFoundError,
            DocumentVectorIndexStatusError,
            DocumentNotFoundError,
        ):
            raise
        except Exception as exc:
            await self._repository.rollback()
            # Rollback expires ORM instances. Cleanup and failure persistence use
            # primitive IDs / a freshly loaded object so they cannot trigger
            # SQLAlchemy MissingGreenlet and mask the original indexing error.
            try:
                await self._delete_existing_document_points(
                    document_id=response_document_id,
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.exception(
                    "Failed to clean partial Qdrant points for document=%s",
                    response_document_id,
                )
            await self._mark_index_failed(response_document_id, exc)
            raise VectorIndexingError(
                f"Failed to vector index document: {exc}"
            ) from exc

        logger.info(
            "indexed document=%s total_chunks=%s indexable_chunks=%s collection=%s",
            response_document_id,
            len(rag_chunks),
            len(indexable_chunks),
            self._vector_store.collection_name,
        )
        return DocumentVectorIndexResponse(
            document_id=response_document_id,
            status="indexed",
            indexed_chunk_count=len(indexable_chunks),
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[str] | None = None,
        knowledge_base_ids: set[str] | None = None,
        organization_id: str | None = None,
        tenant_id: str | None = None,
        visibility: str | None = None,
        unit: str | None = None,
        chunk_type: str | None = None,
        table_name: str | None = None,
    ) -> VectorSearchResponse:
        try:
            if document_ids is not None and not document_ids:
                return VectorSearchResponse(query=query, top_k=top_k, results=[])
            query_embedding_text = build_query_embedding_text(query)
            query_vector = await self._embedding_provider.embed_query(query_embedding_text)
            sparse_query = None
            if self._sparse_embedding_provider is not None:
                sparse_query = await self._sparse_embedding_provider.embed_query(
                    query_embedding_text
                )
            results = await self._search_store(
                query_vector=query_vector,
                sparse_query=sparse_query,
                top_k=top_k,
                document_ids=document_ids,
                knowledge_base_ids=knowledge_base_ids,
                organization_id=organization_id,
                tenant_id=tenant_id,
                visibility=visibility,
                unit=unit,
                chunk_type=chunk_type,
                table_name=table_name,
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

    def _build_point(
        self,
        *,
        chunk: RagChunk,
        dense_vector: list[float],
        sparse_vector: Any,
        payload: dict[str, Any],
    ) -> Any:
        parameters = inspect.signature(self._vector_store.build_point).parameters
        if "point_id" in parameters:
            return self._vector_store.build_point(
                point_id=stable_point_id(chunk),
                vector=dense_vector,
                sparse_vector=sparse_vector,
                payload=payload,
            )

        # Backward-compatible adapter for custom/fake stores still using the
        # original build_point contract.
        return self._vector_store.build_point(
            chunk_id=UUID(chunk.database_chunk_id or chunk.chunk_id),
            document_id=UUID(chunk.document_id),
            chunk_index=chunk.chunk_index or 0,
            content=chunk.text,
            metadata={
                key: value
                for key, value in payload.items()
                if key not in {"text", "content", "chunk_id", "document_id"}
            },
            vector=dense_vector,
            organization_id=chunk.organization_id,
            knowledge_base_id=chunk.knowledge_base_id,
            uploaded_by_user_id=chunk.uploaded_by_user_id,
            visibility=chunk.visibility,
        )

    async def _search_store(self, **kwargs: Any) -> list[Any]:
        parameters = inspect.signature(self._vector_store.search).parameters
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        supported = (
            dict(kwargs)
            if accepts_var_kwargs
            else {key: value for key, value in kwargs.items() if key in parameters}
        )
        return await self._vector_store.search(**supported)

    async def _delete_existing_document_points(
        self,
        *,
        document_id: UUID | str,
        tenant_id: UUID | str | None,
    ) -> None:
        delete_method = getattr(self._vector_store, "delete_points_for_document", None)
        if delete_method is None:
            return
        parameters = inspect.signature(delete_method).parameters
        kwargs: dict[str, Any] = {}
        if "tenant_id" in parameters:
            kwargs["tenant_id"] = tenant_id
        await delete_method(document_id, **kwargs)

    async def _get_primary_document_file(self, document_id: UUID) -> Any | None:
        getter = getattr(self._repository, "get_primary_document_file", None)
        if getter is None:
            return None
        return await getter(document_id)

    def _validate_chunks_for_indexing(self, chunks: list[RagChunk]) -> None:
        point_ids: set[str] = set()
        for chunk in chunks:
            if not chunk.document_id or not chunk.chunk_id:
                raise ValueError("Every indexable chunk must have document_id and chunk_id.")
            if not chunk.text.strip():
                raise ValueError(f"Chunk {chunk.chunk_id} has empty text.")
            if (
                chunk.chunker in {"docling_hybrid_v6", "docling_router_v1"}
                and chunk.token_count is not None
                and chunk.token_count > settings.docling_chunk_max_tokens
            ):
                raise ValueError(
                    f"Chunk {chunk.chunk_id} exceeds the Docling hard token limit: "
                    f"{chunk.token_count} > {settings.docling_chunk_max_tokens}."
                )
            if chunk.chunk_type == "administrative_footer":
                raise ValueError(
                    f"Administrative footer {chunk.chunk_id} must not be indexed."
                )
            critical_issues = [
                issue
                for issue in chunk.validation_issues
                if str(issue.get("severity") or "").casefold() == "critical"
            ]
            if critical_issues:
                raise ValueError(
                    f"Chunk {chunk.chunk_id} has critical validation issues: {critical_issues}."
                )
            point_id = stable_point_id(chunk)
            if point_id in point_ids:
                raise ValueError(f"Duplicate stable Qdrant point ID: {point_id}.")
            point_ids.add(point_id)
            json.dumps(
                qdrant_payload(
                    chunk,
                    store_raw_text=settings.store_raw_text_in_qdrant,
                ),
                ensure_ascii=False,
            )

    async def _mark_index_started(
        self,
        document: Any,
        *,
        total_chunks: int,
        indexable_chunks: int,
    ) -> None:
        updater = getattr(self._repository, "update_document_metadata", None)
        if updater is None:
            return
        await updater(
            document,
            {
                "ingestion_status": "indexing",
                "ingestion_started_at": datetime.now(UTC).isoformat(),
                "chunk_count_total": total_chunks,
                "chunk_count_indexable": indexable_chunks,
                "qdrant_collection": self._vector_store.collection_name,
            },
        )

    async def _mark_index_failed(self, document_id: UUID, exc: Exception) -> None:
        updater = getattr(self._repository, "update_document_metadata", None)
        if updater is None:
            return
        try:
            # The previous rollback expires ORM instances. Load a fresh one
            # before updating failure metadata to avoid implicit async IO on an
            # expired attribute.
            document = await self._repository.get_document(document_id)
            if document is None:
                return
            await updater(
                document,
                {
                    "ingestion_status": "index_failed",
                    "ingestion_error": str(exc),
                    "ingestion_completed_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._repository.commit()
        except Exception:
            logger.exception(
                "Failed to persist index failure metadata for document=%s",
                document_id,
            )
            await self._repository.rollback()

    async def _update_index_metadata(
        self,
        document: Any,
        *,
        total_chunks: int,
        indexed_chunks: int,
    ) -> None:
        updater = getattr(self._repository, "update_document_metadata", None)
        if updater is None:
            return
        await updater(
            document,
            {
                "chunk_count_total": total_chunks,
                "chunk_count_indexed": indexed_chunks,
                "qdrant_collection": self._vector_store.collection_name,
                "ingestion_status": "indexed",
                "ingestion_completed_at": datetime.now(UTC).isoformat(),
                "dense_vector_name": self._vector_store.dense_vector_name,
                "sparse_vector_name": (
                    self._vector_store.sparse_vector_name
                    if self._sparse_embedding_provider is not None
                    else None
                ),
            },
        )

    @staticmethod
    def _preview(content: str) -> str:
        return content[:CONTENT_PREVIEW_LIMIT]

    @staticmethod
    def _metadata(metadata: dict[str, Any]) -> dict[str, object]:
        return dict(metadata)
