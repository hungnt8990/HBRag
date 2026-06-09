from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

logger = logging.getLogger(__name__)
DEFAULT_DISTANCE = Distance.COSINE


@dataclass(frozen=True)
class VectorSearchResult:
    chunk_id: str
    document_id: str
    score: float
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorStoreCollectionInfo:
    collection_name: str
    exists: bool
    vector_size: int | None
    expected_vector_size: int
    distance: str | None
    expected_distance: str
    matches_config: bool
    recreated: bool


class QdrantVectorStore:
    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        vector_size: int,
        upsert_batch_size: int = 128,
    ) -> None:
        if upsert_batch_size <= 0:
            raise ValueError("upsert_batch_size must be greater than 0.")

        self._client = client
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.upsert_batch_size = upsert_batch_size
        self.distance = DEFAULT_DISTANCE

    async def ensure_collection(self) -> None:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if exists:
            return

        await self._create_collection()

    async def validate_collection_config(
        self,
        *,
        auto_recreate: bool = False,
    ) -> VectorStoreCollectionInfo:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            logger.info(
                "Qdrant collection does not exist; creating collection=%s vector_size=%s "
                "distance=%s",
                self.collection_name,
                self.vector_size,
                self.expected_distance,
            )
            await self._create_collection()
            return await self._get_collection_info(recreated=False)

        collection_info = await self._get_collection_info(recreated=False)
        logger.info(
            "Qdrant collection config: collection=%s vector_size=%s distance=%s",
            collection_info.collection_name,
            collection_info.vector_size,
            collection_info.distance,
        )

        if collection_info.vector_size == self.vector_size:
            return collection_info

        logger.warning(
            "Qdrant collection vector size mismatch: collection=%s actual_vector_size=%s "
            "expected_vector_size=%s",
            self.collection_name,
            collection_info.vector_size,
            self.vector_size,
        )
        if not auto_recreate:
            return collection_info

        logger.warning(
            "AUTO_RECREATE_COLLECTION is enabled; recreating Qdrant collection=%s",
            self.collection_name,
        )
        return await self.recreate_collection()

    async def recreate_collection(self) -> VectorStoreCollectionInfo:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if exists:
            await self._client.delete_collection(collection_name=self.collection_name)

        await self._create_collection()
        collection_info = await self._get_collection_info(recreated=True)
        logger.info(
            "Qdrant collection recreated: collection=%s vector_size=%s distance=%s",
            collection_info.collection_name,
            collection_info.vector_size,
            collection_info.distance,
        )
        return collection_info

    @property
    def expected_distance(self) -> str:
        return self._distance_to_string(self.distance)

    async def upsert_chunks(self, points: list[PointStruct]) -> None:
        if not points:
            return
        await self.ensure_collection()

        for batch in self._batched(points, self.upsert_batch_size):
            await self._client.upsert(collection_name=self.collection_name, points=batch)

    async def delete_points_for_document(self, document_id: UUID | str) -> None:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            return

        await self._client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=str(document_id)),
                        )
                    ]
                )
            ),
        )

    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        document_ids: set[str] | None = None,
        knowledge_base_ids: set[str] | None = None,
        organization_id: str | None = None,
        visibility: str | None = None,
    ) -> list[VectorSearchResult]:
        await self.ensure_collection()
        response = await self._client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            query_filter=self._payload_filter(
                document_ids=document_ids,
                knowledge_base_ids=knowledge_base_ids,
                organization_id=organization_id,
                visibility=visibility,
            ),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        points = getattr(response, "points", response)
        return [self._to_search_result(point) for point in points]

    @staticmethod
    def build_point(
        *,
        chunk_id: UUID,
        document_id: UUID,
        chunk_index: int,
        content: str,
        metadata: dict[str, Any],
        vector: list[float],
        organization_id: UUID | str | None = None,
        knowledge_base_id: UUID | str | None = None,
        uploaded_by_user_id: UUID | str | None = None,
        visibility: str | None = None,
    ) -> PointStruct:
        return PointStruct(
            id=str(chunk_id),
            vector=vector,
            payload={
                "chunk_id": str(chunk_id),
                "document_id": str(document_id),
                "chunk_index": chunk_index,
                "content": content,
                "metadata": metadata,
                "organization_id": str(organization_id) if organization_id else None,
                "knowledge_base_id": str(knowledge_base_id) if knowledge_base_id else None,
                "uploaded_by_user_id": (
                    str(uploaded_by_user_id) if uploaded_by_user_id else None
                ),
                "visibility": visibility,
            },
        )

    @staticmethod
    def _to_search_result(point: Any) -> VectorSearchResult:
        payload = point.payload or {}
        return VectorSearchResult(
            chunk_id=str(payload.get("chunk_id", point.id)),
            document_id=str(payload.get("document_id", "")),
            score=float(point.score),
            content=str(payload.get("content", "")),
            metadata=dict(payload.get("metadata") or {}),
        )

    @staticmethod
    def _payload_filter(
        *,
        document_ids: set[str] | None,
        knowledge_base_ids: set[str] | None,
        organization_id: str | None,
        visibility: str | None,
    ) -> Filter | None:
        must: list[FieldCondition] = []
        if document_ids is not None:
            must.append(
                FieldCondition(
                    key="document_id",
                    match=MatchAny(any=sorted(document_ids)),
                )
            )
        if knowledge_base_ids is not None:
            must.append(
                FieldCondition(
                    key="knowledge_base_id",
                    match=MatchAny(any=sorted(knowledge_base_ids)),
                )
            )
        if organization_id is not None:
            must.append(
                FieldCondition(
                    key="organization_id",
                    match=MatchValue(value=organization_id),
                )
            )
        if visibility is not None:
            must.append(
                FieldCondition(
                    key="visibility",
                    match=MatchValue(value=visibility),
                )
            )
        if not must:
            return None
        return Filter(must=must)

    @staticmethod
    def _batched(points: list[PointStruct], batch_size: int) -> list[list[PointStruct]]:
        return [points[index : index + batch_size] for index in range(0, len(points), batch_size)]

    async def _create_collection(self) -> None:
        await self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.vector_size, distance=self.distance),
        )

    async def _get_collection_info(self, *, recreated: bool) -> VectorStoreCollectionInfo:
        raw_info = await self._client.get_collection(collection_name=self.collection_name)
        vector_params = self._extract_vector_params(raw_info)
        vector_size = self._get_field(vector_params, "size")
        distance = self._get_field(vector_params, "distance")
        normalized_vector_size = vector_size if isinstance(vector_size, int) else None

        return VectorStoreCollectionInfo(
            collection_name=self.collection_name,
            exists=True,
            vector_size=normalized_vector_size,
            expected_vector_size=self.vector_size,
            distance=self._distance_to_string(distance) if distance is not None else None,
            expected_distance=self.expected_distance,
            matches_config=normalized_vector_size == self.vector_size,
            recreated=recreated,
        )

    @classmethod
    def _extract_vector_params(cls, collection_info: Any) -> Any:
        config = cls._get_field(collection_info, "config")
        params = cls._get_field(config, "params")
        vectors = cls._get_field(params, "vectors")
        if isinstance(vectors, dict):
            if "" in vectors:
                return vectors[""]
            if len(vectors) == 1:
                return next(iter(vectors.values()))
        return vectors

    @staticmethod
    def _get_field(value: Any, field_name: str) -> Any:
        if isinstance(value, dict):
            return value.get(field_name)
        return getattr(value, field_name, None)

    @staticmethod
    def _distance_to_string(distance: Any) -> str:
        return str(getattr(distance, "value", distance))


@lru_cache
def get_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore(
        client=AsyncQdrantClient(url=settings.qdrant_url),
        collection_name=settings.qdrant_collection_name,
        vector_size=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
    )
