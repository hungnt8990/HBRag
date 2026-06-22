from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.models.knowledge_artifact import KnowledgeArtifact
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.services.embeddings import EmbeddingProvider
from app.services.embeddings.sparse import SparseEmbeddingProvider
from app.services.vector_store import QdrantVectorStore


@dataclass(frozen=True)
class KnowledgeArtifactIndexResponse:
    document_id: UUID
    status: str
    indexed_artifact_count: int


@dataclass(frozen=True)
class KnowledgeArtifactVectorHit:
    artifact_id: UUID
    document_id: UUID
    score: float
    content_preview: str
    metadata: dict[str, Any]


class KnowledgeArtifactIndexingService:
    def __init__(
        self,
        *,
        repository: KnowledgeArtifactRepository,
        embedding_provider: EmbeddingProvider,
        vector_store: QdrantVectorStore,
        sparse_embedding_provider: SparseEmbeddingProvider | None = None,
    ) -> None:
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._sparse_embedding_provider = sparse_embedding_provider

    async def index_document(self, document_id: UUID) -> KnowledgeArtifactIndexResponse:
        artifacts = await self._repository.list_for_document(document_id, statuses={"ready"})
        if not artifacts:
            await self._delete_existing_document_points(document_id=document_id)
            await self._repository.update_embedding_status_for_document(document_id, "skipped")
            return KnowledgeArtifactIndexResponse(
                document_id=document_id,
                status="skipped",
                indexed_artifact_count=0,
            )

        await self._repository.update_embedding_status_for_document(document_id, "indexing")
        try:
            embedding_texts = [artifact.canonical_text for artifact in artifacts]
            dense_vectors = await self._embedding_provider.embed_texts(embedding_texts)
            if len(dense_vectors) != len(artifacts):
                raise ValueError("Dense embedding count does not match the number of artifacts.")
            for vector in dense_vectors:
                if len(vector) != self._vector_store.vector_size:
                    raise ValueError(
                        "Dense embedding dimension mismatch: "
                        f"got {len(vector)}, expected {self._vector_store.vector_size}."
                    )

            sparse_vectors = None
            if self._sparse_embedding_provider is not None:
                sparse_vectors = await self._sparse_embedding_provider.embed_texts(embedding_texts)
                if len(sparse_vectors) != len(artifacts):
                    raise ValueError("Sparse embedding count does not match the number of artifacts.")

            points = []
            for index, (artifact, dense_vector) in enumerate(zip(artifacts, dense_vectors, strict=True)):
                payload = artifact_qdrant_payload(artifact)
                sparse_vector = sparse_vectors[index] if sparse_vectors is not None else None
                points.append(
                    self._vector_store.build_point(
                        point_id=stable_artifact_point_id(artifact),
                        vector=dense_vector,
                        sparse_vector=sparse_vector,
                        payload=payload,
                    )
                )

            await self._delete_existing_document_points(document_id=document_id)
            await self._vector_store.upsert_chunks(points)
            await self._repository.update_embedding_status_for_document(document_id, "indexed")
        except Exception:
            await self._repository.update_embedding_status_for_document(document_id, "failed")
            raise

        return KnowledgeArtifactIndexResponse(
            document_id=document_id,
            status="indexed",
            indexed_artifact_count=len(points),
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
    ) -> list[KnowledgeArtifactVectorHit]:
        if document_ids is not None and not document_ids:
            return []
        query_vector = await self._embedding_provider.embed_query(query)
        sparse_query = None
        if self._sparse_embedding_provider is not None:
            sparse_query = await self._sparse_embedding_provider.embed_query(query)
        results = await self._vector_store.search(
            query_vector=query_vector,
            sparse_query=sparse_query,
            top_k=top_k,
            document_ids={str(document_id) for document_id in document_ids} if document_ids is not None else None,
        )
        hits: list[KnowledgeArtifactVectorHit] = []
        for result in results:
            artifact_id = result.metadata.get("artifact_id") or result.chunk_id
            try:
                parsed_artifact_id = UUID(str(artifact_id))
                parsed_document_id = UUID(str(result.document_id))
            except (TypeError, ValueError):
                continue
            hits.append(
                KnowledgeArtifactVectorHit(
                    artifact_id=parsed_artifact_id,
                    document_id=parsed_document_id,
                    score=result.score,
                    content_preview=result.content,
                    metadata=dict(result.metadata),
                )
            )
        return hits

    async def _delete_existing_document_points(self, *, document_id: UUID) -> None:
        delete_method = getattr(self._vector_store, "delete_points_for_document", None)
        if delete_method is None:
            return
        parameters = inspect.signature(delete_method).parameters
        kwargs: dict[str, Any] = {}
        if "tenant_id" in parameters:
            kwargs["tenant_id"] = None
        await delete_method(document_id, **kwargs)


def stable_artifact_point_id(artifact: KnowledgeArtifact) -> str:
    identity = ":".join(
        [
            "artifact",
            str(artifact.document_id),
            str(artifact.id),
            hashlib.sha1((artifact.canonical_text or "").encode("utf-8")).hexdigest(),
        ]
    )
    return str(uuid5(NAMESPACE_URL, identity))


def artifact_qdrant_payload(artifact: KnowledgeArtifact) -> dict[str, Any]:
    identifiers = _flatten_identifier_values(artifact.normalized_identifiers or {})
    source_chunk_ids = [str(chunk_id) for chunk_id in list(artifact.source_chunk_ids or [])]
    evidence_chunk_ids = [
        str(chunk_id)
        for chunk_id in list(getattr(artifact, "evidence_chunk_ids", None) or source_chunk_ids)
    ]
    idea_metadata = dict(getattr(artifact, "idea_metadata", None) or {})
    idea_block_type = getattr(artifact, "idea_block_type", None) or artifact.artifact_type
    return {
        "item_type": "idea_block",
        "source_layer": "idea_block",
        "chunk_id": str(artifact.id),
        "artifact_id": str(artifact.id),
        "idea_block_id": str(artifact.id),
        "document_id": str(artifact.document_id),
        "artifact_type": artifact.artifact_type,
        "idea_block_type": idea_block_type,
        "chunk_type": artifact.artifact_type,
        "context_type": artifact.context_type,
        "status": artifact.status,
        "source_chunk_ids": source_chunk_ids,
        "evidence_chunk_ids": evidence_chunk_ids,
        "title": artifact.title,
        "text": artifact.canonical_text,
        "content": artifact.canonical_text,
        "canonical_text": artifact.canonical_text,
        "summary_text": getattr(artifact, "summary_text", None),
        "metadata": idea_metadata,
        "structured_data": artifact.structured_data or {},
        "normalized_identifiers": artifact.normalized_identifiers or {},
        "citation_map": artifact.citation_map or {},
        "confidence_score": float(artifact.confidence_score or 0.0),
        "identifiers": identifiers,
        "scope_key": getattr(artifact, "scope_key", None),
        "content_hash": getattr(artifact, "content_hash", None),
        "dedup_hash": getattr(artifact, "dedup_hash", None),
        "embedding_status": getattr(artifact, "embedding_status", None),
        "doc_id": str(artifact.document_id),
        "doc_code": idea_metadata.get("doc_code"),
        "doc_number": idea_metadata.get("doc_number"),
        "issued_date": idea_metadata.get("issued_date"),
        "issuing_org": idea_metadata.get("issuing_org"),
        "person_names": idea_metadata.get("person_names") or [],
        "assigned_units": idea_metadata.get("assigned_units") or [],
        "deadline": idea_metadata.get("deadline"),
    }


def _flatten_identifier_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            values.extend(_flatten_identifier_values(nested))
    elif isinstance(value, list | tuple | set):
        for item in value:
            values.extend(_flatten_identifier_values(item))
    elif value not in (None, ""):
        values.append(str(value))
    ordered: list[str] = []
    seen: set[str] = set()
    for item in values:
        clean = " ".join(item.split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return ordered
