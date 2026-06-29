from __future__ import annotations

import asyncio
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
    Fusion,
    FusionQuery,
    IsNullCondition,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.core.config import settings
from app.services.security.security_access_control import AccessFilter
from app.services.embeddings.embedding_sparse import SparseEmbedding

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
    dense_vector_name: str = "dense"
    sparse_vector_name: str | None = None
    sparse_configured: bool = False


class QdrantVectorStore:
    """Qdrant repository for one-RAG-chunk-per-point storage.

    Each point uses a named dense vector and, when enabled, a named sparse
    vector. Payload is deliberately flat so filters and citations do not need
    to understand an internal nested metadata object.
    """

    PAYLOAD_KEYWORD_FIELDS = (
        "tenant_id",
        "organization_id",
        "knowledge_base_id",
        "document_id",
        "artifact_id",
        "item_type",
        "artifact_type",
        "context_type",
        "status",
        "source_chunk_ids",
        "document_version",
        "chunk_id",
        "semantic_chunk_id",
        "chunk_type",
        "content_format",
        "unit",
        "scope",
        "source_file",
        "quality_status",
        "table_name",
        "visibility",
        "owner_org_id",
        "owner_org_path",
        "classification",
        "business_domains",
        "project_codes",
        "allowed_org_ids",
        "allowed_org_paths",
        "allowed_role_names",
        "allowed_group_codes",
        "allowed_user_ids",
        "denied_org_ids",
        "denied_org_paths",
        "denied_role_names",
        "denied_group_codes",
        "denied_user_ids",
        "identifiers",
        "doc_codes",
        "dates",
        "platform",
        "feature_name",
        "screen_name",
        "change_content",
        "phase",
        "change_type",
        "content_type",
        "change_topic",
        "screen_names",
        "source_type",
        "source_name",
        "id_vb",
        "ky_hieu",
        "trich_yeu",
        "document_code",
        "doc_code",
        "noi_ban_hanh",
        "issuing_org",
        "ten_file",
        # ACL flatten (2 list keyword): allow ["dv_/pb_/nv_"] + deny ["pb_/nv_"].
        "acl_subjects",
        "acl_deny",
    )
    PAYLOAD_INTEGER_FIELDS = (
        "page_start",
        "page_end",
        "chunk_index",
        # ACL cũ (id nguyên) — giữ index cho dữ liệu chưa reindex; bản mới dùng
        # acl_subjects + acl_deny (keyword) ở trên.
        "acl_allow_dv",
        "acl_allow_pb",
        "acl_allow_nv",
        "acl_deny_pb",
        "acl_deny_nv",
    )

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        vector_size: int,
        upsert_batch_size: int = 128,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
        sparse_enabled: bool = True,
        upsert_retry_count: int = 2,
        hybrid_candidate_multiplier: int = 4,
        auto_recreate_collection: bool = False,
    ) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size must be greater than 0.")
        if upsert_batch_size <= 0:
            raise ValueError("upsert_batch_size must be greater than 0.")
        if upsert_retry_count < 0:
            raise ValueError("upsert_retry_count cannot be negative.")
        if hybrid_candidate_multiplier <= 0:
            raise ValueError("hybrid_candidate_multiplier must be greater than 0.")

        self._client = client
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.upsert_batch_size = upsert_batch_size
        self.dense_vector_name = dense_vector_name
        self.sparse_vector_name = sparse_vector_name
        self.sparse_enabled = sparse_enabled
        self.upsert_retry_count = upsert_retry_count
        self.hybrid_candidate_multiplier = hybrid_candidate_multiplier
        self.auto_recreate_collection = auto_recreate_collection
        self.distance = DEFAULT_DISTANCE
        self._payload_indexes_ready = False
        self._collection_validated = False

    async def ensure_collection(self) -> None:
        if not self._collection_validated:
            info = await self.validate_collection_config(
                auto_recreate=self.auto_recreate_collection,
            )
            if not info.matches_config:
                raise RuntimeError(
                    "Qdrant collection schema is incompatible with the configured "
                    f"named vectors: collection={self.collection_name!r}, "
                    f"dense={self.dense_vector_name!r}, sparse={self.sparse_enabled}. "
                    "Use a versioned collection name or explicitly enable controlled "
                    "collection recreation."
                )
            self._collection_validated = True
        if not self._payload_indexes_ready:
            await self._ensure_payload_indexes()
            self._payload_indexes_ready = True

    async def validate_collection_config(
        self,
        *,
        auto_recreate: bool = False,
    ) -> VectorStoreCollectionInfo:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            logger.info(
                "Creating Qdrant collection=%s dense_vector=%s vector_size=%s sparse=%s",
                self.collection_name,
                self.dense_vector_name,
                self.vector_size,
                self.sparse_enabled,
            )
            await self._create_collection()
            await self._ensure_payload_indexes()
            self._payload_indexes_ready = True
            return await self._get_collection_info(recreated=False)

        collection_info = await self._get_collection_info(recreated=False)
        if collection_info.matches_config:
            await self._ensure_payload_indexes()
            self._payload_indexes_ready = True
            return collection_info

        logger.warning(
            "Qdrant collection config mismatch: collection=%s actual_size=%s "
            "expected_size=%s sparse_configured=%s expected_sparse=%s",
            self.collection_name,
            collection_info.vector_size,
            self.vector_size,
            collection_info.sparse_configured,
            self.sparse_enabled,
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
        await self._ensure_payload_indexes()
        self._payload_indexes_ready = True
        self._collection_validated = True
        return await self._get_collection_info(recreated=True)

    @property
    def expected_distance(self) -> str:
        return self._distance_to_string(self.distance)

    async def upsert_chunks(self, points: list[PointStruct]) -> None:
        if not points:
            return
        await self.ensure_collection()

        for batch_index, batch in enumerate(self._batched(points, self.upsert_batch_size)):
            await self._upsert_batch_with_retry(batch=batch, batch_index=batch_index)

    async def delete_points_for_document(
        self,
        document_id: UUID | str,
        *,
        tenant_id: UUID | str | None = None,
    ) -> None:
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            return

        must = [
            FieldCondition(
                key="document_id",
                match=MatchValue(value=str(document_id)),
            )
        ]
        if tenant_id is not None:
            must.append(
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=str(tenant_id)),
                )
            )
        await self._client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(filter=Filter(must=must)),
            wait=True,
        )

    async def retrieve_payloads_for_document(
        self,
        document_id: UUID | str,
        *,
        tenant_id: UUID | str | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Lấy payload (metadata) của mọi point thuộc 1 văn bản (không kèm vector).

        Dùng cho UI "xem metadata Qdrant theo chunk": scroll theo ``document_id``,
        trả list payload — FE map sang chunk PG theo ``chunk_index``/``chunk_id``.
        """
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            return []
        must = [FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]
        if tenant_id is not None:
            must.append(FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id))))
        points, _next = await self._client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(must=must),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [{"point_id": str(point.id), **(dict(point.payload or {}))} for point in points]

    async def count_points_for_document(
        self,
        document_id: UUID | str,
        *,
        tenant_id: UUID | str | None = None,
    ) -> int:
        """Đếm số point của 1 văn bản (nhẹ — dùng cho UI hiển thị 'N point Qdrant')."""
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            return 0
        must = [FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]
        if tenant_id is not None:
            must.append(FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id))))
        result = await self._client.count(
            collection_name=self.collection_name,
            count_filter=Filter(must=must),
            exact=True,
        )
        return int(getattr(result, "count", 0) or 0)

    async def set_acl_payload_for_document(
        self,
        document_id: UUID | str,
        acl_payload: dict[str, object],
        *,
        tenant_id: UUID | str | None = None,
    ) -> None:
        """Cập nhật riêng các trường ACL cho toàn bộ point của một văn bản.

        Dùng cho re-compress: chỉ ghi đè payload (set_payload merge theo key),
        KHÔNG đụng vector nên không cần nhúng lại. Các key không nằm trong
        ``acl_payload`` được giữ nguyên.
        """
        if not acl_payload:
            return
        exists = await self._client.collection_exists(collection_name=self.collection_name)
        if not exists:
            return

        must = [FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]
        if tenant_id is not None:
            must.append(FieldCondition(key="tenant_id", match=MatchValue(value=str(tenant_id))))
        await self._client.set_payload(
            collection_name=self.collection_name,
            payload=dict(acl_payload),
            points=Filter(must=must),
            wait=True,
        )

    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        sparse_query: SparseEmbedding | None = None,
        document_ids: set[str] | None = None,
        knowledge_base_ids: set[str] | None = None,
        organization_id: str | None = None,
        tenant_id: str | None = None,
        visibility: str | None = None,
        unit: str | None = None,
        chunk_type: str | None = None,
        table_name: str | None = None,
        access_filter: AccessFilter | None = None,
        acl_subject: "AclSubject | None" = None,
    ) -> list[VectorSearchResult]:
        await self.ensure_collection()
        query_filter = self._payload_filter(
            document_ids=document_ids,
            knowledge_base_ids=knowledge_base_ids,
            organization_id=organization_id,
            tenant_id=tenant_id,
            visibility=visibility,
            unit=unit,
            chunk_type=chunk_type,
            table_name=table_name,
            access_filter=access_filter,
            acl_subject=acl_subject,
        )

        from qdrant_client.models import QuantizationSearchParams, SearchParams

        search_params = SearchParams(
            hnsw_ef=settings.qdrant_search_hnsw_ef,
            exact=False,
            quantization=(
                QuantizationSearchParams(
                    ignore=False,
                    rescore=settings.qdrant_quantization_rescore,
                    oversampling=settings.qdrant_quantization_oversampling,
                )
                if settings.qdrant_quantization_enabled
                else None
            ),
        )

        if self.sparse_enabled and sparse_query is not None and sparse_query.indices:
            candidate_limit = max(top_k, top_k * self.hybrid_candidate_multiplier)
            response = await self._client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    Prefetch(
                        query=query_vector,
                        using=self.dense_vector_name,
                        limit=candidate_limit,
                        filter=query_filter,
                        params=search_params,
                    ),
                    Prefetch(
                        query=SparseVector(
                            indices=sparse_query.indices,
                            values=sparse_query.values,
                        ),
                        using=self.sparse_vector_name,
                        limit=candidate_limit,
                        filter=query_filter,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=top_k,
                with_payload=True,
                with_vectors=False,
            )
        else:
            response = await self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                using=self.dense_vector_name,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
                search_params=search_params,
            )
        points = getattr(response, "points", response)
        return [self._to_search_result(point) for point in points]

    def build_point(
        self,
        *,
        vector: list[float],
        point_id: UUID | str | None = None,
        payload: dict[str, Any] | None = None,
        sparse_vector: SparseEmbedding | None = None,
        # Compatibility inputs for existing callers/tests. New ingestion should
        # pass point_id + payload instead.
        chunk_id: UUID | str | None = None,
        document_id: UUID | str | None = None,
        chunk_index: int | None = None,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
        organization_id: UUID | str | None = None,
        knowledge_base_id: UUID | str | None = None,
        uploaded_by_user_id: UUID | str | None = None,
        visibility: str | None = None,
    ) -> PointStruct:
        if len(vector) != self.vector_size:
            raise ValueError(
                f"Dense vector dimension mismatch: got {len(vector)}, expected {self.vector_size}."
            )
        resolved_point_id = point_id or chunk_id
        if resolved_point_id is None:
            raise ValueError("point_id is required.")

        if payload is None:
            if chunk_id is None or document_id is None or chunk_index is None:
                raise ValueError("chunk_id, document_id and chunk_index are required.")
            legacy_metadata = dict(metadata or {})
            payload = {
                "chunk_id": str(chunk_id),
                "semantic_chunk_id": str(chunk_id),
                "document_id": str(document_id),
                "chunk_index": chunk_index,
                "text": content or "",
                "content": content or "",
                "metadata": legacy_metadata,
                **legacy_metadata,
                "organization_id": str(organization_id) if organization_id else None,
                "knowledge_base_id": str(knowledge_base_id) if knowledge_base_id else None,
                "uploaded_by_user_id": (
                    str(uploaded_by_user_id) if uploaded_by_user_id else None
                ),
                "visibility": visibility,
            }

        vectors: dict[str, Any] = {self.dense_vector_name: vector}
        if self.sparse_enabled and sparse_vector is not None:
            vectors[self.sparse_vector_name] = SparseVector(
                indices=sparse_vector.indices,
                values=sparse_vector.values,
            )
        return PointStruct(id=str(resolved_point_id), vector=vectors, payload=payload)

    @staticmethod
    def _to_search_result(point: Any) -> VectorSearchResult:
        payload = dict(point.payload or {})
        content = str(payload.get("text") or payload.get("content") or "")
        metadata = dict(payload)
        metadata.pop("text", None)
        metadata.pop("content", None)
        # Preserve compatibility with old points while making canonical fields
        # available directly for citations and filtering.
        nested_metadata = metadata.pop("metadata", None)
        if isinstance(nested_metadata, dict):
            metadata = {**nested_metadata, **metadata}
        return VectorSearchResult(
            chunk_id=str(payload.get("chunk_id", point.id)),
            document_id=str(payload.get("document_id", "")),
            score=float(point.score),
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _payload_filter(
        *,
        document_ids: set[str] | None,
        knowledge_base_ids: set[str] | None,
        organization_id: str | None,
        tenant_id: str | None,
        visibility: str | None,
        unit: str | None = None,
        chunk_type: str | None = None,
        table_name: str | None = None,
        access_filter: AccessFilter | None = None,
        acl_subject: "AclSubject | None" = None,
    ) -> Filter | None:
        must: list[FieldCondition] = []
        must_not: list[FieldCondition] = []
        should: list[FieldCondition] = []
        if document_ids is not None:
            must.append(
                FieldCondition(key="document_id", match=MatchAny(any=sorted(document_ids)))
            )
        if knowledge_base_ids is not None:
            must.append(
                FieldCondition(
                    key="knowledge_base_id",
                    match=MatchAny(any=sorted(knowledge_base_ids)),
                )
            )
        for key, value in (
            ("organization_id", organization_id),
            ("tenant_id", tenant_id),
            ("visibility", visibility),
            ("unit", unit),
            ("chunk_type", chunk_type),
            ("table_name", table_name),
        ):
            if value is not None:
                must.append(FieldCondition(key=key, match=MatchValue(value=value)))
        if access_filter is not None and not settings.access_read_all_documents:
            allowed_classifications = [
                name
                for name, rank in settings.access_classification_rank.items()
                if rank <= access_filter.clearance_rank
            ]
            must.append(
                Filter(
                    should=[
                        FieldCondition(
                            key="classification",
                            match=MatchAny(any=allowed_classifications),
                        ),
                        IsNullCondition(is_null={"key": "classification"}),
                    ]
                )
            )
            org_ids = set(access_filter.descendant_org_ids)
            if access_filter.organization_id:
                org_ids.add(access_filter.organization_id)
            for key, values in (
                ("denied_user_ids", {access_filter.subject_user_id}),
                ("denied_org_ids", org_ids),
                ("denied_role_names", access_filter.role_names),
                ("denied_group_codes", access_filter.group_codes),
            ):
                if values:
                    must_not.append(
                        FieldCondition(key=key, match=MatchAny(any=sorted(values)))
                    )
            if org_ids:
                should.append(
                    FieldCondition(key="owner_org_id", match=MatchAny(any=sorted(org_ids)))
                )
                should.append(
                    FieldCondition(key="organization_id", match=MatchAny(any=sorted(org_ids)))
                )
                should.append(
                    FieldCondition(key="allowed_org_ids", match=MatchAny(any=sorted(org_ids)))
                )
            if access_filter.org_path:
                should.append(
                    FieldCondition(
                        key="allowed_org_paths",
                        match=MatchAny(any=[access_filter.org_path]),
                    )
                )
            should.append(
                FieldCondition(
                    key="scope",
                    match=MatchAny(any=sorted(settings.access_corp_wide_scopes)),
                )
            )
            should.append(IsNullCondition(is_null={"key": "scope"}))
            should.append(
                FieldCondition(
                    key="allowed_user_ids",
                    match=MatchAny(any=[access_filter.subject_user_id]),
                )
            )
            if access_filter.role_names:
                should.append(
                    FieldCondition(
                        key="allowed_role_names",
                        match=MatchAny(any=sorted(access_filter.role_names)),
                    )
                )
            if access_filter.group_codes:
                should.append(
                    FieldCondition(
                        key="allowed_group_codes",
                        match=MatchAny(any=sorted(access_filter.group_codes)),
                    )
                )
            if access_filter.business_domains:
                should.append(
                    FieldCondition(
                        key="business_domains",
                        match=MatchAny(any=sorted(access_filter.business_domains)),
                    )
                )
            if access_filter.project_codes:
                should.append(
                    FieldCondition(
                        key="project_codes",
                        match=MatchAny(any=sorted(access_filter.project_codes)),
                    )
                )
        # Hệ ACL danh mục (acl_*): enforce ĐỘC LẬP, bất kể access_read_all_documents.
        if acl_subject is not None:
            from app.services.security.security_acl_payload import (
                build_qdrant_acl_conditions_flat,
            )

            # Flat: 1 MatchAny trên acl_subjects (nhanh hơn 3 OR), + deny.
            acl_conditions = build_qdrant_acl_conditions_flat(acl_subject)
            if acl_conditions is not None:  # None = super admin -> không lọc
                acl_should, acl_must_not = acl_conditions
                must.append(Filter(should=acl_should))
                must_not.extend(acl_must_not)

        return Filter(must=must, must_not=must_not, should=should) if must or must_not or should else None

    @staticmethod
    def _batched(points: list[PointStruct], batch_size: int) -> list[list[PointStruct]]:
        return [points[index : index + batch_size] for index in range(0, len(points), batch_size)]

    async def _upsert_batch_with_retry(
        self,
        *,
        batch: list[PointStruct],
        batch_index: int,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(self.upsert_retry_count + 1):
            try:
                await self._client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                    wait=True,
                )
                return
            except Exception as exc:  # noqa: BLE001 - client exceptions vary by transport
                last_error = exc
                if attempt >= self.upsert_retry_count:
                    break
                delay = 0.25 * (2**attempt)
                logger.warning(
                    "Qdrant upsert retry collection=%s batch=%s attempt=%s delay=%s",
                    self.collection_name,
                    batch_index,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"Qdrant upsert failed for batch {batch_index} after "
            f"{self.upsert_retry_count + 1} attempt(s)."
        ) from last_error

    async def _create_collection(self) -> None:
        from qdrant_client.models import (
            HnswConfigDiff,
            OptimizersConfigDiff,
            ScalarQuantization,
            ScalarQuantizationConfig,
            ScalarType,
        )

        kwargs: dict[str, Any] = {
            "collection_name": self.collection_name,
            "vectors_config": {
                self.dense_vector_name: VectorParams(
                    size=self.vector_size,
                    distance=self.distance,
                    on_disk=settings.qdrant_vector_on_disk,
                )
            },
            "hnsw_config": HnswConfigDiff(
                m=settings.qdrant_hnsw_m,
                ef_construct=settings.qdrant_hnsw_ef_construct,
                on_disk=settings.qdrant_hnsw_on_disk,
            ),
            "optimizers_config": OptimizersConfigDiff(
                memmap_threshold=settings.qdrant_memmap_threshold,
            ),
            "shard_number": settings.qdrant_shard_number,
            "replication_factor": settings.qdrant_replication_factor,
        }
        if settings.qdrant_quantization_enabled:
            kwargs["quantization_config"] = ScalarQuantization(
                scalar=ScalarQuantizationConfig(
                    type=ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True,  # index quantized vẫn ở RAM để search nhanh
                )
            )
        if self.sparse_enabled:
            kwargs["sparse_vectors_config"] = {
                self.sparse_vector_name: SparseVectorParams()
            }
        await self._client.create_collection(**kwargs)

    async def _ensure_payload_indexes(self) -> None:
        for field_name in self.PAYLOAD_KEYWORD_FIELDS:
            await self._create_payload_index_if_needed(
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        for field_name in self.PAYLOAD_INTEGER_FIELDS:
            await self._create_payload_index_if_needed(
                field_name=field_name,
                field_schema=PayloadSchemaType.INTEGER,
            )

    async def _create_payload_index_if_needed(
        self,
        *,
        field_name: str,
        field_schema: PayloadSchemaType,
    ) -> None:
        creator = getattr(self._client, "create_payload_index", None)
        if creator is None:
            return
        try:
            await creator(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )
        except Exception as exc:  # Qdrant returns an error when an index already exists.
            message = str(exc).casefold()
            if "already exists" in message or "already indexed" in message:
                return
            raise

    async def _get_collection_info(self, *, recreated: bool) -> VectorStoreCollectionInfo:
        raw_info = await self._client.get_collection(collection_name=self.collection_name)
        vectors, sparse_vectors = self._extract_vector_configs(raw_info)
        dense_params = self._get_named_vector(vectors, self.dense_vector_name)
        vector_size = self._get_field(dense_params, "size")
        distance = self._get_field(dense_params, "distance")
        normalized_vector_size = vector_size if isinstance(vector_size, int) else None
        sparse_configured = bool(
            self._get_named_vector(sparse_vectors, self.sparse_vector_name)
        )
        normalized_distance = (
            self._distance_to_string(distance) if distance is not None else None
        )
        matches = (
            normalized_vector_size == self.vector_size
            and normalized_distance == self.expected_distance
            and (sparse_configured if self.sparse_enabled else True)
        )
        return VectorStoreCollectionInfo(
            collection_name=self.collection_name,
            exists=True,
            vector_size=normalized_vector_size,
            expected_vector_size=self.vector_size,
            distance=normalized_distance,
            expected_distance=self.expected_distance,
            matches_config=matches,
            recreated=recreated,
            dense_vector_name=self.dense_vector_name,
            sparse_vector_name=self.sparse_vector_name if self.sparse_enabled else None,
            sparse_configured=sparse_configured,
        )

    @classmethod
    def _extract_vector_configs(cls, collection_info: Any) -> tuple[Any, Any]:
        config = cls._get_field(collection_info, "config")
        params = cls._get_field(config, "params")
        return cls._get_field(params, "vectors"), cls._get_field(params, "sparse_vectors")

    @classmethod
    def _get_named_vector(cls, value: Any, name: str) -> Any:
        # The new collection contract requires named vectors. Treat an old
        # unnamed/single-vector collection as incompatible even when its
        # dimension happens to match; named-vector upserts would otherwise fail.
        if not isinstance(value, dict):
            return None
        return value.get(name)

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
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        collection_name=settings.qdrant_collection_name,
        vector_size=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_vector_name=settings.dense_vector_name,
        sparse_vector_name=settings.sparse_vector_name,
        sparse_enabled=settings.sparse_embedding_enabled,
        upsert_retry_count=settings.qdrant_upsert_retry_count,
        hybrid_candidate_multiplier=settings.qdrant_hybrid_candidate_multiplier,
        auto_recreate_collection=settings.auto_recreate_collection,
    )


@lru_cache
def get_artifact_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore(
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        collection_name=settings.qdrant_artifact_collection_name,
        vector_size=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_vector_name=settings.dense_vector_name,
        sparse_vector_name=settings.sparse_vector_name,
        sparse_enabled=settings.sparse_embedding_enabled,
        upsert_retry_count=settings.qdrant_upsert_retry_count,
        hybrid_candidate_multiplier=settings.qdrant_hybrid_candidate_multiplier,
        auto_recreate_collection=settings.auto_recreate_collection,
    )


def _doffice_vector_store(collection_name: str) -> QdrantVectorStore:
    """Qdrant store dense+sparse cho thiết kế DOffice 3-DB (chung tham số, khác tên).

    Cả Col chunks lẫn Col docmeta đều dùng dense (Qwen3-Embedding-8B, semantic) +
    sparse (keyword, mạnh cho ký hiệu) -> giữ được cả truy hồi ngữ nghĩa lẫn từ khóa.
    """
    return QdrantVectorStore(
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        collection_name=collection_name,
        vector_size=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_vector_name=settings.dense_vector_name,
        sparse_vector_name=settings.sparse_vector_name,
        sparse_enabled=settings.sparse_embedding_enabled,
        upsert_retry_count=settings.qdrant_upsert_retry_count,
        hybrid_candidate_multiplier=settings.qdrant_hybrid_candidate_multiplier,
        auto_recreate_collection=settings.auto_recreate_collection,
    )


@lru_cache
def get_doffice_chunks_vector_store() -> QdrantVectorStore:
    """Collection 1: vector từng chunk nội dung văn bản."""
    return _doffice_vector_store(settings.qdrant_chunks_collection_name)


@lru_cache
def get_doffice_docmeta_vector_store() -> QdrantVectorStore:
    """Collection 2: 1 point/văn bản — vector metadata (mọi trường trừ noi_dung)."""
    return _doffice_vector_store(settings.qdrant_docmeta_collection_name)
