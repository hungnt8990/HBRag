import asyncio
import logging
from types import SimpleNamespace
from uuid import UUID

from qdrant_client.models import Distance, PointStruct, SparseVector

from app.core.config import settings
from app.services.security.security_access_control import AccessFilter
from app.services.embeddings.embedding_sparse import SparseEmbedding
from app.services.vector.vector_store import QdrantVectorStore


class FakeQdrantClient:
    def __init__(
        self,
        *,
        collection_exists: bool = True,
        vector_size: int = 384,
        distance: Distance = Distance.COSINE,
        sparse_configured: bool = True,
        named_vectors: bool = True,
    ) -> None:
        self.upsert_batches: list[list[PointStruct]] = []
        self.exists = collection_exists
        self.vector_size = vector_size
        self.distance = distance
        self.sparse_configured = sparse_configured
        self.named_vectors = named_vectors
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[str] = []
        self.point_delete_calls: list[dict[str, object]] = []
        self.payload_indexes: list[str] = []
        self.query_calls: list[dict[str, object]] = []

    async def collection_exists(self, *, collection_name: str) -> bool:
        return self.exists

    async def create_collection(
        self,
        *,
        collection_name: str,
        vectors_config,
        sparse_vectors_config=None,
    ) -> None:
        dense = vectors_config["dense"]
        self.exists = True
        self.vector_size = dense.size
        self.distance = dense.distance
        self.sparse_configured = bool(sparse_vectors_config)
        self.create_calls.append(
            {
                "collection_name": collection_name,
                "vector_size": dense.size,
                "distance": dense.distance,
                "sparse": bool(sparse_vectors_config),
            }
        )

    async def create_payload_index(self, *, field_name: str, **_: object) -> None:
        self.payload_indexes.append(field_name)

    async def delete_collection(self, *, collection_name: str) -> None:
        self.exists = False
        self.delete_calls.append(collection_name)

    async def get_collection(self, *, collection_name: str):
        sparse_vectors = {"sparse": SimpleNamespace()} if self.sparse_configured else {}
        dense = SimpleNamespace(size=self.vector_size, distance=self.distance)
        vectors = {"dense": dense} if self.named_vectors else dense
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=vectors,
                    sparse_vectors=sparse_vectors,
                )
            )
        )

    async def upsert(
        self,
        *,
        collection_name: str,
        points: list[PointStruct],
        wait: bool,
    ) -> None:
        self.upsert_batches.append(points)

    async def delete(self, *, collection_name: str, points_selector, wait: bool) -> None:
        self.point_delete_calls.append(
            {"collection_name": collection_name, "points_selector": points_selector}
        )

    async def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="point-1",
                    score=0.9,
                    payload={
                        "chunk_id": "db-chunk-1",
                        "document_id": "doc-1",
                        "text": "Matched text",
                        "unit": "CPCIT",
                    },
                )
            ]
        )


def _store(client: FakeQdrantClient, *, vector_size: int = 2) -> QdrantVectorStore:
    return QdrantVectorStore(
        client=client,  # type: ignore[arg-type]
        collection_name="test_chunks",
        vector_size=vector_size,
        upsert_batch_size=2,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        sparse_enabled=True,
    )


def test_qdrant_vector_store_upserts_points_in_batches() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(vector_size=2)
        store = _store(client)
        points = [
            store.build_point(
                point_id=f"00000000-0000-0000-0000-00000000000{index}",
                payload={"chunk_id": f"chunk-{index}", "document_id": "doc"},
                vector=[0.1, 0.2],
                sparse_vector=SparseEmbedding(indices=[index], values=[1.0]),
            )
            for index in range(5)
        ]

        await store.upsert_chunks(points)

        assert [len(batch) for batch in client.upsert_batches] == [2, 2, 1]

    asyncio.run(run_test())


def test_qdrant_build_point_contains_named_dense_sparse_and_flat_payload() -> None:
    store = _store(FakeQdrantClient(vector_size=2))
    point = store.build_point(
        point_id="00000000-0000-0000-0000-000000000001",
        payload={
            "chunk_id": "db-chunk-id",
            "document_id": "doc-id",
            "text": "Chunk",
            "organization_id": "org-id",
        },
        vector=[0.1, 0.2],
        sparse_vector=SparseEmbedding(indices=[12], values=[0.8]),
    )

    assert point.payload["text"] == "Chunk"
    assert point.payload["organization_id"] == "org-id"
    assert point.vector["dense"] == [0.1, 0.2]
    assert isinstance(point.vector["sparse"], SparseVector)


def test_qdrant_vector_store_creates_missing_collection_with_named_vectors() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=False, sparse_configured=False)
        store = _store(client, vector_size=768)

        info = await store.validate_collection_config()

        assert info.matches_config is True
        assert info.vector_size == 768
        assert info.sparse_configured is True
        assert client.create_calls == [
            {
                "collection_name": "test_chunks",
                "vector_size": 768,
                "distance": Distance.COSINE,
                "sparse": True,
            }
        ]
        assert "document_id" in client.payload_indexes
        assert "table_name" in client.payload_indexes

    asyncio.run(run_test())


def test_qdrant_vector_store_warns_on_dimension_mismatch_without_recreate(caplog) -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=True, vector_size=384)
        store = _store(client, vector_size=768)

        with caplog.at_level(logging.WARNING):
            info = await store.validate_collection_config(auto_recreate=False)

        assert info.matches_config is False
        assert info.vector_size == 384
        assert info.expected_vector_size == 768
        assert info.recreated is False
        assert client.delete_calls == []
        assert "Qdrant collection config mismatch" in caplog.text

    asyncio.run(run_test())


def test_qdrant_vector_store_auto_recreates_on_dimension_mismatch() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=True, vector_size=384)
        store = _store(client, vector_size=768)

        info = await store.validate_collection_config(auto_recreate=True)

        assert info.matches_config is True
        assert info.vector_size == 768
        assert info.recreated is True
        assert client.delete_calls == ["test_chunks"]

    asyncio.run(run_test())


def test_qdrant_vector_store_deletes_points_for_document_and_tenant() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(collection_exists=True)
        store = _store(client, vector_size=384)

        await store.delete_points_for_document(
            UUID("11111111-1111-1111-1111-111111111111"),
            tenant_id="tenant-1",
        )

        conditions = client.point_delete_calls[0]["points_selector"].filter.must
        assert conditions[0].key == "document_id"
        assert conditions[1].key == "tenant_id"

    asyncio.run(run_test())


def test_qdrant_vector_store_uses_rrf_for_dense_sparse_search() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(vector_size=2)
        store = _store(client)

        results = await store.search(
            query_vector=[0.1, 0.2],
            sparse_query=SparseEmbedding(indices=[4], values=[1.0]),
            top_k=3,
            document_ids={"doc-1"},
            tenant_id="tenant-1",
        )

        call = client.query_calls[0]
        assert len(call["prefetch"]) == 2
        filter_keys = {condition.key for condition in call["prefetch"][0].filter.must}
        assert {"document_id", "tenant_id"} <= filter_keys
        assert results[0].chunk_id == "db-chunk-1"
        assert results[0].content == "Matched text"
        assert results[0].metadata["unit"] == "CPCIT"

    asyncio.run(run_test())


def test_qdrant_access_filter_keeps_legacy_payloads_searchable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "access_read_all_documents", False)

    async def run_test() -> None:
        client = FakeQdrantClient(vector_size=2)
        store = _store(client)

        await store.search(
            query_vector=[0.1, 0.2],
            top_k=3,
            organization_id="org-1",
            access_filter=AccessFilter(
                subject_user_id="user-1",
                organization_id="org-1",
                descendant_org_ids={"org-2"},
                org_path="/EVN/CPC/IT",
                role_names={"reader"},
                clearance_rank=1,
            ),
        )

        query_filter = client.query_calls[0]["query_filter"]
        dumped_filter = query_filter.model_dump(mode="json")

        assert "classification" in str(dumped_filter)
        assert "is_null" in str(dumped_filter)
        assert "scope" in str(dumped_filter)
        assert any(
            getattr(condition, "key", None) == "organization_id"
            for condition in query_filter.should
        )

    asyncio.run(run_test())

def test_qdrant_vector_store_rejects_legacy_unnamed_vector_collection() -> None:
    async def run_test() -> None:
        client = FakeQdrantClient(
            collection_exists=True,
            vector_size=2,
            named_vectors=False,
        )
        store = _store(client)

        info = await store.validate_collection_config(auto_recreate=False)

        assert info.matches_config is False
        assert info.vector_size is None

    asyncio.run(run_test())
